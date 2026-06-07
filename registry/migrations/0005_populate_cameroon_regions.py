from django.db import migrations

CAMEROON_REGIONS = [
    ("Adamawa",    "AD"),
    ("Centre",     "CE"),
    ("East",       "ES"),
    ("Far North",  "EN"),
    ("Littoral",   "LT"),
    ("North",      "NO"),
    ("Northwest",  "NW"),
    ("South",      "SU"),
    ("Southwest",  "SW"),
    ("West",       "OU"),
]


def populate_regions(apps, schema_editor):
    Region = apps.get_model('registry', 'Region')
    for name, code in CAMEROON_REGIONS:
        Region.objects.get_or_create(code=code, defaults={'name': name})


def remove_regions(apps, schema_editor):
    Region = apps.get_model('registry', 'Region')
    Region.objects.filter(code__in=[code for _, code in CAMEROON_REGIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('registry', '0004_mfa_audit_actions'),
    ]

    operations = [
        migrations.RunPython(populate_regions, reverse_code=remove_regions),
    ]
