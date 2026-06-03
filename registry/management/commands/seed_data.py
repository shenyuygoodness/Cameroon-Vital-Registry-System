from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import date
from registry.models import Region, Division, Subdivision, Citizen, Household, HouseholdMember, BirthDeclaration, DeathDeclaration
from accounts.models import User

class Command(BaseCommand):
    help = 'Seeds the database with Cameroon locations, test users, and sample citizen records.'

    def handle(self, *args, **options):
        self.stdout.write("Starting database seeding...")

        # -------------------------------------------------------------
        # 1. Clear existing data
        # -------------------------------------------------------------
        self.stdout.write("Clearing existing data...")
        BirthDeclaration.objects.all().delete()
        DeathDeclaration.objects.all().delete()
        HouseholdMember.objects.all().delete()
        Household.objects.all().delete()
        User.objects.all().delete()
        
        # Hard-delete Citizens using raw SQL to bypass the soft-delete override on queryset.delete()
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM registry_citizen")
            
        Subdivision.objects.all().delete()
        Division.objects.all().delete()
        Region.objects.all().delete()

        # -------------------------------------------------------------
        # 2. Create Administrative Location Hierarchy
        # -------------------------------------------------------------
        self.stdout.write("Seeding Cameroon Administrative Hierarchy...")
        
        # Center Region
        r_center = Region.objects.create(name="Center", code="CE")
        d_mfoundi = Division.objects.create(name="Mfoundi", region=r_center)
        sub_y1 = Subdivision.objects.create(name="Yaoundé I", division=d_mfoundi)
        sub_y2 = Subdivision.objects.create(name="Yaoundé II", division=d_mfoundi)
        
        # Littoral Region
        r_littoral = Region.objects.create(name="Littoral", code="LT")
        d_wouri = Division.objects.create(name="Wouri", region=r_littoral)
        sub_d1 = Subdivision.objects.create(name="Douala I", division=d_wouri)
        
        # Southwest Region
        r_sw = Region.objects.create(name="Southwest", code="SW")
        d_fako = Division.objects.create(name="Fako", region=r_sw)
        sub_buea = Subdivision.objects.create(name="Buea", division=d_fako)
        
        # Northwest Region
        r_nw = Region.objects.create(name="Northwest", code="NW")
        d_mezam = Division.objects.create(name="Mezam", region=r_nw)
        sub_bam = Subdivision.objects.create(name="Bamenda I", division=d_mezam)

        # -------------------------------------------------------------
        # 3. Seed Citizens
        # -------------------------------------------------------------
        self.stdout.write("Seeding Citizens...")
        
        # Father
        c_father = Citizen.objects.create(
            first_name="Pierre",
            last_name="Ngué",
            dob=date(1980, 5, 10),
            gender="M",
            national_id="112233445",
            subdivision=sub_y1,
            current_status=Citizen.Status.ALIVE
        )
        
        # Mother
        c_mother = Citizen.objects.create(
            first_name="Chantal",
            last_name="Mbié",
            dob=date(1985, 10, 24),
            gender="F",
            national_id="998877665",
            subdivision=sub_y1,
            current_status=Citizen.Status.ALIVE
        )
        
        # Citizen who will link to Citizen User Account
        c_citizen_user = Citizen.objects.create(
            first_name="Jean",
            last_name="Nguene",
            dob=date(1998, 2, 18),
            gender="M",
            national_id="123456789",
            subdivision=sub_y1,
            current_status=Citizen.Status.ALIVE
        )
        
        # Under-18 child with no NIC
        c_child = Citizen.objects.create(
            first_name="Marie",
            last_name="Atangana",
            dob=date(2015, 6, 12),
            gender="F",
            father=c_father,
            mother=c_mother,
            subdivision=sub_y1,
            current_status=Citizen.Status.ALIVE
        )

        # -------------------------------------------------------------
        # 4. Household Seeding
        # -------------------------------------------------------------
        self.stdout.write("Seeding Household registries...")
        h_family = Household.objects.create(
            household_number="HH-CE-00249",
            head=c_father,
            subdivision=sub_y1
        )
        
        HouseholdMember.objects.create(household=h_family, citizen=c_father, relationship_to_head=HouseholdMember.Relationship.HEAD)
        HouseholdMember.objects.create(household=h_family, citizen=c_mother, relationship_to_head=HouseholdMember.Relationship.SPOUSE)
        HouseholdMember.objects.create(household=h_family, citizen=c_child, relationship_to_head=HouseholdMember.Relationship.CHILD)

        # -------------------------------------------------------------
        # 5. Create RBAC Test Users
        # -------------------------------------------------------------
        self.stdout.write("Creating Test Users...")

        # 1. Super Admin
        super_admin = User.objects.create_superuser(
            username="superadmin",
            email="divinegoodnesst@gmail.com",
            password="password123",
            first_name="Global",
            last_name="Administrator",
            role=User.Role.SUPER_ADMIN
        )

        # 2. Regional Admin (Center Region)
        regional_admin = User.objects.create_user(
            username="REG-00001",
            email="regionaladmin@clvrs.cm",
            password="password123",
            first_name="Martin",
            last_name="Amadou",
            role=User.Role.REGIONAL_ADMIN,
            region=r_center
        )

        # 3. Council Officer (Yaoundé I subdivision)
        council_officer = User.objects.create_user(
            username="COU-00001",
            email="councilofficer@clvrs.cm",
            password="password123",
            first_name="Therese",
            last_name="Fouda",
            role=User.Role.COUNCIL_OFFICER,
            subdivision=sub_y1,
            division=sub_y1.division,
            region=sub_y1.division.region
        )

        # 4. Hospital Staff (Yaoundé General Hospital - linked to Yaoundé I subdivision)
        hospital_staff = User.objects.create_user(
            username="HOS-00001",
            email="hospitalstaff@clvrs.cm",
            password="password123",
            first_name="Samuel",
            last_name="Eto'o",
            role=User.Role.HOSPITAL_STAFF,
            subdivision=sub_y1,
            division=sub_y1.division,
            region=sub_y1.division.region,
            hospital_name="Yaoundé General Hospital",
            hospital_type="Government",
            dr_incharge="Dr. Samuel Eto'o"
        )

        # 5. Citizen User (linked to Jean Nguene)
        citizen_user = User.objects.create_user(
            username="citizenuser",
            email="citizenuser@clvrs.cm",
            password="password123",
            first_name="Jean",
            last_name="Nguene",
            role=User.Role.CITIZEN,
            citizen_profile=c_citizen_user
        )

        # -------------------------------------------------------------
        # 6. Seed Vital Declarations
        # -------------------------------------------------------------
        self.stdout.write("Seeding Pending Declarations...")

        # Pending Birth Declaration (Undecided names)
        BirthDeclaration.objects.create(
            child_first_name="",
            child_last_name="",
            child_dob=date(2026, 5, 15),
            child_gender="M",
            father_national_id=c_father.national_id,
            father_name=c_father.get_full_name(),
            mother_national_id=c_mother.national_id,
            mother_name=c_mother.get_full_name(),
            place_of_birth="Yaoundé General Hospital",
            subdivision=sub_y1,
            created_by=hospital_staff,
            remarks="Child was delivered safely. Parents requested name deferral.",
            status=BirthDeclaration.Status.PENDING
        )

        # Confirmed Birth Declaration (For Jean Nguene to link his certificate)
        BirthDeclaration.objects.create(
            child_first_name="Jean",
            child_last_name="Nguene",
            child_dob=date(1998, 2, 18),
            child_gender="M",
            mother_national_id="998877665",
            mother_name="Chantal Mbié",
            place_of_birth="Yaoundé General Hospital",
            subdivision=sub_y1,
            created_by=hospital_staff,
            reviewed_by=council_officer,
            status=BirthDeclaration.Status.CONFIRMED,
            confirmed_at=timezone.now(),
            associated_citizen=c_citizen_user
        )

        # Pending Death Declaration
        # Declaring Pierre Ngué as deceased
        DeathDeclaration.objects.create(
            citizen=c_father,
            death_date=date(2026, 5, 20),
            death_cause="Respiratory Failure",
            place_of_death="Yaoundé General Hospital",
            declarant_name=c_mother.get_full_name(),
            declarant_relation="Spouse",
            subdivision=sub_y1,
            created_by=hospital_staff,
            status=DeathDeclaration.Status.PENDING
        )

        self.stdout.write(self.style.SUCCESS("Database seeded successfully with Cameroon locations, users, and cases!"))
