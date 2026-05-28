from django.urls import path
from accounts.views import CustomLoginView, CustomLogoutView, DashboardRedirectView, UserProfileView, CustomPasswordChangeView

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('dashboard/', DashboardRedirectView.as_view(), name='dashboard_redirect'),
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('profile/password/', CustomPasswordChangeView.as_view(), name='password_change'),
]
