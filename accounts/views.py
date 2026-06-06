import logging

from django.shortcuts import redirect, render
from django.contrib.auth import logout, login as auth_login
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.forms import SetPasswordForm
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.views.generic import View, TemplateView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from accounts.forms import CustomAuthenticationForm, MFAVerificationForm
from accounts.models import User, MFAToken

logger = logging.getLogger('accounts')


def _get_client_ip(request):
    """
    Return the real client IP address.
    X-Forwarded-For is only trusted when the direct TCP connection originates
    from a known proxy listed in settings.TRUSTED_PROXIES — otherwise it is
    trivially spoofable and would let attackers bypass rate-limiting.
    """
    remote_addr = request.META.get('REMOTE_ADDR', '')
    trusted_proxies = getattr(settings, 'TRUSTED_PROXIES', set())
    if remote_addr in trusted_proxies:
        forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
    return remote_addr


def _send_mfa_email(user, otp):
    """Send OTP to the user's registered email address."""
    subject = "Your CLVRS Login Verification Code"
    html_body = render_to_string('accounts/email/mfa_otp.html', {
        'user': user,
        'otp': otp,
        'expiry_minutes': getattr(settings, 'MFA_OTP_EXPIRY_SECONDS', 600) // 60,
    })
    send_mail(
        subject=subject,
        message=f"Your CLVRS verification code is: {otp}\nIt expires in "
                f"{getattr(settings, 'MFA_OTP_EXPIRY_SECONDS', 600) // 60} minutes.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=False,
    )


def _initiate_mfa(request, user):
    """
    Generate an OTP, store the pending user ID in the session (the user is NOT
    yet authenticated at this point), attempt to email the code, and redirect
    to the verification page.  The redirect happens regardless of whether the
    email succeeds — if it failed the user can hit 'Resend Code' on the next page.
    """
    otp, _token = MFAToken.generate_for_user(user)
    request.session['mfa_pending_user_id'] = user.pk
    request.session['mfa_pending_backend'] = 'django.contrib.auth.backends.ModelBackend'

    if not user.email:
        logger.error("MFA email skipped — user pk=%s has no email address.", user.pk)
        messages.warning(
            request,
            "Your account has no email address on file. "
            "Please contact an administrator to complete login.",
        )
    else:
        try:
            _send_mfa_email(user, otp)
        except Exception:
            logger.exception(
                "MFA email delivery failed for user pk=%s (address: %s).",
                user.pk, user.email,
            )
            messages.warning(
                request,
                "We could not deliver the verification code right now. "
                "Please use the 'Resend Code' button on the next page, "
                "or contact an administrator if the problem persists.",
            )

    return redirect(reverse('mfa_verify'))


@method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True), name='dispatch')
class CustomLoginView(LoginView):
    authentication_form = CustomAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        """Credentials are valid — begin MFA instead of completing login."""
        return _initiate_mfa(self.request, form.get_user())


class CustomLogoutView(View):
    def _do_logout(self, request):
        from registry.models import AuditLog
        if request.user.is_authenticated:
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.Action.LOGOUT,
                model_name='User',
                record_id=str(request.user.pk),
                details=f"User {request.user.username} logged out.",
                ip_address=_get_client_ip(request),
            )
        logout(request)

    def get(self, request, *args, **kwargs):
        self._do_logout(request)
        return redirect('login')

    def post(self, request, *args, **kwargs):
        self._do_logout(request)
        return redirect('login')


@method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True), name='dispatch')
class MFAVerifyView(View):
    template_name = 'accounts/mfa_verify.html'

    def _get_pending_user(self, request):
        user_id = request.session.get('mfa_pending_user_id')
        if not user_id:
            return None
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('registry:dashboard')
        if not request.session.get('mfa_pending_user_id'):
            return redirect('login')
        return render(request, self.template_name, {'form': MFAVerificationForm()})

    def post(self, request, *args, **kwargs):
        from registry.models import AuditLog
        if request.user.is_authenticated:
            return redirect('registry:dashboard')

        user = self._get_pending_user(request)
        if not user:
            messages.error(request, "Session expired. Please log in again.")
            return redirect('login')

        form = MFAVerificationForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        code = form.cleaned_data['code']
        token = (
            MFAToken.objects
            .filter(user=user, is_used=False)
            .order_by('-created_at')
            .first()
        )

        if token and token.verify(code):
            # Clear MFA session keys before completing login
            request.session.pop('mfa_pending_user_id', None)
            request.session.pop('mfa_pending_backend', None)

            backend = 'django.contrib.auth.backends.ModelBackend'
            auth_login(request, user, backend=backend)

            AuditLog.objects.create(
                user=user,
                action=AuditLog.Action.LOGIN,
                model_name='User',
                record_id=str(user.pk),
                details=f"Successful login with MFA for {user.username}.",
                ip_address=_get_client_ip(request),
            )
            AuditLog.objects.create(
                user=user,
                action=AuditLog.Action.MFA_VERIFY,
                model_name='User',
                record_id=str(user.pk),
                details=f"MFA code verified for {user.username}.",
                ip_address=_get_client_ip(request),
            )
            return redirect('registry:dashboard')

        # OTP wrong or expired
        AuditLog.objects.create(
            user=user,
            action=AuditLog.Action.MFA_FAILED,
            model_name='User',
            record_id=str(user.pk),
            details=f"Failed MFA attempt for {user.username}.",
            ip_address=_get_client_ip(request),
        )
        form.add_error('code', "Invalid or expired verification code. Please try again or request a new code.")
        return render(request, self.template_name, {'form': form})


@method_decorator(ratelimit(key='ip', rate='3/m', method='POST', block=True), name='dispatch')
class MFAResendView(View):
    """Resend a fresh OTP to the user who is mid-login."""

    def post(self, request, *args, **kwargs):
        user_id = request.session.get('mfa_pending_user_id')
        if not user_id:
            messages.error(request, "Session expired. Please log in again.")
            return redirect('login')
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            messages.error(request, "Session expired. Please log in again.")
            return redirect('login')

        otp, _token = MFAToken.generate_for_user(user)
        _send_mfa_email(user, otp)
        messages.success(request, "A new verification code has been sent to your email.")
        return redirect(reverse('mfa_verify'))


class DashboardRedirectView(LoginRequiredMixin, View):
    """Redirects users to their specific dashboard based on their RBAC role."""
    def get(self, request, *args, **kwargs):
        if request.user.role == User.Role.CITIZEN:
            logout(request)
            messages.info(request, "The citizen portal is not yet available. Please check back later.")
            return redirect('login')
        return redirect('registry:dashboard')


class UserProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/profile.html'


@method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True), name='dispatch')
class LandingPageView(View):
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('registry:dashboard')
        return render(request, 'landing.html', {'form': CustomAuthenticationForm(request)})

    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('registry:dashboard')
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            return _initiate_mfa(request, form.get_user())
        return render(request, 'landing.html', {'form': form})


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('profile')

    def form_valid(self, form):
        messages.success(self.request, "Your password was successfully updated! Please log in again if required.")
        return super().form_valid(form)


class ActivateAccountView(View):
    def _get_user(self, uidb64):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return None

    def get(self, request, uidb64, token):
        user = self._get_user(uidb64)
        token_generator = PasswordResetTokenGenerator()

        if user is None or user.is_active or not token_generator.check_token(user, token):
            return render(request, 'accounts/activate.html', {'invalid': True})

        form = SetPasswordForm(user=user)
        return render(request, 'accounts/activate.html', {
            'form': form,
            'uidb64': uidb64,
            'token': token,
            'system_id': user.username,
            'role': user.get_role_display(),
        })

    def post(self, request, uidb64, token):
        user = self._get_user(uidb64)
        token_generator = PasswordResetTokenGenerator()

        if user is None or user.is_active or not token_generator.check_token(user, token):
            return render(request, 'accounts/activate.html', {'invalid': True})

        form = SetPasswordForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()
            user.is_active = True
            user.save(update_fields=['is_active'])
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, f"Welcome, {user.get_full_name() or user.username}! Your account is now active.")
            return redirect('registry:dashboard')

        return render(request, 'accounts/activate.html', {
            'form': form,
            'uidb64': uidb64,
            'token': token,
            'system_id': user.username,
            'role': user.get_role_display(),
        })
