import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Creates a SUPER_ADMIN user from env vars if none exists (idempotent).'

    def handle(self, *args, **options):
        username = os.environ.get('SUPERADMIN_USERNAME')
        password = os.environ.get('SUPERADMIN_PASSWORD')
        email    = os.environ.get('SUPERADMIN_EMAIL', '')

        if not username or not password:
            self.stdout.write('SUPERADMIN_USERNAME / SUPERADMIN_PASSWORD not set — skipping.')
            return

        if User.objects.filter(role=User.Role.SUPER_ADMIN).exists():
            self.stdout.write('Super admin already exists — skipping.')
            return

        user = User.objects.create_superuser(username, email, password)
        user.role = User.Role.SUPER_ADMIN
        user.save()
        self.stdout.write(self.style.SUCCESS(f'Super admin "{username}" created.'))
