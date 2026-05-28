from django.shortcuts import redirect, render
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.views.generic import View, TemplateView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from accounts.forms import CustomAuthenticationForm

@method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True), name='dispatch')
class CustomLoginView(LoginView):
    authentication_form = CustomAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True


class CustomLogoutView(View):
    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect('login')

    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect('login')


class DashboardRedirectView(LoginRequiredMixin, View):
    """
    Redirects users to their specific dashboard based on their RBAC role.
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        # All role dashboards are rendered by registry views, which inspect the request.user.
        # So we can redirect them all to the core registry dashboard.
        return redirect('registry:dashboard')

class UserProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'accounts/profile.html'

class LandingPageView(View):
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('registry:dashboard')
        return render(request, 'landing.html')

class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('profile')
    
    def form_valid(self, form):
        messages.success(self.request, "Your password was successfully updated! Please log in again if required.")
        return super().form_valid(form)
