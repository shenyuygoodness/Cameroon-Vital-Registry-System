from django import forms
from django.utils import timezone
from datetime import date
from registry.models import Citizen, BirthDeclaration, DeathDeclaration, get_hash

class CitizenForm(forms.ModelForm):
    class Meta:
        model = Citizen
        fields = ['first_name', 'last_name', 'dob', 'gender', 'photo', 'father', 'mother', 'national_id', 'subdivision']
        widgets = {
            'dob': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'photo': forms.FileInput(attrs={'class': 'form-control'}),
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

class RegionalAdminCreationForm(forms.ModelForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'region', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'region': forms.Select(attrs={'class': 'form-select', 'required': True}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip()
            if User.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError("An account with this email address already exists.")
        return email

class CouncilOfficerCreationForm(forms.ModelForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'division', 'subdivision', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'division': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'subdivision': forms.Select(attrs={'class': 'form-select', 'required': True}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and user.is_regional_admin and user.region:
            self.fields['division'].queryset = self.fields['division'].queryset.filter(region=user.region)
            self.fields['subdivision'].queryset = self.fields['subdivision'].queryset.filter(division__region=user.region)

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip()
            if User.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.division:
            instance.region = instance.division.region
        if commit:
            instance.save()
        return instance

class HospitalStaffCreationForm(forms.ModelForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'division', 'subdivision', 'hospital_name', 'hospital_type', 'dr_incharge', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'division': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'subdivision': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'hospital_name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'hospital_type': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'dr_incharge': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and user.is_regional_admin and user.region:
            self.fields['division'].queryset = self.fields['division'].queryset.filter(region=user.region)
            self.fields['subdivision'].queryset = self.fields['subdivision'].queryset.filter(division__region=user.region)

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip()
            if User.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.division:
            instance.region = instance.division.region
        if commit:
            instance.save()
        return instance
