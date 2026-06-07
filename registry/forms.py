from django import forms
from django.utils import timezone
from datetime import date
from registry.models import Citizen, BirthDeclaration, DeathDeclaration, get_hash
from registry.validators import validate_image_upload

class CitizenForm(forms.ModelForm):
    class Meta:
        model = Citizen
        fields = ['first_name', 'last_name', 'dob', 'gender', 'photo', 'father', 'mother', 'national_id', 'subdivision']
        widgets = {
            'dob': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'photo': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.jpg,.jpeg,.png,.webp',
            }),
            'father': forms.Select(attrs={'class': 'form-select'}),
            'mother': forms.Select(attrs={'class': 'form-select'}),
            'national_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 112233445'}),
            'subdivision': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Enforce that parent drop-downs only show alive citizens
        self.fields['father'].queryset = Citizen.objects.filter(gender='M', current_status=Citizen.Status.ALIVE)
        self.fields['mother'].queryset = Citizen.objects.filter(gender='F', current_status=Citizen.Status.ALIVE)
        
        # If the user is a Council Officer, lock the subdivision to their own
        if user and not user.is_super_admin:
            if user.subdivision:
                self.fields['subdivision'].queryset = Citizen._meta.get_field('subdivision').remote_field.model.objects.filter(id=user.subdivision.id)
                self.fields['subdivision'].initial = user.subdivision
                self.fields['subdivision'].disabled = True
            
        # If editing and under-18, the national ID field can be empty
        self.fields['national_id'].required = False
        self.fields['first_name'].required = False
        self.fields['last_name'].required = False
        self.fields['father'].required = False
        self.fields['mother'].required = False

        # Attach upload security validator to the photo field
        self.fields['photo'].validators.append(validate_image_upload)

    def clean(self):
        cleaned_data = super().clean()
        dob = cleaned_data.get('dob')
        national_id = cleaned_data.get('national_id')
        
        if dob:
            if dob > date.today():
                self.add_error('dob', "Date of birth cannot be in the future.")
                return cleaned_data
                
            today = date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            
            if age >= 18:
                if not national_id:
                    self.add_error('national_id', "National ID (NIC) is mandatory for citizens aged 18 and above.")
            else:
                if national_id:
                    self.add_error('national_id', f"Citizens under 18 cannot have a National ID. (Age: {age})")
                
                # Check for photo if citizen is under 18
                photo = cleaned_data.get('photo')
                if not photo and not (self.instance and self.instance.photo):
                    self.add_error('photo', "A photo of the child is required for citizens under 18 since they do not have a National ID.")
                    
        if national_id:
            nic_clean = national_id.strip()
            if len(nic_clean) < 9 or len(nic_clean) > 20:
                self.add_error('national_id', "National ID must be between 9 and 20 characters.")
                
            # Check unique hash (excluding self if editing)
            nic_hash = get_hash(nic_clean)
            qs = Citizen.objects.filter(national_id_hash=nic_hash)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error('national_id', "A citizen with this National ID already exists in the system.")
                
        return cleaned_data


class BirthDeclarationForm(forms.ModelForm):
    class Meta:
        model = BirthDeclaration
        fields = [
            'child_first_name', 'child_last_name', 'child_dob', 'child_gender',
            'father_national_id', 'father_name', 'mother_national_id', 'mother_name',
            'place_of_birth', 'subdivision', 'remarks'
        ]
        widgets = {
            'child_first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional (leave blank if undecided)'}),
            'child_last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'child_dob': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'child_gender': forms.Select(attrs={'class': 'form-select'}),
            'father_national_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Father CNI (Optional)'}),
            'father_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Father Full Name (Optional)'}),
            'mother_national_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mother CNI (Required)'}),
            'mother_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mother Full Name (Required)'}),
            'place_of_birth': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Yaoundé Central Hospital'}),
            'subdivision': forms.Select(attrs={'class': 'form-select'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional observations...'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Enforce that hospital staff can only submit declarations to their assigned subdivision
        if user and not user.is_super_admin:
            if user.subdivision:
                self.fields['subdivision'].queryset = Citizen._meta.get_field('subdivision').remote_field.model.objects.filter(id=user.subdivision.id)
                self.fields['subdivision'].initial = user.subdivision
                self.fields['subdivision'].disabled = True

    def clean_child_dob(self):
        dob = self.cleaned_data.get('child_dob')
        if dob and dob > date.today():
            raise forms.ValidationError("Date of birth cannot be in the future.")
        return dob

    def clean(self):
        cleaned_data = super().clean()
        father_nic = cleaned_data.get('father_national_id')
        father_name = cleaned_data.get('father_name')
        mother_nic = cleaned_data.get('mother_national_id')
        mother_name = cleaned_data.get('mother_name')

        if father_nic and not father_name:
            self.add_error('father_name', "Father's name is required if Father's CNI is provided.")
        if mother_nic and not mother_name:
            self.add_error('mother_name', "Mother's name is required.")

        return cleaned_data


class DeathDeclarationForm(forms.ModelForm):
    class Meta:
        model = DeathDeclaration
        fields = [
            'citizen', 'death_date', 'death_cause', 'place_of_death',
            'declarant_name', 'declarant_relation', 'subdivision', 'remarks'
        ]
        widgets = {
            'citizen': forms.Select(attrs={'class': 'form-select'}),
            'death_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'death_cause': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Cardiopulmonary arrest'}),
            'place_of_death': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. District Hospital, Sangmelima'}),
            'declarant_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Declarant Full Name'}),
            'declarant_relation': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Spouse, Son, Neighbor'}),
            'subdivision': forms.Select(attrs={'class': 'form-select'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional observations...'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Enforce that hospital staff can only submit declarations to their assigned subdivision
        if user and not user.is_super_admin:
            if user.subdivision:
                self.fields['subdivision'].queryset = Citizen._meta.get_field('subdivision').remote_field.model.objects.filter(id=user.subdivision.id)
                self.fields['subdivision'].initial = user.subdivision
                self.fields['subdivision'].disabled = True
                
                # Show only citizens in their subdivision who are currently ALIVE
                self.fields['citizen'].queryset = Citizen.objects.filter(subdivision=user.subdivision, current_status=Citizen.Status.ALIVE)
            else:
                self.fields['citizen'].queryset = Citizen.objects.filter(current_status=Citizen.Status.ALIVE)
        else:
            self.fields['citizen'].queryset = Citizen.objects.filter(current_status=Citizen.Status.ALIVE)

    def clean(self):
        cleaned_data = super().clean()
        citizen = cleaned_data.get('citizen')
        death_date = cleaned_data.get('death_date')

        if citizen and death_date:
            if death_date > date.today():
                self.add_error('death_date', "Date of death cannot be in the future.")
            if death_date < citizen.dob:
                self.add_error('death_date', f"Date of death ({death_date}) cannot be before the citizen's birth date ({citizen.dob}).")

        return cleaned_data

from accounts.models import User
from registry.models import Region, Division, Subdivision


def _clean_email_unique(email):
    """Shared email uniqueness check for all user creation forms."""
    if email:
        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
    return email


def _get_or_create_division_subdivision(region, division_name, subdivision_name):
    """
    Look up or create a Division (case-insensitive) inside the given Region,
    then look up or create a Subdivision inside that Division.
    Returns (division, subdivision).
    """
    division_name = division_name.strip()
    subdivision_name = subdivision_name.strip()

    try:
        division = Division.objects.get(name__iexact=division_name, region=region)
    except Division.DoesNotExist:
        division = Division.objects.create(name=division_name, region=region)

    try:
        subdivision = Subdivision.objects.get(name__iexact=subdivision_name, division=division)
    except Subdivision.DoesNotExist:
        subdivision = Subdivision.objects.create(name=subdivision_name, division=division)

    return division, subdivision


class RegionalAdminCreationForm(forms.ModelForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'region', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'region': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show the 10 Cameroon regions, ordered alphabetically
        self.fields['region'].queryset = Region.objects.all().order_by('name')
        self.fields['region'].empty_label = "— Select Region —"

    def clean_email(self):
        return _clean_email_unique(self.cleaned_data.get('email'))


class CouncilOfficerCreationForm(forms.Form):
    """
    Division and subdivision are free-text: if the named record does not exist
    it is created automatically. Super admins must also pick the region; regional
    admins have theirs applied from their own profile.
    """
    first_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    last_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    region = forms.ModelChoiceField(
        queryset=Region.objects.all().order_by('name'),
        required=False,
        empty_label="— Select Region —",
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Region",
    )
    division_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Mfoundi',
        }),
        label="Division",
    )
    subdivision_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Yaoundé I (Council name)',
        }),
        label="Subdivision (Council)",
    )

    def __init__(self, *args, **kwargs):
        self._creating_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self._creating_user and self._creating_user.is_regional_admin:
            # Regional admin's region is fixed — hide the picker
            del self.fields['region']

    def clean_email(self):
        return _clean_email_unique(self.cleaned_data.get('email'))

    def clean(self):
        cleaned = super().clean()
        # Super admin must select a region
        if not (self._creating_user and self._creating_user.is_regional_admin):
            if not cleaned.get('region'):
                self.add_error('region', "Please select a region.")
        return cleaned

    def save(self):
        data = self.cleaned_data
        region = (
            self._creating_user.region
            if self._creating_user and self._creating_user.is_regional_admin
            else data['region']
        )
        division, subdivision = _get_or_create_division_subdivision(
            region, data['division_name'], data['subdivision_name']
        )
        user = User(
            first_name=data['first_name'],
            last_name=data['last_name'],
            email=data['email'],
            region=region,
            division=division,
            subdivision=subdivision,
        )
        return user


class HospitalStaffCreationForm(forms.Form):
    """
    Division and subdivision are free-text: if the named record does not exist
    it is created automatically.
    """
    first_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    last_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    region = forms.ModelChoiceField(
        queryset=Region.objects.all().order_by('name'),
        required=False,
        empty_label="— Select Region —",
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Region",
    )
    division_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Wouri',
        }),
        label="Division",
    )
    subdivision_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Douala I',
        }),
        label="Subdivision (Council)",
    )
    hospital_name = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Yaoundé General Hospital',
        }),
    )
    hospital_type = forms.ChoiceField(
        choices=[('', '— Select Type —'), ('Government', 'Government'), ('Private', 'Private')],
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    dr_incharge = forms.CharField(
        max_length=150, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Dr. Marie Ngo',
        }),
        label="Doctor In Charge",
    )

    def __init__(self, *args, **kwargs):
        self._creating_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self._creating_user and self._creating_user.is_regional_admin:
            del self.fields['region']

    def clean_email(self):
        return _clean_email_unique(self.cleaned_data.get('email'))

    def clean(self):
        cleaned = super().clean()
        if not (self._creating_user and self._creating_user.is_regional_admin):
            if not cleaned.get('region'):
                self.add_error('region', "Please select a region.")
        return cleaned

    def save(self):
        data = self.cleaned_data
        region = (
            self._creating_user.region
            if self._creating_user and self._creating_user.is_regional_admin
            else data['region']
        )
        division, subdivision = _get_or_create_division_subdivision(
            region, data['division_name'], data['subdivision_name']
        )
        user = User(
            first_name=data['first_name'],
            last_name=data['last_name'],
            email=data['email'],
            region=region,
            division=division,
            subdivision=subdivision,
            hospital_name=data['hospital_name'],
            hospital_type=data['hospital_type'],
            dr_incharge=data['dr_incharge'],
        )
        return user
