from django.test import TestCase, Client
from django.urls import reverse

class TestRatelimit(TestCase):
    def setUp(self):
        self.client = Client()
        self.login_url = reverse('login')

    def test_login_rate_limiting(self):
        # We allow 5 requests per minute
        for i in range(5):
            response = self.client.post(self.login_url, {'username': 'testuser', 'password': 'wrongpassword'})
            self.assertEqual(response.status_code, 200)

        # The 6th request should be blocked
        response = self.client.post(self.login_url, {'username': 'testuser', 'password': 'wrongpassword'})
        self.assertEqual(response.status_code, 403)
