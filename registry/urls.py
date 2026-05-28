from django.urls import path
from registry.views import (
    UnifiedDashboardView, CitizenListView, CitizenDetailView,
    CitizenCreateView, CitizenUpdateView, CitizenDeleteView,
    BirthDeclarationCreateView, DeathDeclarationCreateView,
    PendingDeclarationsListView, BirthDeclarationReviewView,
    DeathDeclarationReviewView, BirthCertificatePDFView,
    DeathCertificatePDFView, BulkImportView, DownloadTemplateView,
    ExportCSVView, SecureMediaView, UserManagementDashboardView
)

app_name = 'registry'

urlpatterns = [
    # Dashboard
    path('', UnifiedDashboardView.as_view(), name='dashboard'),
    
    # Citizen CRUD
    path('citizens/', CitizenListView.as_view(), name='citizen_list'),
    path('citizens/<int:pk>/', CitizenDetailView.as_view(), name='citizen_detail'),
    path('citizens/new/', CitizenCreateView.as_view(), name='citizen_create'),
    path('citizens/<int:pk>/edit/', CitizenUpdateView.as_view(), name='citizen_edit'),
    path('citizens/<int:pk>/delete/', CitizenDeleteView.as_view(), name='citizen_delete'),
    
    # Declarations
    path('declarations/birth/new/', BirthDeclarationCreateView.as_view(), name='birth_create'),
    path('declarations/death/new/', DeathDeclarationCreateView.as_view(), name='death_create'),
    path('declarations/pending/', PendingDeclarationsListView.as_view(), name='pending_list'),
    path('declarations/birth/<int:pk>/review/', BirthDeclarationReviewView.as_view(), name='birth_review'),
    path('declarations/death/<int:pk>/review/', DeathDeclarationReviewView.as_view(), name='death_review'),
    
    # Certificates PDF
    path('certificates/birth/<int:pk>/pdf/', BirthCertificatePDFView.as_view(), name='birth_certificate_pdf'),
    path('certificates/death/<int:pk>/pdf/', DeathCertificatePDFView.as_view(), name='death_certificate_pdf'),
    
    # Bulk Ops
    path('bulk/import/', BulkImportView.as_view(), name='bulk_import'),
    path('bulk/template/', DownloadTemplateView.as_view(), name='download_template'),
    path('bulk/export/', ExportCSVView.as_view(), name='export_csv'),
    
    # Secure Media
    path('secure-media/<path:path>', SecureMediaView.as_view(), name='secure_media'),
    
    # User Management
    path('users/', UserManagementDashboardView.as_view(), name='user_management'),
]
