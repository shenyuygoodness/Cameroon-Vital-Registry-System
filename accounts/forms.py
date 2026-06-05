from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from accounts.models import User

class CustomAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label="System ID / Username", widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'e.g. REG-12345, COU-ABCDE',
        'id': 'id_username'
    }))
    email = forms.EmailField(label="Email Address", widget=forms.EmailInput(attrs={
        'class': 'form-control',
        'placeholder': 'e.g. user@example.com',
        'id': 'id_email'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control',
        'placeholder': 'Enter your password',
        'id': 'id_password'
    }))

    def clean(self):
        username = self.cleaned_data.get("username")
        email = self.cleaned_data.get("email")
        password = self.cleaned_data.get("password")

        if username and email and password:
            # Normalize before lookup to prevent bypass via leading/trailing whitespace
            email = email.strip().lower()
            try:
                user = User.objects.get(username=username, email__iexact=email)
            except User.DoesNotExist:
                raise forms.ValidationError(
                    "Invalid credentials. Please verify your System ID and Email.",
                    code="invalid_login"
                )

            if not user.is_active:
                raise forms.ValidationError(
                    "This account has not been activated yet. "
                    "Please check your email for the activation link.",
                    code="inactive"
                )

            from django.contrib.auth import authenticate
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise forms.ValidationError(
                    "Invalid credentials. Please verify your System ID and Email.",
                    code="invalid_login"
                )

            if self.user_cache.role == User.Role.CITIZEN:
                raise forms.ValidationError(
                    "The citizen portal is not yet available. "
                    "Please contact your local council office for assistance.",
                    code="citizen_portal_unavailable"
                )

            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'role', 'region', 'division', 'subdivision', 'hospital_name')
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select'}),
            'region': forms.Select(attrs={'class': 'form-select'}),
            'division': forms.Select(attrs={'class': 'form-select'}),
            'subdivision': forms.Select(attrs={'class': 'form-select'}),
            'hospital_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Yaoundé General Hospital'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class MFAVerificationForm(forms.Form):
    code = forms.CharField(
        label="Verification Code",
        min_length=9,
        max_length=9,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'placeholder': '— — — — — — — — —',
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]{9}',
            'id': 'id_code',
        }),
    )

    def clean_code(self):
        code = self.cleaned_data.get('code', '').strip()
        if not code.isdigit():
            raise forms.ValidationError("Verification code must contain digits only.")
        return code
