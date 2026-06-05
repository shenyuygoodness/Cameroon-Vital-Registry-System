import string
import hashlib
import secrets
from datetime import timedelta
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', _('Super Admin')
        REGIONAL_ADMIN = 'REGIONAL_ADMIN', _('Regional Admin')
        COUNCIL_OFFICER = 'COUNCIL_OFFICER', _('Council Officer (Subdivision)')
        HOSPITAL_STAFF = 'HOSPITAL_STAFF', _('Hospital Staff')
        CITIZEN = 'CITIZEN', _('Citizen')

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CITIZEN
    )
    
    # Jurisdiction bounds
    region = models.ForeignKey(
        'registry.Region',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='region_admins',
        help_text="Assigned region for Regional Admins."
    )
    division = models.ForeignKey(
        'registry.Division',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='division_admins',
        help_text="Assigned division (optional)."
    )
    subdivision = models.ForeignKey(
        'registry.Subdivision',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subdivision_users',
        help_text="Assigned subdivision (Council) for Council Officers & Hospital Staff."
    )
    hospital_name = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="Hospital name for Hospital Staff."
    )
    hospital_type = models.CharField(
        max_length=50,
        choices=[('Government', 'Government'), ('Private', 'Private')],
        blank=True,
        null=True,
        help_text="Hospital type (Government or Private)."
    )
    dr_incharge = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="Name of the Doctor in charge."
    )
    citizen_profile = models.OneToOneField(
        'registry.Citizen',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_account',
        help_text="Linked citizen profile for Citizen users."
    )

    def __str__(self):
        role_label = self.get_role_display()
        if self.role == self.Role.COUNCIL_OFFICER and self.subdivision:
            return f"{self.username} ({role_label} - {self.subdivision.name})"
        elif self.role == self.Role.HOSPITAL_STAFF and self.hospital_name:
            return f"{self.username} (Hospital Staff - {self.hospital_name})"
        return f"{self.username} ({role_label})"

    @property
    def is_super_admin(self):
        return self.role == self.Role.SUPER_ADMIN or self.is_superuser

    @property
    def is_regional_admin(self):
        return self.role == self.Role.REGIONAL_ADMIN

    @property
    def is_council_officer(self):
        return self.role == self.Role.COUNCIL_OFFICER

    @property
    def is_hospital_staff(self):
        return self.role == self.Role.HOSPITAL_STAFF

    @property
    def is_citizen(self):
        return self.role == self.Role.CITIZEN

    @classmethod
    def generate_id(cls, prefix, length=6):
        """Generates a cryptographically random unique System ID like PRE-A1B2C3."""
        alphabet = string.ascii_uppercase + string.digits
        while True:
            chars = ''.join(secrets.choice(alphabet) for _ in range(length))
            new_id = f"{prefix}-{chars}"
            if not cls.objects.filter(username=new_id).exists():
                return new_id


class MFAToken(models.Model):
    """
    Stores a single-use, time-limited OTP for email-based MFA.
    The raw code is never persisted — only its SHA-256 hash is stored.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='mfa_tokens',
    )
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def generate_for_user(cls, user):
        """
        Invalidates any outstanding tokens for the user, generates a fresh
        9-digit OTP, persists its hash, and returns (raw_otp, token).
        """
        from django.conf import settings as django_settings
        cls.objects.filter(user=user, is_used=False).update(is_used=True)

        raw_otp = ''.join(str(secrets.randbelow(10)) for _ in range(9))
        expiry = getattr(django_settings, 'MFA_OTP_EXPIRY_SECONDS', 600)

        token = cls.objects.create(
            user=user,
            code_hash=hashlib.sha256(raw_otp.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(seconds=expiry),
        )
        return raw_otp, token

    def verify(self, code: str) -> bool:
        """Return True and mark used if code matches and has not expired."""
        if self.is_used or timezone.now() > self.expires_at:
            return False
        candidate = hashlib.sha256(code.encode()).hexdigest()
        if secrets.compare_digest(self.code_hash, candidate):
            self.is_used = True
            self.save(update_fields=['is_used'])
            return True
        return False
