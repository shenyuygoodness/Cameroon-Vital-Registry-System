from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import date, timedelta
from accounts.models import User
from registry.models import Region, Division, Subdivision, Citizen, BirthDeclaration, DeathDeclaration
from registry.services import EncryptionService, get_hash
from registry.forms import CitizenForm

class CLVRSTests(TestCase):
    def setUp(self):
        # Locations
        self.region, _ = Region.objects.get_or_create(code="CE", defaults={"name": "Center Region"})
        self.division = Division.objects.create(name="Mfoundi Division", region=self.region)
        self.subdivision_y1 = Subdivision.objects.create(name="Yaoundé I", division=self.division)
        self.subdivision_y2 = Subdivision.objects.create(name="Yaoundé II", division=self.division)

        # Users
        self.officer_y1 = User.objects.create_user(
            username="officer1", password="password123",
            role=User.Role.COUNCIL_OFFICER, subdivision=self.subdivision_y1
        )
        self.officer_y2 = User.objects.create_user(
            username="officer2", password="password123",
            role=User.Role.COUNCIL_OFFICER, subdivision=self.subdivision_y2
        )

        # Active Citizen
        self.citizen = Citizen.objects.create(
            first_name="Jean",
            last_name="Nguene",
            dob=date(1990, 5, 12),
            gender="M",
            national_id="123456789",
            subdivision=self.subdivision_y1
        )

    def test_national_id_encryption(self):
        """
        Tests that NIC is encrypted in the database but decrypted when accessed,
        and that a unique hash is stored deterministically.
        """
        # Fetch directly from DB raw values using cursor (to verify encryption is saved in DB)
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT national_id FROM registry_citizen WHERE id = %s", [self.citizen.id])
            row = cursor.fetchone()
            raw_nic = row[0]
        
        # Verify it's not plaintext '123456789' and starts with 'v2:' (AES-GCM prefix)
        self.assertNotEqual(raw_nic, "123456789")
        self.assertTrue(raw_nic.startswith("v2:"))
        
        # Verify decrypt works
        decrypted = EncryptionService.decrypt(raw_nic)
        self.assertEqual(decrypted, "123456789")
        
        # Verify access via model attribute is decrypted automatically
        citizen_fetched = Citizen.objects.get(id=self.citizen.id)
        self.assertEqual(citizen_fetched.national_id, "123456789")
        
        # Verify hash match
        self.assertEqual(citizen_fetched.national_id_hash, get_hash("123456789"))

    def test_soft_delete(self):
        """
        Tests that soft deleting a citizen hides it from the default manager
        but preserves the database row.
        """
        self.assertEqual(Citizen.objects.count(), 1)
        self.citizen.soft_delete()
        
        # Default manager should hide it
        self.assertEqual(Citizen.objects.count(), 0)
        # All objects manager should still find it
        self.assertEqual(Citizen.all_objects.count(), 1)
        
        # Restore it
        self.citizen.restore()
        self.assertEqual(Citizen.objects.count(), 1)

    def test_citizen_form_validation(self):
        """
        Tests age-based NIC validation rules on CitizenForm.
        """
        # 1. Adult (30 years old) without NIC -> Invalid
        data_adult_no_nic = {
            'first_name': 'Adult',
            'last_name': 'No NIC',
            'dob': date.today() - timedelta(days=30*365),
            'gender': 'M',
            'national_id': '',
            'subdivision': self.subdivision_y1.id
        }
        form1 = CitizenForm(data=data_adult_no_nic, user=self.officer_y1)
        self.assertFalse(form1.is_valid())
        self.assertIn('national_id', form1.errors)

        # 2. Child (10 years old) with NIC -> Invalid
        data_child_with_nic = {
            'first_name': 'Child',
            'last_name': 'With NIC',
            'dob': date.today() - timedelta(days=10*365),
            'gender': 'F',
            'national_id': '112233445',
            'subdivision': self.subdivision_y1.id
        }
        form2 = CitizenForm(data=data_child_with_nic, user=self.officer_y1)
        self.assertFalse(form2.is_valid())
        self.assertIn('national_id', form2.errors)

        # 3. Child (10 years old) without NIC but with Photo -> Valid
        from django.core.files.uploadedfile import SimpleUploadedFile
        mock_photo = SimpleUploadedFile(
            name='child_photo.png',
            content=b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x4c\x01\x00\x3b',
            content_type='image/png'
        )
        data_child_no_nic = {
            'first_name': 'Child',
            'last_name': 'No NIC',
            'dob': date.today() - timedelta(days=10*365),
            'gender': 'F',
            'national_id': '',
            'subdivision': self.subdivision_y1.id
        }
        form3 = CitizenForm(data=data_child_no_nic, files={'photo': mock_photo}, user=self.officer_y1)
        self.assertTrue(form3.is_valid())

    def test_jurisdiction_filtering(self):
        """
        Tests that get_visible_citizens_qs restricts access correctly.
        """
        from registry.views import get_visible_citizens_qs
        
        # Citizen is in subdivision_y1
        # officer_y1 belongs to subdivision_y1
        qs_y1 = get_visible_citizens_qs(self.officer_y1)
        self.assertIn(self.citizen, qs_y1)
        
        # officer_y2 belongs to subdivision_y2
        qs_y2 = get_visible_citizens_qs(self.officer_y2)
        self.assertNotIn(self.citizen, qs_y2)

    def test_secure_media_access(self):
        """
        Tests that secure media serves correctly based on permissions.
        """
        import os
        from django.urls import reverse
        from django.conf import settings
        
        # Create a mock file
        test_dir = os.path.join(settings.SECURE_MEDIA_ROOT, 'photos')
        os.makedirs(test_dir, exist_ok=True)
        test_file_path = os.path.join(test_dir, 'test_photo.gif')
        with open(test_file_path, 'wb') as f:
            f.write(b'gif')
            
        self.citizen.photo = 'photos/test_photo.gif'
        self.citizen.save()

        url = reverse('registry:secure_media', kwargs={'path': 'photos/test_photo.gif'})
        
        # Unauthenticated
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302) # redirect to login
        
        # Wrong jurisdiction
        self.client.force_login(self.officer_y2)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        
        # Correct jurisdiction
        self.client.force_login(self.officer_y1)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Consume streaming content to release the file handle without
        # triggering request_finished signal (which would close the DB connection on PostgreSQL)
        b''.join(response.streaming_content)
        os.remove(test_file_path)
