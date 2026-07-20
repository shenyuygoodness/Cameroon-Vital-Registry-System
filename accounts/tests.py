from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache

class TestRatelimit(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.login_url = reverse('login')

    def test_login_rate_limiting(self):
        # We allow 5 requests per minute
        for i in range(5):
            response = self.client.post(self.login_url, {'username': 'testuser', 'email': 'testuser@clvrs.cm', 'password': 'wrongpassword'})
            self.assertEqual(response.status_code, 200)

        # The 6th request should be blocked
        response = self.client.post(self.login_url, {'username': 'testuser', 'email': 'testuser@clvrs.cm', 'password': 'wrongpassword'})
        self.assertEqual(response.status_code, 403)


from accounts.models import User

class TestCustomAuthentication(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.login_url = reverse('login')
        self.user = User.objects.create_user(
            username="REG-99999",
            email="testadmin@clvrs.cm",
            password="securepassword123",
            role=User.Role.REGIONAL_ADMIN,
        )

    def test_login_success(self):
        response = self.client.post(self.login_url, {
            'username': 'REG-99999',
            'email': 'testadmin@clvrs.cm',
            'password': 'securepassword123'
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')

    def test_login_fails_on_incorrect_email(self):
        response = self.client.post(self.login_url, {
            'username': 'REG-99999',
            'email': 'wrongemail@clvrs.cm',
            'password': 'securepassword123'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid credentials. Please verify your System ID and Email.")

    from django.test import override_settings

    @override_settings(DISABLE_MFA=True)
    def test_login_mfa_disabled(self):
        response = self.client.post(self.login_url, {
            'username': 'REG-99999',
            'email': 'testadmin@clvrs.cm',
            'password': 'securepassword123'
        })
        self.assertEqual(response.status_code, 302)
        # Should redirect directly to home/dashboard (LOGIN_REDIRECT_URL)
        self.assertEqual(response.url, '/')

    @override_settings(DISABLE_MFA=False)
    def test_login_mfa_enabled(self):
        response = self.client.post(self.login_url, {
            'username': 'REG-99999',
            'email': 'testadmin@clvrs.cm',
            'password': 'securepassword123'
        })
        self.assertEqual(response.status_code, 302)
        # Should redirect to the MFA verify URL
        self.assertEqual(response.url, '/mfa/verify/')

