from django.shortcuts import redirect, render
from django.contrib.auth import logout, login as auth_login
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.forms import SetPasswordForm
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.views.generic import View, TemplateView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from django.conf import settings
from accounts.forms import CustomAuthenticationForm
from accounts.models import User


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

@method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True), name='dispatch')
class CustomLoginView(LoginView):
    authentication_form = CustomAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        from registry.models import AuditLog
        AuditLog.objects.create(
            user=form.get_user(),
            action=AuditLog.Action.LOGIN,
            model_name='User',
            record_id=str(form.get_user().pk),
            details=f"Successful login for {form.get_user().username}.",
            ip_address=_get_client_ip(self.request),
        )
        return super().form_valid(form)


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


class DashboardRedirectView(LoginRequiredMixin, View):
    """
    Redirects users to their specific dashboard based on their RBAC role.
    """
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
            from django.contrib.auth import login as auth_login
            auth_login(request, form.get_user())
            return redirect('registry:dashboard')
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
