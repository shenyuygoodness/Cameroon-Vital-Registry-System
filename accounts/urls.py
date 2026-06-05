from django.urls import path
from django.contrib.auth import views as auth_views
from accounts.views import (
    CustomLoginView, CustomLogoutView, DashboardRedirectView,
    UserProfileView, CustomPasswordChangeView, ActivateAccountView,
    MFAVerifyView, MFAResendView,
)

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('mfa/verify/', MFAVerifyView.as_view(), name='mfa_verify'),
    path('mfa/resend/', MFAResendView.as_view(), name='mfa_resend'),
    path('dashboard/', DashboardRedirectView.as_view(), name='dashboard_redirect'),
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('profile/password/', CustomPasswordChangeView.as_view(), name='password_change'),
    path('activate/<uidb64>/<token>/', ActivateAccountView.as_view(), name='activate_account'),

    # Password reset flow (for forgotten passwords)
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset_form.html',
        email_template_name='accounts/email/password_reset_email.html',
        subject_template_name='accounts/email/password_reset_subject.txt',
        html_email_template_name='accounts/email/password_reset_email.html',
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html',
    ), name='password_reset_complete'),
]
