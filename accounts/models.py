import string
import random
from django.db import models
from django.contrib.auth.models import AbstractUser
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
    def generate_id(cls, prefix, length=5):
        """Generates a unique ID like PRE-1A2B3"""
        while True:
            chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
            new_id = f"{prefix}-{chars}"
            if not cls.objects.filter(username=new_id).exists():
                return new_id
