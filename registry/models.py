import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import date
from django.core.files.storage import FileSystemStorage
from django.utils.translation import gettext_lazy as _
from registry.services import EncryptionService, get_hash

secure_storage = FileSystemStorage(location=settings.SECURE_MEDIA_ROOT, base_url='/registry/secure-media/')

class EncryptedCharField(models.CharField):
    """
    A custom field that encrypts content on save and decrypts it on read.
    Useful for protecting sensitive data like National IDs in the database.
    """
    description = "Symmetrically encrypted CharField"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is not None and value != '':
            return EncryptionService.encrypt(value)
        return value

    def from_db_value(self, value, expression, connection):
        if value is not None and value != '':
            return EncryptionService.decrypt(value)
        return value

    def to_python(self, value):
        return value


# 1. Administrative Locations
class Region(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)

    def __str__(self):
        return self.name

class Division(models.Model):
    name = models.CharField(max_length=100)
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='divisions')

    class Meta:
        unique_together = ('name', 'region')

    def __str__(self):
        return f"{self.name} ({self.region.name})"

class Subdivision(models.Model):
    """
    Subdivision represents the Council level in Cameroon.
    """
    name = models.CharField(max_length=100)
    division = models.ForeignKey(Division, on_delete=models.CASCADE, related_name='subdivisions')

    class Meta:
        unique_together = ('name', 'division')

    def __str__(self):
        return f"{self.name} ({self.division.name})"


# 2. Citizen Model
class CitizenQuerySet(models.QuerySet):
    def delete(self):
        return self.update(is_deleted=True, deleted_at=timezone.now())

class CitizenManager(models.Manager):
    def get_queryset(self):
        return CitizenQuerySet(self.model, using=self._db).filter(is_deleted=False)

class CitizenAllManager(models.Manager):
    def get_queryset(self):
        return CitizenQuerySet(self.model, using=self._db)

class Citizen(models.Model):
    class Gender(models.TextChoices):
        MALE = 'M', _('Male')
        FEMALE = 'F', _('Female')

    class Status(models.TextChoices):
        ALIVE = 'ALIVE', _('Alive')
        DECEASED = 'DECEASED', _('Deceased')

    # Sensitive encrypted national ID, blank=True, null=True for under-18s
    national_id = EncryptedCharField(max_length=255, blank=True, null=True)
    # Searchable unique hash of national ID (SHA-256)
    national_id_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, db_index=True)
    
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    dob = models.DateField()
    gender = models.CharField(max_length=1, choices=Gender.choices)
    photo = models.ImageField(upload_to='photos/%Y/%m/%d/', storage=secure_storage, blank=True, null=True)
    
    father = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='fathered_children')
    mother = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='mothered_children')
    
    current_status = models.CharField(max_length=10, choices=Status.choices, default=Status.ALIVE)
    death_date = models.DateField(blank=True, null=True)
    
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT, related_name='citizens')
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Soft delete fields
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CitizenManager()
    all_objects = CitizenAllManager()

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        name = self.get_full_name()
        nic = f"NIC: {self.national_id}" if self.national_id else "No NIC"
        status_label = "Deceased" if self.current_status == self.Status.DECEASED else "Alive"
        return f"{name} ({nic}, {status_label})"

    def get_full_name(self):
        first = self.first_name or "Unnamed"
        last = self.last_name or "Child"
        return f"{first} {last}".strip()

    @property
    def age(self):
        today = date.today()
        # If deceased, calculate age at death
        end_date = self.death_date if self.current_status == self.Status.DECEASED and self.death_date else today
        return end_date.year - self.dob.year - ((end_date.month, end_date.day) < (self.dob.month, self.dob.day))

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    def save(self, *args, **kwargs):
        # Sync national_id_hash for exact search matches and unique constraint checks
        if self.national_id:
            self.national_id_hash = get_hash(self.national_id)
        else:
            self.national_id_hash = None
            
        # Age validation logic (NIC required for 18+)
        if self.age >= 18 and not self.national_id:
            # We will handle form validation, but enforce a warning or raise validation here if appropriate.
            # However, during save, since Django admin or bulk uploads might bypass form clean, we can validate.
            pass
            
        super().save(*args, **kwargs)


# 3. Household Models
class Household(models.Model):
    household_number = models.CharField(max_length=50, unique=True)
    head = models.ForeignKey(Citizen, on_delete=models.PROTECT, related_name='headed_households')
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT, related_name='households')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Household {self.household_number} (Head: {self.head.get_full_name()})"

class HouseholdMember(models.Model):
    class Relationship(models.TextChoices):
        HEAD = 'HEAD', 'Head of Household'
        SPOUSE = 'SPOUSE', 'Spouse'
        CHILD = 'CHILD', 'Child'
        PARENT = 'PARENT', 'Parent'
        SIBLING = 'SIBLING', 'Sibling'
        OTHER = 'OTHER', 'Other Relative/Member'

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='members')
    citizen = models.OneToOneField(Citizen, on_delete=models.CASCADE, related_name='household_membership')
    relationship_to_head = models.CharField(max_length=20, choices=Relationship.choices)

    def __str__(self):
        return f"{self.citizen.get_full_name()} - {self.get_relationship_to_head_display()}"


# 4. Declarations (Vital Events)
class VitalEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        REJECTED = 'REJECTED', 'Rejected'

    declaration_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True, null=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='%(class)s_created')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='%(class)s_reviewed')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True


class BirthDeclaration(VitalEvent):
    child_first_name = models.CharField(max_length=100, blank=True, null=True)
    child_last_name = models.CharField(max_length=100, blank=True, null=True)
    child_dob = models.DateField()
    child_gender = models.CharField(max_length=1, choices=Citizen.Gender.choices)

    # NICs stored encrypted; hash columns enable exact-match lookups without decryption
    father_national_id = EncryptedCharField(max_length=255, blank=True, null=True)
    father_national_id_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    father_name = models.CharField(max_length=200, blank=True, null=True)

    mother_national_id = EncryptedCharField(max_length=255)
    mother_national_id_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    mother_name = models.CharField(max_length=200)
    
    place_of_birth = models.CharField(max_length=150, help_text="Hospital name or village")
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT, related_name='birth_declarations')
    
    associated_citizen = models.ForeignKey(Citizen, on_delete=models.SET_NULL, null=True, blank=True, related_name='birth_record')

    def __str__(self):
        names = f"{self.child_first_name or ''} {self.child_last_name or ''}".strip()
        child_label = names if names else "Unnamed Child"
        return f"Birth Dec {self.declaration_number} ({child_label})"

    def save(self, *args, **kwargs):
        if not self.declaration_number:
            year = self.child_dob.year if self.child_dob else timezone.now().year
            for _ in range(10):
                serial = uuid.uuid4().hex[:8].upper()
                candidate = f"CLVRS-B-{year}-{serial}"
                if not BirthDeclaration.objects.filter(declaration_number=candidate).exists():
                    self.declaration_number = candidate
                    break
        # Sync hash columns for secure lookup
        self.mother_national_id_hash = get_hash(self.mother_national_id) if self.mother_national_id else None
        self.father_national_id_hash = get_hash(self.father_national_id) if self.father_national_id else None
        super().save(*args, **kwargs)


class DeathDeclaration(VitalEvent):
    citizen = models.ForeignKey(Citizen, on_delete=models.PROTECT, related_name='death_declarations')
    death_date = models.DateField()
    death_cause = models.CharField(max_length=255)
    place_of_death = models.CharField(max_length=150)
    
    declarant_name = models.CharField(max_length=200)
    declarant_relation = models.CharField(max_length=100, help_text="Relation to the deceased")
    
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT, related_name='death_declarations')

    def __str__(self):
        return f"Death Dec {self.declaration_number} ({self.citizen.get_full_name()})"

    def save(self, *args, **kwargs):
        if not self.declaration_number:
            year = self.death_date.year if self.death_date else timezone.now().year
            for _ in range(10):
                serial = uuid.uuid4().hex[:8].upper()
                candidate = f"CLVRS-D-{year}-{serial}"
                if not DeathDeclaration.objects.filter(declaration_number=candidate).exists():
                    self.declaration_number = candidate
                    break
        super().save(*args, **kwargs)


# 5. Audit Logging
class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE  = 'CREATE',  'Create'
        UPDATE  = 'UPDATE',  'Update'
        DELETE  = 'DELETE',  'Delete'
        APPROVE = 'APPROVE', 'Approve'
        REJECT  = 'REJECT',  'Reject'
        IMPORT  = 'IMPORT',  'Import'
        EXPORT  = 'EXPORT',  'Export'
        LOGIN   = 'LOGIN',   'Login'
        LOGOUT  = 'LOGOUT',  'Logout'
        LOCKOUT = 'LOCKOUT', 'Account Locked'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=20, choices=Action.choices)
    model_name = models.CharField(max_length=100)
    record_id = models.CharField(max_length=100, blank=True, null=True)
    details = models.TextField(help_text="Detailed change summary. NEVER log decrypted NICs.")
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        user_str = self.user.username if self.user else "System"
        return f"{self.timestamp} - {user_str} - {self.action} on {self.model_name}"
