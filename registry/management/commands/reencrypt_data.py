"""
Management command: reencrypt_data

Migrates all Fernet (AES-128-CBC) encrypted NID values in the database to the
new AES-256-GCM format (prefixed with 'v2:').

Safe to run multiple times — rows already using the v2 format are skipped.

Usage:
    python manage.py reencrypt_data [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Re-encrypt all legacy Fernet NID ciphertext to AES-256-GCM format."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be migrated without writing any changes.',
        )

    def handle(self, *args, **options):
        from registry.models import Citizen, BirthDeclaration
        from registry.services import EncryptionService, _AES_VERSION_PREFIX

        dry_run = options['dry_run']
        total_migrated = 0

        def needs_migration(value):
            return value and not value.startswith(_AES_VERSION_PREFIX)

        def reencrypt(value):
            """Decrypt with legacy Fernet path, re-encrypt with AES-256-GCM."""
            plaintext = EncryptionService.decrypt(value)
            return EncryptionService.encrypt(plaintext)

        # ── Citizens ──────────────────────────────────────────────────────────
        citizen_qs = Citizen.all_objects.exclude(national_id__isnull=True).exclude(national_id='')
        citizen_count = 0
        for citizen in citizen_qs.iterator(chunk_size=200):
            raw = citizen.__class__.all_objects.filter(pk=citizen.pk).values('national_id').first()
            if not raw:
                continue
            raw_value = raw['national_id']
            if needs_migration(raw_value):
                citizen_count += 1
                if not dry_run:
                    with transaction.atomic():
                        new_value = reencrypt(raw_value)
                        citizen.__class__.all_objects.filter(pk=citizen.pk).update(national_id=new_value)

        self.stdout.write(f"Citizens  — national_id: {citizen_count} rows {'would be' if dry_run else ''} migrated.")
        total_migrated += citizen_count

        # ── BirthDeclarations ─────────────────────────────────────────────────
        birth_count_mother = 0
        birth_count_father = 0
        for decl in BirthDeclaration.objects.iterator(chunk_size=200):
            raw = BirthDeclaration.objects.filter(pk=decl.pk).values(
                'mother_national_id', 'father_national_id'
            ).first()
            if not raw:
                continue
            updates = {}
            if needs_migration(raw.get('mother_national_id')):
                birth_count_mother += 1
                if not dry_run:
                    updates['mother_national_id'] = reencrypt(raw['mother_national_id'])
            if needs_migration(raw.get('father_national_id')):
                birth_count_father += 1
                if not dry_run:
                    updates['father_national_id'] = reencrypt(raw['father_national_id'])
            if updates and not dry_run:
                with transaction.atomic():
                    BirthDeclaration.objects.filter(pk=decl.pk).update(**updates)

        self.stdout.write(f"BirthDecl — mother_national_id: {birth_count_mother} rows {'would be' if dry_run else ''} migrated.")
        self.stdout.write(f"BirthDecl — father_national_id: {birth_count_father} rows {'would be' if dry_run else ''} migrated.")
        total_migrated += birth_count_mother + birth_count_father

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — {total_migrated} rows would be re-encrypted. "
                "Run without --dry-run to apply."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. {total_migrated} rows re-encrypted to AES-256-GCM."
            ))
