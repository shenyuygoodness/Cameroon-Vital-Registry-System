import os
import re
import base64
import hashlib
import hmac
import datetime
import logging
from io import BytesIO
import pandas as pd
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# ReportLab imports
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# -------------------------------------------------------------
# 1. Encryption Service  (AES-256-GCM; v1 Fernet legacy read)
# -------------------------------------------------------------
_AES_VERSION_PREFIX = 'v2:'

class EncryptionService:
    """
    Encrypts with AES-256-GCM (NIST SP 800-38D).
    Transparently decrypts legacy Fernet (AES-128-CBC) ciphertext so that
    existing database rows keep working until re-encrypted via
    `python manage.py reencrypt_data`.

    Key derivation: ENCRYPTION_KEY must be a URL-safe base64 value that
    decodes to ≥ 32 bytes.  A Fernet key (44 chars) satisfies this exactly —
    the same env variable works without any .env changes.
    """

    @staticmethod
    def _get_aes256_key() -> bytes:
        raw = getattr(settings, 'ENCRYPTION_KEY', None)
        if not raw:
            raise ImproperlyConfigured(
                "ENCRYPTION_KEY is not set. Generate one with:\n"
                "python -c \"import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
            )
        if isinstance(raw, str):
            raw = raw.encode()
        try:
            key_bytes = base64.urlsafe_b64decode(raw + b'==')
        except Exception:
            raise ImproperlyConfigured("ENCRYPTION_KEY must be URL-safe base64 encoded.")
        if len(key_bytes) < 32:
            raise ImproperlyConfigured(
                "ENCRYPTION_KEY must decode to at least 32 bytes for AES-256. "
                "Regenerate with: python -c \"import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
            )
        return key_bytes[:32]

    @classmethod
    def encrypt(cls, text: str) -> str:
        if not text:
            return None
        try:
            key = cls._get_aes256_key()
            aesgcm = AESGCM(key)
            nonce = os.urandom(12)           # 96-bit nonce — unique per operation
            ciphertext = aesgcm.encrypt(nonce, text.strip().encode('utf-8'), None)
            payload = base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')
            return _AES_VERSION_PREFIX + payload
        except Exception as e:
            logger.critical("ENCRYPTION FAILURE — data could not be encrypted: %s", e)
            raise RuntimeError("Encryption failed. Check ENCRYPTION_KEY configuration.") from e

    @classmethod
    def decrypt(cls, encrypted_text: str) -> str:
        if not encrypted_text:
            return None
        try:
            if encrypted_text.startswith(_AES_VERSION_PREFIX):
                # New AES-256-GCM path
                payload = base64.urlsafe_b64decode(
                    encrypted_text[len(_AES_VERSION_PREFIX):]
                )
                nonce, ciphertext = payload[:12], payload[12:]
                key = cls._get_aes256_key()
                return AESGCM(key).decrypt(nonce, ciphertext, None).decode('utf-8')
            else:
                # Legacy Fernet (AES-128-CBC-HMAC) path — read-only until re-encrypted
                raw = getattr(settings, 'ENCRYPTION_KEY', '').encode()
                return Fernet(raw).decrypt(encrypted_text.encode()).decode()
        except InvalidToken:
            logger.critical(
                "DECRYPTION FAILURE — InvalidToken. The ENCRYPTION_KEY may have "
                "been rotated without re-encrypting existing data, or the data is corrupt."
            )
            raise RuntimeError("Decryption failed. The encryption key may be incorrect.") from None
        except Exception as e:
            logger.critical("DECRYPTION FAILURE — unexpected error: %s", e)
            raise RuntimeError("Decryption failed unexpectedly.") from e

def get_hash(text: str) -> str:
    """
    Generates a keyed HMAC-SHA256 digest of a string.
    Using ENCRYPTION_KEY as the HMAC secret prevents rainbow-table attacks
    against the hash column even if the database is read by an attacker.
    """
    if not text:
        return None
    key = getattr(settings, 'ENCRYPTION_KEY', None)
    if not key:
        raise ImproperlyConfigured("ENCRYPTION_KEY is not set — cannot generate secure NIC hash.")
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, text.strip().upper().encode(), hashlib.sha256).hexdigest()


# Matches alphanumeric strings of 9–20 chars that look like NIDs.
# Used as a last-resort scrubber — callers should never pass raw NIDs to
# AuditLog in the first place.
_NID_PATTERN = re.compile(r'\b[A-Z0-9]{9,20}\b')

def sanitize_audit_details(text: str) -> str:
    """Strip NID-shaped tokens from audit log detail strings before writing."""
    if not text:
        return text
    return _NID_PATTERN.sub('[REDACTED]', text)


def mask_nid(nid: str) -> str:
    """Return a NID with all but the last 4 digits replaced by asterisks."""
    if not nid:
        return 'N/A'
    visible = min(4, len(nid))
    return '*' * (len(nid) - visible) + nid[-visible:]


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
        [Paragraph("Father's NIC / CNI du Père:", label_style), Paragraph(mask_nid(declaration.father_national_id), body_style)],
        [Paragraph("Mother's Name / Nom de la Mère:", label_style), Paragraph(declaration.mother_name, body_style)],
        [Paragraph("Mother's NIC / CNI de la Mère:", label_style), Paragraph(mask_nid(declaration.mother_national_id), body_style)],
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


def generate_marriage_certificate_pdf(registration):
    """
    Generates a bilingual Marriage Certificate PDF in-memory.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CertTitle', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=20, leading=24,
        textColor=colors.HexColor('#8B6914'),
        alignment=1, spaceAfter=5,
    )
    subtitle_style = ParagraphStyle(
        'CertSubtitle', parent=styles['Normal'],
        fontName='Helvetica-BoldOblique', fontSize=14, leading=16,
        textColor=colors.HexColor('#4A4A4A'), alignment=1, spaceAfter=15,
    )
    header_left = ParagraphStyle(
        'HeaderLeft', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=0,
    )
    header_right = ParagraphStyle(
        'HeaderRight', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=2,
    )
    body_style = ParagraphStyle(
        'CertBody', parent=styles['Normal'],
        fontName='Helvetica', fontSize=11, leading=15, spaceAfter=6,
    )
    label_style = ParagraphStyle(
        'CertLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=11, leading=15,
    )
    section_style = ParagraphStyle(
        'CertSection', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=12, leading=14,
        textColor=colors.HexColor('#8B6914'), spaceAfter=4, spaceBefore=10,
    )

    story = []

    # Cameroon bilingual header
    header_data = [
        [
            Paragraph("REPUBLIC OF CAMEROON<br/>Peace - Work - Fatherland<br/>--------", header_left),
            Paragraph("REPUBLIQUE DU CAMEROUN<br/>Paix - Travail - Patrie<br/>--------", header_right),
        ],
        [
            Paragraph(
                f"MINISTRY OF DECENTRALIZATION<br/>AND LOCAL DEVELOPMENT<br/>"
                f"<b>SUBDIVISION: {registration.subdivision.name.upper()}</b>", header_left,
            ),
            Paragraph(
                f"MINISTERE DE LA DECENTRALISATION<br/>ET DU DEVELOPPEMENT LOCAL<br/>"
                f"<b>COMMUNE DE {registration.subdivision.name.upper()}</b>", header_right,
            ),
        ],
    ]
    header_table = Table(header_data, colWidths=[260, 260])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))

    story.append(Paragraph("MARRIAGE CERTIFICATE", title_style))
    story.append(Paragraph("ACTE DE MARIAGE", subtitle_style))
    story.append(Spacer(1, 10))

    meta_data = [
        [Paragraph("Registration No. / N° Acte:", label_style), Paragraph(registration.declaration_number, body_style)],
        [Paragraph("Date of Marriage / Date de Mariage:", label_style), Paragraph(registration.date_of_marriage.strftime('%B %d, %Y'), body_style)],
        [Paragraph("Place / Lieu:", label_style), Paragraph(registration.place_of_marriage, body_style)],
        [Paragraph("Type of Marriage / Type de Mariage:", label_style), Paragraph(registration.get_marriage_type_display(), body_style)],
    ]
    meta_table = Table(meta_data, colWidths=[210, 310])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    story.append(meta_table)

    story.append(Paragraph("GROOM / ÉPOUX", section_style))
    husband_data = [
        [Paragraph("Full Name / Nom Complet:", label_style), Paragraph(f"{registration.husband_first_name} {registration.husband_last_name}", body_style)],
        [Paragraph("Date of Birth / Date de Naissance:", label_style), Paragraph(registration.husband_dob.strftime('%B %d, %Y'), body_style)],
        [Paragraph("National ID / CNI:", label_style), Paragraph(mask_nid(registration.husband_national_id) if registration.husband_national_id else 'N/A', body_style)],
    ]
    husband_table = Table(husband_data, colWidths=[210, 310])
    husband_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    story.append(husband_table)

    story.append(Paragraph("BRIDE / ÉPOUSE", section_style))
    wife_data = [
        [Paragraph("Full Name / Nom Complet:", label_style), Paragraph(f"{registration.wife_first_name} {registration.wife_last_name}", body_style)],
        [Paragraph("Date of Birth / Date de Naissance:", label_style), Paragraph(registration.wife_dob.strftime('%B %d, %Y'), body_style)],
        [Paragraph("National ID / CNI:", label_style), Paragraph(mask_nid(registration.wife_national_id) if registration.wife_national_id else 'N/A', body_style)],
    ]
    wife_table = Table(wife_data, colWidths=[210, 310])
    wife_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    story.append(wife_table)

    story.append(Paragraph("WITNESSES / TÉMOINS & APPROVAL", section_style))
    witness_data = [
        [Paragraph("Witness 1 / Témoin 1:", label_style), Paragraph(registration.witness_1_name, body_style)],
        [Paragraph("Witness 2 / Témoin 2:", label_style), Paragraph(registration.witness_2_name, body_style)],
        [Paragraph("Date of Approval / Date de Validation:", label_style), Paragraph(registration.confirmed_at.strftime('%B %d, %Y') if registration.confirmed_at else 'N/A', body_style)],
        [Paragraph("Registering Officer / Officier d'État Civil:", label_style), Paragraph(registration.reviewed_by.get_full_name() if registration.reviewed_by else 'N/A', body_style)],
    ]
    witness_table = Table(witness_data, colWidths=[210, 310])
    witness_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    story.append(witness_table)
    story.append(Spacer(1, 20))

    sig_data = [
        [
            Paragraph("Registering Officer Signature & Stamp<br/>Signature et Sceau de l'Officier", header_left),
            Paragraph("Subdivision Municipal Seal<br/>Sceau Municipal de la Commune", header_right),
        ],
        [Spacer(1, 40), Spacer(1, 40)],
    ]
    sig_table = Table(sig_data, colWidths=[260, 260])
    sig_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
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
    nic_label = mask_nid(deceased.national_id) if deceased.national_id else "Under-18 / Pas de CNI"
    
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


def generate_divorce_certificate_pdf(declaration):
    """
    Generates a bilingual Divorce Certificate PDF in-memory.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40,
    )

    styles = getSampleStyleSheet()

    PURPLE = colors.HexColor('#5B2C6F')

    title_style = ParagraphStyle(
        'DivTitle', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=20, leading=24,
        textColor=PURPLE, alignment=1, spaceAfter=5,
    )
    subtitle_style = ParagraphStyle(
        'DivSubtitle', parent=styles['Normal'],
        fontName='Helvetica-BoldOblique', fontSize=14, leading=16,
        textColor=colors.HexColor('#4A4A4A'), alignment=1, spaceAfter=15,
    )
    header_left = ParagraphStyle(
        'DivHeaderLeft', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=0,
    )
    header_right = ParagraphStyle(
        'DivHeaderRight', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=2,
    )
    body_style = ParagraphStyle(
        'DivBody', parent=styles['Normal'],
        fontName='Helvetica', fontSize=11, leading=15, spaceAfter=6,
    )
    label_style = ParagraphStyle(
        'DivLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=11, leading=15,
    )
    section_style = ParagraphStyle(
        'DivSection', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=12, leading=14,
        textColor=PURPLE, spaceAfter=4, spaceBefore=10,
    )

    story = []

    header_data = [
        [
            Paragraph("REPUBLIC OF CAMEROON<br/>Peace - Work - Fatherland<br/>--------", header_left),
            Paragraph("REPUBLIQUE DU CAMEROUN<br/>Paix - Travail - Patrie<br/>--------", header_right),
        ],
        [
            Paragraph(
                f"MINISTRY OF DECENTRALIZATION<br/>AND LOCAL DEVELOPMENT<br/>"
                f"<b>SUBDIVISION: {declaration.subdivision.name.upper()}</b>", header_left,
            ),
            Paragraph(
                f"MINISTÈRE DE LA DÉCENTRALISATION<br/>ET DU DÉVELOPPEMENT LOCAL<br/>"
                f"<b>SUBDIVISION: {declaration.subdivision.name.upper()}</b>", header_right,
            ),
        ],
    ]
    header_table = Table(header_data, colWidths=[260, 260])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("CERTIFICATE OF DIVORCE", title_style))
    story.append(Paragraph("ACTE DE DIVORCE", subtitle_style))
    story.append(Spacer(1, 8))

    meta_data = [
        [Paragraph("<b>Declaration No. / N° Acte:</b>", label_style), Paragraph(declaration.declaration_number, body_style)],
        [Paragraph("<b>Date Issued / Date d'émission:</b>", label_style), Paragraph(str(declaration.confirmed_at.date()) if declaration.confirmed_at else "—", body_style)],
        [Paragraph("<b>Status / Statut:</b>", label_style), Paragraph(declaration.get_status_display(), body_style)],
    ]
    meta_table = Table(meta_data, colWidths=[200, 320])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#EDE8F5')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("EX-HUSBAND / EX-ÉPOUX", section_style))
    h_data = [
        [Paragraph("<b>First Name / Prénom:</b>", label_style), Paragraph(declaration.ex_husband_first_name, body_style)],
        [Paragraph("<b>Last Name / Nom:</b>", label_style), Paragraph(declaration.ex_husband_last_name, body_style)],
        [Paragraph("<b>National ID / CNI:</b>", label_style), Paragraph(declaration.ex_husband_national_id or "—", body_style)],
    ]
    h_table = Table(h_data, colWidths=[200, 320])
    h_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F5EEF8')),
    ]))
    story.append(h_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("EX-WIFE / EX-ÉPOUSE", section_style))
    w_data = [
        [Paragraph("<b>First Name / Prénom:</b>", label_style), Paragraph(declaration.ex_wife_first_name, body_style)],
        [Paragraph("<b>Last Name / Nom:</b>", label_style), Paragraph(declaration.ex_wife_last_name, body_style)],
        [Paragraph("<b>National ID / CNI:</b>", label_style), Paragraph(declaration.ex_wife_national_id or "—", body_style)],
    ]
    w_table = Table(w_data, colWidths=[200, 320])
    w_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F5EEF8')),
    ]))
    story.append(w_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("COURT DECREE / DÉCISION JUDICIAIRE", section_style))
    dom_str = str(declaration.date_of_marriage) if declaration.date_of_marriage else "—"
    decree_data = [
        [Paragraph("<b>Court / Tribunal:</b>", label_style), Paragraph(declaration.court_name, body_style)],
        [Paragraph("<b>Judgment No. / N° Jugement:</b>", label_style), Paragraph(declaration.judgment_number, body_style)],
        [Paragraph("<b>Date of Decree / Date du Jugement:</b>", label_style), Paragraph(str(declaration.date_of_divorce), body_style)],
        [Paragraph("<b>Date of Marriage / Date du Mariage:</b>", label_style), Paragraph(dom_str, body_style)],
    ]
    decree_table = Table(decree_data, colWidths=[200, 320])
    decree_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#EDE8F5')),
    ]))
    story.append(decree_table)
    story.append(Spacer(1, 20))

    story.append(Paragraph("APPROVAL / APPROBATION", section_style))
    sig_data = [
        [
            Paragraph("<b>Confirmed By / Confirmé par:</b><br/><br/>____________________<br/>"
                      f"{(declaration.reviewed_by.get_full_name() if declaration.reviewed_by else '—')}", body_style),
            Paragraph("<b>Official Stamp / Cachet Officiel:</b><br/><br/><br/>____________________", body_style),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[260, 260])
    sig_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(sig_table)

    doc.build(story, canvasmaker=CertificateCanvas)
    buffer.seek(0)
    return buffer
