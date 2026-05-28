import hashlib
import datetime
from io import BytesIO
import pandas as pd
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

# ReportLab imports
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# -------------------------------------------------------------
# 1. Encryption Service
# -------------------------------------------------------------
class EncryptionService:
    @staticmethod
    def get_fernet():
        key = getattr(settings, 'ENCRYPTION_KEY', None)
        if not key:
            # fallback for development if not configured
            dummy_key = Fernet.generate_key()
            return Fernet(dummy_key)
        # Enforce that key is in bytes
        if isinstance(key, str):
            key = key.encode()
        return Fernet(key)

    @classmethod
    def encrypt(cls, text: str) -> str:
        if not text:
            return None
        try:
            f = cls.get_fernet()
            return f.encrypt(text.strip().encode()).decode()
        except Exception:
            return None

    @classmethod
    def decrypt(cls, encrypted_text: str) -> str:
        if not encrypted_text:
            return None
        try:
            f = cls.get_fernet()
            return f.decrypt(encrypted_text.encode()).decode()
        except Exception:
            return "Decryption Error"

def get_hash(text: str) -> str:
    """
    Generates a deterministic SHA-256 hash of a string.
    Used for exact lookup match and unique constraints on encrypted fields.
    """
    if not text:
        return None
    return hashlib.sha256(text.strip().upper().encode()).hexdigest()


# -------------------------------------------------------------
# 2. Bulk Excel Import / Export Service
# -------------------------------------------------------------
def import_citizens_from_excel(file_file, subdivision, user):
    """
    Parses and imports citizens from an Excel or CSV file.
    Performs batch validation and guarantees transaction rollback on errors.
    """
    from registry.models import Citizen, AuditLog  # local imports to avoid cycle

    try:
        if file_file.name.endswith('.csv'):
            df = pd.read_csv(file_file)
        else:
            df = pd.read_excel(file_file)
    except Exception as e:
        raise ValidationError([f"Invalid file format: {str(e)}"])

    # Required column checks
    required_cols = ['first_name', 'last_name', 'dob', 'gender', 'national_id']
    for col in required_cols:
        if col not in df.columns:
            raise ValidationError([f"Missing required column: '{col}' in upload sheet."])

    # Ensure optional columns exist
    if 'father_national_id' not in df.columns:
        df['father_national_id'] = None
    if 'mother_national_id' not in df.columns:
        df['mother_national_id'] = None

    errors = []
    citizens_to_create = []
    nic_hashes_in_batch = set()

    for idx, row in df.iterrows():
        row_num = idx + 2  # Row 1 is header
        
        first_name = str(row['first_name']).strip() if pd.notna(row['first_name']) else None
        last_name = str(row['last_name']).strip() if pd.notna(row['last_name']) else None
        
        # Date validation
        dob_raw = row['dob']
        dob = None
        if pd.notna(dob_raw):
            if isinstance(dob_raw, (datetime.date, datetime.datetime)):
                dob = dob_raw.date() if isinstance(dob_raw, datetime.datetime) else dob_raw
            else:
                try:
                    dob = pd.to_datetime(dob_raw).date()
                except Exception:
                    errors.append(f"Row {row_num}: Invalid date of birth '{dob_raw}'. Use YYYY-MM-DD.")
                    continue
        else:
            errors.append(f"Row {row_num}: Date of birth is required.")
            continue

        # Gender validation
        gender = str(row['gender']).strip().upper() if pd.notna(row['gender']) else None
        if gender not in ['M', 'F', 'MALE', 'FEMALE']:
            errors.append(f"Row {row_num}: Gender must be M or F.")
            continue
        gender = 'M' if gender in ['M', 'MALE'] else 'F'

        # Age calculation
        today = datetime.date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        # National ID validations
        nic = str(row['national_id']).strip() if pd.notna(row['national_id']) else None
        # Clean up floating point string parse like '123456789.0' or 'nan'
        if nic:
            if nic.endswith('.0'):
                nic = nic[:-2]
            if nic == 'nan' or nic == '':
                nic = None

        if age >= 18 and not nic:
            errors.append(f"Row {row_num}: National ID is required for adults (age {age} >= 18).")
            continue
        elif age < 18 and nic:
            errors.append(f"Row {row_num}: Under-18 citizens cannot have a National ID.")
            continue

        if nic:
            if len(nic) < 9 or len(nic) > 20:
                errors.append(f"Row {row_num}: National ID must be between 9 and 20 characters.")
                continue

            nic_hash = get_hash(nic)
            # Check unique in DB
            if Citizen.objects.filter(national_id_hash=nic_hash).exists():
                errors.append(f"Row {row_num}: Citizen with National ID '{nic}' already exists in database.")
                continue

            # Check unique in Batch
            if nic_hash in nic_hashes_in_batch:
                errors.append(f"Row {row_num}: Duplicate National ID '{nic}' in upload batch.")
                continue
            nic_hashes_in_batch.add(nic_hash)

        # Parent ID lookups
        father_nic = str(row['father_national_id']).strip() if pd.notna(row['father_national_id']) else None
        if father_nic and father_nic.endswith('.0'):
            father_nic = father_nic[:-2]
        if father_nic == 'nan' or father_nic == '':
            father_nic = None
            
        mother_nic = str(row['mother_national_id']).strip() if pd.notna(row['mother_national_id']) else None
        if mother_nic and mother_nic.endswith('.0'):
            mother_nic = mother_nic[:-2]
        if mother_nic == 'nan' or mother_nic == '':
            mother_nic = None

        father = None
        mother = None

        if father_nic:
            father = Citizen.objects.filter(national_id_hash=get_hash(father_nic)).first()
            if not father:
                errors.append(f"Row {row_num}: Father with NIC '{father_nic}' not found in registry.")
                continue

        if mother_nic:
            mother = Citizen.objects.filter(national_id_hash=get_hash(mother_nic)).first()
            if not mother:
                errors.append(f"Row {row_num}: Mother with NIC '{mother_nic}' not found in registry.")
                continue

        citizen = Citizen(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            gender=gender,
            national_id=nic,
            father=father,
            mother=mother,
            subdivision=subdivision,
            current_status=Citizen.Status.ALIVE
        )
        citizens_to_create.append(citizen)

    if errors:
        raise ValidationError(errors)

    # Perform atomic transaction
    with transaction.atomic():
        for citizen in citizens_to_create:
            citizen.save()
            AuditLog.objects.create(
                user=user,
                action=AuditLog.Action.IMPORT,
                model_name="Citizen",
                record_id=str(citizen.id),
                details=f"Bulk imported citizen: {citizen.get_full_name()} in Subdivision {subdivision.name}."
            )
    return len(citizens_to_create)

def generate_excel_template():
    """
    Generates a sample spreadsheet in-memory for download.
    """
    data = {
        'first_name': ['Pierre', 'Chantal', ''],
        'last_name': ['Ngué', 'Mbié', 'Unnamed Child'],
        'dob': ['1990-04-15', '1995-10-24', '2026-05-10'],
        'gender': ['M', 'F', 'M'],
        'national_id': ['112233445', '998877665', ''],
        'father_national_id': ['', '', '112233445'],
        'mother_national_id': ['', '', '998877665']
    }
    df = pd.DataFrame(data)
    buffer = BytesIO()
    # Write as excel
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Citizens Template')
    buffer.seek(0)
    return buffer


# -------------------------------------------------------------
# 3. PDF Generation Service with Custom Borders
# -------------------------------------------------------------
class CertificateCanvas(canvas.Canvas):
    """
    Draws Cameroon flag themed double borders on pages.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_border()
            super().showPage()
        super().save()

    def draw_border(self):
        self.saveState()
        # Emerald green outer border
        self.setStrokeColor(colors.HexColor('#0B6623'))
        self.setLineWidth(4)
        self.rect(20, 20, 572, 752)
        
        # Gold inner border
        self.setStrokeColor(colors.HexColor('#D4AF37'))
        self.setLineWidth(1.5)
        self.rect(25, 25, 562, 742)
        self.restoreState()


def generate_birth_certificate_pdf(declaration):
    """
    Generates a bilingual Birth Certificate PDF in-memory.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CertTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0B6623'),
        alignment=1,  # Center
        spaceAfter=5
    )
    
    subtitle_style = ParagraphStyle(
        'CertSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-BoldOblique',
        fontSize=14,
        leading=16,
        textColor=colors.HexColor('#4A4A4A'),
        alignment=1,
        spaceAfter=15
    )
    
    header_left = ParagraphStyle(
        'HeaderLeft',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        alignment=0
    )
    
    header_right = ParagraphStyle(
        'HeaderRight',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        alignment=2
    )
    
    body_style = ParagraphStyle(
        'CertBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        spaceAfter=6
    )
    
    label_style = ParagraphStyle(
        'CertLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15
    )

    story = []
    
    # 1. Cameroon Header Table
    header_data = [
        [
            Paragraph("REPUBLIC OF CAMEROON<br/>Peace - Work - Fatherland<br/>--------", header_left),
            Paragraph("REPUBLIQUE DU CAMEROUN<br/>Paix - Travail - Patrie<br/>--------", header_right)
        ],
        [
            Paragraph(f"MINISTRY OF DECENTRALIZATION<br/>AND LOCAL DEVELOPMENT<br/><b>SUBDIVISION: {declaration.subdivision.name.upper()}</b>", header_left),
            Paragraph(f"MINISTERE DE LA DECENTRALISATION<br/>ET DU DEVELOPPEMENT LOCAL<br/><b>COMMUNE DE {declaration.subdivision.name.upper()}</b>", header_right)
        ]
    ]
    
    header_table = Table(header_data, colWidths=[260, 260])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # 2. Titles
    story.append(Paragraph("BIRTH CERTIFICATE", title_style))
    story.append(Paragraph("ACTE DE NAISSANCE", subtitle_style))
    story.append(Spacer(1, 10))
    
    # 3. Details
    child_name = f"{declaration.child_first_name or ''} {declaration.child_last_name or ''}".strip()
    if not child_name:
        child_name = "Unnamed Child (Names Undecided / Non Déterminé)"
        
    gender_label = "Male / Masculin" if declaration.child_gender == 'M' else "Female / Féminin"
    
    details_data = [
        [Paragraph("Registration No. / N° Acte:", label_style), Paragraph(declaration.declaration_number, body_style)],
        [Paragraph("Child's Full Name / Nom de l'Enfant:", label_style), Paragraph(child_name, body_style)],
        [Paragraph("Date of Birth / Date de Naissance:", label_style), Paragraph(declaration.child_dob.strftime('%B %d, %Y'), body_style)],
        [Paragraph("Gender / Sexe:", label_style), Paragraph(gender_label, body_style)],
        [Paragraph("Place of Birth / Lieu de Naissance:", label_style), Paragraph(declaration.place_of_birth, body_style)],
        [Paragraph("Father's Name / Nom du Père:", label_style), Paragraph(declaration.father_name or "N/A", body_style)],
        [Paragraph("Father's NIC / CNI du Père:", label_style), Paragraph(declaration.father_national_id or "N/A", body_style)],
        [Paragraph("Mother's Name / Nom de la Mère:", label_style), Paragraph(declaration.mother_name, body_style)],
        [Paragraph("Mother's NIC / CNI de la Mère:", label_style), Paragraph(declaration.mother_national_id, body_style)],
        [Paragraph("Date of Approval / Date de Validation:", label_style), Paragraph(declaration.confirmed_at.strftime('%B %d, %Y') if declaration.confirmed_at else 'N/A', body_style)],
        [Paragraph("Reviewing Officer / Officier Civil:", label_style), Paragraph(declaration.reviewed_by.get_full_name() if declaration.reviewed_by else 'N/A', body_style)],
    ]
    
    details_table = Table(details_data, colWidths=[210, 310])
    details_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.lightgrey),
    ]))
    story.append(details_table)
    story.append(Spacer(1, 20))
    
    # 4. Signatures
    sig_data = [
        [
            Paragraph("Reviewing Officer Signature & Stamp<br/>Signature et Sceau de l'Officier", header_left),
            Paragraph("Subdivision Municipal Seal<br/>Sceau Municipal de la Commune", header_right)
        ],
        [
            Spacer(1, 40),
            Spacer(1, 40)
        ]
    ]
    sig_table = Table(sig_data, colWidths=[260, 260])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(sig_table)
    
    doc.build(story, canvasmaker=CertificateCanvas)
    buffer.seek(0)
    return buffer


def generate_death_certificate_pdf(declaration):
    """
    Generates a bilingual Death Certificate PDF in-memory.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CertTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#B22222'),  # Firebrick Red for death declarations
        alignment=1,
        spaceAfter=5
    )
    
    subtitle_style = ParagraphStyle(
        'CertSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-BoldOblique',
        fontSize=14,
        leading=16,
        textColor=colors.HexColor('#4A4A4A'),
        alignment=1,
        spaceAfter=15
    )
    
    header_left = ParagraphStyle(
        'HeaderLeft',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        alignment=0
    )
    
    header_right = ParagraphStyle(
        'HeaderRight',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        alignment=2
    )
    
    body_style = ParagraphStyle(
        'CertBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        spaceAfter=6
    )
    
    label_style = ParagraphStyle(
        'CertLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15
    )

    story = []
    
    # 1. Cameroon Header Table
    header_data = [
        [
            Paragraph("REPUBLIC OF CAMEROON<br/>Peace - Work - Fatherland<br/>--------", header_left),
            Paragraph("REPUBLIQUE DU CAMEROUN<br/>Paix - Travail - Patrie<br/>--------", header_right)
        ],
        [
            Paragraph(f"MINISTRY OF DECENTRALIZATION<br/>AND LOCAL DEVELOPMENT<br/><b>SUBDIVISION: {declaration.subdivision.name.upper()}</b>", header_left),
            Paragraph(f"MINISTERE DE LA DECENTRALISATION<br/>ET DU DEVELOPPEMENT LOCAL<br/><b>COMMUNE DE {declaration.subdivision.name.upper()}</b>", header_right)
        ]
    ]
    
    header_table = Table(header_data, colWidths=[260, 260])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # 2. Titles
    story.append(Paragraph("DEATH CERTIFICATE", title_style))
    story.append(Paragraph("ACTE DE DECES", subtitle_style))
    story.append(Spacer(1, 10))
    
    # 3. Details
    deceased = declaration.citizen
    gender_label = "Male / Masculin" if deceased.gender == 'M' else "Female / Féminin"
    nic_label = deceased.national_id if deceased.national_id else "Under-18 / Pas de CNI"
    
    details_data = [
        [Paragraph("Registration No. / N° Acte:", label_style), Paragraph(declaration.declaration_number, body_style)],
        [Paragraph("Deceased's Full Name / Nom du Défunt:", label_style), Paragraph(deceased.get_full_name(), body_style)],
        [Paragraph("National ID / CNI du Défunt:", label_style), Paragraph(nic_label, body_style)],
        [Paragraph("Gender / Sexe:", label_style), Paragraph(gender_label, body_style)],
        [Paragraph("Date of Death / Date du Décès:", label_style), Paragraph(declaration.death_date.strftime('%B %d, %Y'), body_style)],
        [Paragraph("Place of Death / Lieu du Décès:", label_style), Paragraph(declaration.place_of_death, body_style)],
        [Paragraph("Cause of Death / Cause du Décès:", label_style), Paragraph(declaration.death_cause, body_style)],
        [Paragraph("Declarant / Déclarant:", label_style), Paragraph(f"{declaration.declarant_name} ({declaration.declarant_relation})", body_style)],
        [Paragraph("Date of Confirmation / Date de Validation:", label_style), Paragraph(declaration.confirmed_at.strftime('%B %d, %Y') if declaration.confirmed_at else 'N/A', body_style)],
        [Paragraph("Reviewing Officer / Officier Civil:", label_style), Paragraph(declaration.reviewed_by.get_full_name() if declaration.reviewed_by else 'N/A', body_style)],
    ]
    
    details_table = Table(details_data, colWidths=[210, 310])
    details_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.lightgrey),
    ]))
    story.append(details_table)
    story.append(Spacer(1, 20))
    
    # 4. Signatures
    sig_data = [
        [
            Paragraph("Reviewing Officer Signature & Stamp<br/>Signature et Sceau de l'Officier", header_left),
            Paragraph("Subdivision Municipal Seal<br/>Sceau Municipal de la Commune", header_right)
        ],
        [
            Spacer(1, 40),
            Spacer(1, 40)
        ]
    ]
    sig_table = Table(sig_data, colWidths=[260, 260])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(sig_table)
    
    doc.build(story, canvasmaker=CertificateCanvas)
    buffer.seek(0)
    return buffer
