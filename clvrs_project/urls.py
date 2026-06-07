"""
URL configuration for clvrs_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse
from accounts.views import DashboardRedirectView, LandingPageView
from django.conf import settings
from django.conf.urls.static import static


def security_txt(request):
    content = (
        "Contact: mailto:security@clvrs.cm\n"
        "Preferred-Languages: en, fr\n"
        "Policy: https://clvrs.cm/security-policy\n"
    )
    return HttpResponse(content, content_type='text/plain')


def health_check(request):
    return HttpResponse("ok", status=200, content_type='text/plain')


urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('clvrs-admin-portal/', admin.site.urls),
    path('.well-known/security.txt', security_txt),
    path('i18n/', include('django.conf.urls.i18n')),
    path('', include('accounts.urls')),
    path('', LandingPageView.as_view(), name='home'),
    path('registry/', include('registry.urls', namespace='registry')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

