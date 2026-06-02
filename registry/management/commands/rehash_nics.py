from django.core.management.base import BaseCommand
from registry.models import Citizen, BirthDeclaration
from registry.services import get_hash


class Command(BaseCommand):
    help = (
        "Recomputes all NIC hash columns using the new HMAC-SHA256 algorithm. "
        "Run this ONCE after upgrading from the plain SHA-256 hash scheme."
    )

    def handle(self, *args, **options):
        self.stdout.write("Rehashing Citizen.national_id_hash …")
        citizen_count = 0
        for citizen in Citizen.all_objects.exclude(national_id__isnull=True).exclude(national_id=""):
            new_hash = get_hash(citizen.national_id)
            if citizen.national_id_hash != new_hash:
                citizen.national_id_hash = new_hash
                citizen.save(update_fields=["national_id_hash"])
                citizen_count += 1
        self.stdout.write(self.style.SUCCESS(f"  Updated {citizen_count} Citizen records."))

        self.stdout.write("Rehashing BirthDeclaration NIC hash columns …")
        decl_count = 0
        for decl in BirthDeclaration.objects.all():
            fields_to_save = []
            if decl.mother_national_id:
                new_hash = get_hash(decl.mother_national_id)
                if decl.mother_national_id_hash != new_hash:
                    decl.mother_national_id_hash = new_hash
                    fields_to_save.append("mother_national_id_hash")
            if decl.father_national_id:
                new_hash = get_hash(decl.father_national_id)
                if decl.father_national_id_hash != new_hash:
                    decl.father_national_id_hash = new_hash
                    fields_to_save.append("father_national_id_hash")
            if fields_to_save:
                decl.save(update_fields=fields_to_save)
                decl_count += 1
        self.stdout.write(self.style.SUCCESS(f"  Updated {decl_count} BirthDeclaration records."))

        self.stdout.write(self.style.SUCCESS("Rehashing complete."))
