import os
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.http import HttpResponse, FileResponse, HttpResponseForbidden, Http404
from django.core.exceptions import ValidationError
from django.db import transaction, models
from django.utils import timezone
from django.contrib import messages
from django.utils._os import safe_join
from django.conf import settings
from datetime import date, datetime
import pandas as pd
from io import BytesIO

from accounts.models import User
from registry.models import (
    Region, Division, Subdivision, Citizen, Household, HouseholdMember,
    BirthDeclaration, DeathDeclaration, AuditLog
)
from registry.forms import CitizenForm, BirthDeclarationForm, DeathDeclarationForm
from registry.services import (
    import_citizens_from_excel, generate_excel_template,
    generate_birth_certificate_pdf, generate_death_certificate_pdf, get_hash
)

# -------------------------------------------------------------
# 1. Jurisdiction Permission Access Mixins
# -------------------------------------------------------------
class SuperAdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_super_admin

class CouncilOfficerRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.is_super_admin or self.request.user.is_council_officer
        )

class HospitalStaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.is_super_admin or self.request.user.is_hospital_staff
        )

class RegionalAdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.is_super_admin or self.request.user.is_regional_admin
        )

# Helper to filter records by user's assigned jurisdiction
def get_visible_citizens_qs(user):
    if user.is_super_admin:
        return Citizen.objects.all()
    elif user.is_council_officer:
        return Citizen.objects.filter(subdivision=user.subdivision)
    elif user.is_citizen and user.citizen_profile:
        return Citizen.objects.filter(id=user.citizen_profile.id)
    return Citizen.objects.none()

def get_visible_births_qs(user):
    if user.is_super_admin:
        return BirthDeclaration.objects.all()
    elif user.is_council_officer or user.is_hospital_staff:
        return BirthDeclaration.objects.filter(subdivision=user.subdivision)
    elif user.is_citizen and user.citizen_profile:
        nic = user.citizen_profile.national_id
        # Citizen can view birth declaration if it is theirs or they are parents
        q_filter = models.Q(associated_citizen=user.citizen_profile)
        if nic:
            q_filter |= models.Q(mother_national_id=nic) | models.Q(father_national_id=nic)
        return BirthDeclaration.objects.filter(q_filter)
    return BirthDeclaration.objects.none()

def get_visible_deaths_qs(user):
    if user.is_super_admin:
        return DeathDeclaration.objects.all()
    elif user.is_council_officer or user.is_hospital_staff:
        return DeathDeclaration.objects.filter(subdivision=user.subdivision)
    elif user.is_citizen and user.citizen_profile:
        # Citizen can view death certificate of self or household members
        q_filter = models.Q(citizen=user.citizen_profile)
        if hasattr(user.citizen_profile, 'household_membership'):
            household = user.citizen_profile.household_membership.household
            q_filter |= models.Q(citizen__household_membership__household=household)
        return DeathDeclaration.objects.filter(q_filter)
    return DeathDeclaration.objects.none()


# -------------------------------------------------------------
# 2. Dashboards View
# -------------------------------------------------------------
class UnifiedDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'registry/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role = user.role

        if user.is_super_admin:
            # Global aggregates
            context['total_citizens'] = Citizen.objects.count()
            context['total_births_pending'] = BirthDeclaration.objects.filter(status=BirthDeclaration.Status.PENDING).count()
            context['total_deaths_pending'] = DeathDeclaration.objects.filter(status=DeathDeclaration.Status.PENDING).count()
            context['total_households'] = Household.objects.count()
            # Charts preparation (Subdivision aggregates)
            subdivisions = Subdivision.objects.annotate(citizen_count=models.Count('citizens')).order_by('-citizen_count')[:5]
            context['chart_labels'] = [s.name for s in subdivisions]
            context['chart_data'] = [s.citizen_count for s in subdivisions]
            context['recent_logs'] = AuditLog.objects.all()[:10]

        elif role == User.Role.REGIONAL_ADMIN:
            # Enforce view-only aggregate stats for assigned region. No individual records!
            region = user.region
            context['region'] = region
            if region:
                # Subdivisions in this region
                sub_qs = Subdivision.objects.filter(division__region=region)
                context['total_citizens'] = Citizen.objects.filter(subdivision__in=sub_qs).count()
                context['total_births_pending'] = BirthDeclaration.objects.filter(subdivision__in=sub_qs, status=BirthDeclaration.Status.PENDING).count()
                context['total_deaths_pending'] = DeathDeclaration.objects.filter(subdivision__in=sub_qs, status=DeathDeclaration.Status.PENDING).count()
                context['total_households'] = Household.objects.filter(subdivision__in=sub_qs).count()
                
                # Chart: Subdivision distribution
                subdivisions = sub_qs.annotate(citizen_count=models.Count('citizens')).order_by('-citizen_count')[:10]
                context['chart_labels'] = [s.name for s in subdivisions]
                context['chart_data'] = [s.citizen_count for s in subdivisions]
            else:
                context['total_citizens'] = 0

        elif role == User.Role.COUNCIL_OFFICER:
            # Subdivision aggregates
            sub = user.subdivision
            context['subdivision'] = sub
            if sub:
                context['total_citizens'] = Citizen.objects.filter(subdivision=sub).count()
                context['total_births_pending'] = BirthDeclaration.objects.filter(subdivision=sub, status=BirthDeclaration.Status.PENDING).count()
                context['total_deaths_pending'] = DeathDeclaration.objects.filter(subdivision=sub, status=DeathDeclaration.Status.PENDING).count()
                context['total_households'] = Household.objects.filter(subdivision=sub).count()
                
                # Recent births & deaths
                context['pending_births'] = BirthDeclaration.objects.filter(subdivision=sub, status=BirthDeclaration.Status.PENDING)[:5]
                context['pending_deaths'] = DeathDeclaration.objects.filter(subdivision=sub, status=DeathDeclaration.Status.PENDING)[:5]
                context['recent_citizens'] = Citizen.objects.filter(subdivision=sub)[:5]

        elif role == User.Role.HOSPITAL_STAFF:
            # Hospital staff submissions
            sub = user.subdivision
            context['subdivision'] = sub
            context['hospital_name'] = user.hospital_name
            context['my_birth_declarations'] = BirthDeclaration.objects.filter(created_by=user).order_by('-created_at')[:5]
            context['my_death_declarations'] = DeathDeclaration.objects.filter(created_by=user).order_by('-created_at')[:5]

        elif role == User.Role.CITIZEN:
            # Citizen dashboard
            citizen = user.citizen_profile
            context['citizen'] = citizen
            if citizen:
                # Parents
                context['father'] = citizen.father
                context['mother'] = citizen.mother
                
                # Household members
                if hasattr(citizen, 'household_membership'):
                    context['household'] = citizen.household_membership.household
                    context['household_members'] = citizen.household_membership.household.members.all().exclude(citizen=citizen)
                
                # Children birth certificates (if any)
                nic = citizen.national_id
                q_children = models.Q(father=citizen) | models.Q(mother=citizen)
                context['children'] = Citizen.objects.filter(q_children)
                
                # My own birth certificate
                context['birth_declaration'] = BirthDeclaration.objects.filter(associated_citizen=citizen, status=BirthDeclaration.Status.CONFIRMED).first()

        return context


# -------------------------------------------------------------
# 3. Citizen CRUD (Council Officer / Super Admin only)
# -------------------------------------------------------------
class CitizenListView(CouncilOfficerRequiredMixin, ListView):
    model = Citizen
    template_name = 'registry/citizen_list.html'
    context_object_name = 'citizens'
    paginate_by = 20

    def get_queryset(self):
        qs = get_visible_citizens_qs(self.request.user)
        # Search & Filter parameters
        search_query = self.request.GET.get('search')
        gender = self.request.GET.get('gender')
        status = self.request.GET.get('status')
        subdivision = self.request.GET.get('subdivision')

        if search_query:
            # Search by first name, last name or exact NIC (hashed query)
            nic_hash = get_hash(search_query)
            qs = qs.filter(
                models.Q(first_name__icontains=search_query) |
                models.Q(last_name__icontains=search_query) |
                models.Q(national_id_hash=nic_hash)
            )
        if gender:
            qs = qs.filter(gender=gender)
        if status:
            qs = qs.filter(current_status=status)
        if subdivision and self.request.user.is_super_admin:
            qs = qs.filter(subdivision_id=subdivision)
            
        return qs.select_related('subdivision')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_super_admin:
            context['subdivisions'] = Subdivision.objects.all()
        return context


class CitizenDetailView(LoginRequiredMixin, DetailView):
    model = Citizen
    template_name = 'registry/citizen_detail.html'
    context_object_name = 'citizen'

    def get_queryset(self):
        # Prevent access if not matching jurisdiction or not self
        return get_visible_citizens_qs(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        citizen = self.get_object()
        
        # Pull household details
        if hasattr(citizen, 'household_membership'):
            context['household'] = citizen.household_membership.household
            context['household_members'] = citizen.household_membership.household.members.all()
            
        # Get audit logs (only for admins)
        if self.request.user.is_super_admin or self.request.user.is_council_officer:
            context['audit_logs'] = AuditLog.objects.filter(model_name="Citizen", record_id=str(citizen.id))
            
        return context


class CitizenCreateView(CouncilOfficerRequiredMixin, CreateView):
    model = Citizen
    form_class = CitizenForm
    template_name = 'registry/citizen_form.html'
    success_url = reverse_lazy('registry:citizen_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        # Force subdivision for Council Officer
        if not self.request.user.is_super_admin:
            form.instance.subdivision = self.request.user.subdivision
            
        response = super().form_valid(form)
        
        # Log action
        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            model_name="Citizen",
            record_id=str(self.object.id),
            details=f"Created citizen: {self.object.get_full_name()} (NIC Hashed: {self.object.national_id_hash or 'None'}).",
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        messages.success(self.request, f"Citizen {self.object.get_full_name()} registered successfully.")
        return response


class CitizenUpdateView(CouncilOfficerRequiredMixin, UpdateView):
    model = Citizen
    form_class = CitizenForm
    template_name = 'registry/citizen_form.html'

    def get_queryset(self):
        return get_visible_citizens_qs(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse_lazy('registry:citizen_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        # Log action
        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            model_name="Citizen",
            record_id=str(self.object.id),
            details=f"Updated details for citizen: {self.object.get_full_name()}.",
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        messages.success(self.request, f"Citizen {self.object.get_full_name()} updated successfully.")
        return response


class CitizenDeleteView(CouncilOfficerRequiredMixin, View):
    """
    Soft deletes a citizen record to preserve integrity.
    """
    def post(self, request, *args, **kwargs):
        citizen = get_object_or_404(get_visible_citizens_qs(request.user), pk=kwargs.get('pk'))
        citizen.soft_delete()
        
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.DELETE,
            model_name="Citizen",
            record_id=str(citizen.id),
            details=f"Soft deleted citizen: {citizen.get_full_name()}.",
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.warning(request, f"Citizen {citizen.get_full_name()} has been soft-deleted.")
        return redirect('registry:citizen_list')


# -------------------------------------------------------------
# 4. Declarations & Workflow approvals
# -------------------------------------------------------------
class BirthDeclarationCreateView(LoginRequiredMixin, CreateView):
    model = BirthDeclaration
    form_class = BirthDeclarationForm
    template_name = 'registry/declaration_form.html'
    success_url = reverse_lazy('registry:dashboard')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        if not self.request.user.is_super_admin:
            form.instance.subdivision = self.request.user.subdivision
            
        response = super().form_valid(form)
        
        # Audit log
        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            model_name="BirthDeclaration",
            record_id=str(self.object.id),
            details=f"Submitted pending birth declaration {self.object.declaration_number}.",
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        messages.success(self.request, f"Birth declaration {self.object.declaration_number} submitted and is PENDING review.")
        return response


class DeathDeclarationCreateView(LoginRequiredMixin, CreateView):
    model = DeathDeclaration
    form_class = DeathDeclarationForm
    template_name = 'registry/declaration_form.html'
    success_url = reverse_lazy('registry:dashboard')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        if not self.request.user.is_super_admin:
            form.instance.subdivision = self.request.user.subdivision
            
        response = super().form_valid(form)
        
        # Audit log
        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            model_name="DeathDeclaration",
            record_id=str(self.object.id),
            details=f"Submitted pending death declaration {self.object.declaration_number}.",
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        messages.success(self.request, f"Death declaration {self.object.declaration_number} submitted and is PENDING review.")
        return response


class PendingDeclarationsListView(CouncilOfficerRequiredMixin, TemplateView):
    template_name = 'registry/pending_declarations.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Filter pending by subdivision
        context['pending_births'] = get_visible_births_qs(user).filter(status=BirthDeclaration.Status.PENDING)
        context['pending_deaths'] = get_visible_deaths_qs(user).filter(status=DeathDeclaration.Status.PENDING)
        return context


class BirthDeclarationReviewView(CouncilOfficerRequiredMixin, View):
    """
    Council Officer workflow to approve/confirm or reject a Birth Declaration.
    """
    def post(self, request, *args, **kwargs):
        declaration = get_object_or_404(get_visible_births_qs(request.user), pk=kwargs.get('pk'))
        action = request.POST.get('action') # 'confirm' or 'reject'
        reason = request.POST.get('rejection_reason', '').strip()

        if declaration.status != BirthDeclaration.Status.PENDING:
            messages.error(request, "This declaration has already been reviewed.")
            return redirect('registry:pending_list')

        if action == 'reject':
            if not reason:
                messages.error(request, "Rejection reason is required to reject a declaration.")
                return redirect('registry:pending_list')
                
            declaration.status = BirthDeclaration.Status.REJECTED
            declaration.rejection_reason = reason
            declaration.reviewed_by = request.user
            declaration.save()
            
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.Action.REJECT,
                model_name="BirthDeclaration",
                record_id=str(declaration.id),
                details=f"Rejected birth declaration {declaration.declaration_number}. Reason: {reason}",
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.warning(request, f"Birth declaration {declaration.declaration_number} has been rejected.")
            
        elif action == 'confirm':
            # Check for name updates from the council officer
            updated_first_name = request.POST.get('child_first_name')
            updated_last_name = request.POST.get('child_last_name')
            
            if updated_first_name is not None:
                declaration.child_first_name = updated_first_name.strip()
            if updated_last_name is not None:
                declaration.child_last_name = updated_last_name.strip()

            # Begin atomic transaction to build Citizen record
            try:
                with transaction.atomic():
                    # Resolve parent links
                    father = None
                    mother = None
                    if declaration.father_national_id:
                        father = Citizen.objects.filter(national_id_hash=get_hash(declaration.father_national_id)).first()
                    if declaration.mother_national_id:
                        mother = Citizen.objects.filter(national_id_hash=get_hash(declaration.mother_national_id)).first()

                    # Create Citizen
                    citizen = Citizen.objects.create(
                        first_name=declaration.child_first_name,
                        last_name=declaration.child_last_name,
                        dob=declaration.child_dob,
                        gender=declaration.child_gender,
                        father=father,
                        mother=mother,
                        subdivision=declaration.subdivision,
                        current_status=Citizen.Status.ALIVE
                    )

                    # Update declaration
                    declaration.status = BirthDeclaration.Status.CONFIRMED
                    declaration.confirmed_at = timezone.now()
                    declaration.reviewed_by = request.user
                    declaration.associated_citizen = citizen
                    declaration.save()

                    # Audit log
                    AuditLog.objects.create(
                        user=request.user,
                        action=AuditLog.Action.APPROVE,
                        model_name="BirthDeclaration",
                        record_id=str(declaration.id),
                        details=f"Confirmed birth declaration {declaration.declaration_number}. Created Citizen profile ID {citizen.id}.",
                        ip_address=request.META.get('REMOTE_ADDR')
                    )
                messages.success(request, f"Birth declaration approved. Citizen profile created for {citizen.get_full_name()}.")
            except Exception as e:
                messages.error(request, f"An error occurred during verification: {str(e)}")
                
        return redirect('registry:pending_list')


class DeathDeclarationReviewView(CouncilOfficerRequiredMixin, View):
    """
    Council Officer workflow to approve/confirm or reject a Death Declaration.
    """
    def post(self, request, *args, **kwargs):
        declaration = get_object_or_404(get_visible_deaths_qs(request.user), pk=kwargs.get('pk'))
        action = request.POST.get('action') # 'confirm' or 'reject'
        reason = request.POST.get('rejection_reason', '').strip()

        if declaration.status != DeathDeclaration.Status.PENDING:
            messages.error(request, "This declaration has already been reviewed.")
            return redirect('registry:pending_list')

        if action == 'reject':
            if not reason:
                messages.error(request, "Rejection reason is required to reject a declaration.")
                return redirect('registry:pending_list')
                
            declaration.status = DeathDeclaration.Status.REJECTED
            declaration.rejection_reason = reason
            declaration.reviewed_by = request.user
            declaration.save()
            
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.Action.REJECT,
                model_name="DeathDeclaration",
                record_id=str(declaration.id),
                details=f"Rejected death declaration {declaration.declaration_number}. Reason: {reason}",
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.warning(request, f"Death declaration {declaration.declaration_number} has been rejected.")
            
        elif action == 'confirm':
            try:
                with transaction.atomic():
                    # Update associated Citizen status
                    citizen = declaration.citizen
                    citizen.current_status = Citizen.Status.DECEASED
                    citizen.death_date = declaration.death_date
                    citizen.save()

                    # Update declaration
                    declaration.status = DeathDeclaration.Status.CONFIRMED
                    declaration.confirmed_at = timezone.now()
                    declaration.reviewed_by = request.user
                    declaration.save()

                    # Audit log
                    AuditLog.objects.create(
                        user=request.user,
                        action=AuditLog.Action.APPROVE,
                        model_name="DeathDeclaration",
                        record_id=str(declaration.id),
                        details=f"Confirmed death declaration {declaration.declaration_number}. Updated Citizen {citizen.get_full_name()} status to DECEASED.",
                        ip_address=request.META.get('REMOTE_ADDR')
                    )
                messages.success(request, f"Death declaration approved. Citizen {citizen.get_full_name()} record updated to Deceased.")
            except Exception as e:
                messages.error(request, f"An error occurred during verification: {str(e)}")

        return redirect('registry:pending_list')


# -------------------------------------------------------------
# 5. Certificate PDF Streaming Views
# -------------------------------------------------------------
class BirthCertificatePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        declaration = get_object_or_404(get_visible_births_qs(request.user), pk=kwargs.get('pk'))
        if declaration.status != BirthDeclaration.Status.CONFIRMED:
            return HttpResponseForbidden("Certificate is not available. The declaration must be confirmed first.")
            
        pdf_buffer = generate_birth_certificate_pdf(declaration)
        
        # Log download
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.EXPORT,
            model_name="BirthCertificate",
            record_id=str(declaration.id),
            details=f"Downloaded birth certificate for declaration {declaration.declaration_number}.",
            ip_address=request.META.get('REMOTE_ADDR')
        )
        
        filename = f"Birth_Certificate_{declaration.declaration_number}.pdf"
        return FileResponse(pdf_buffer, as_attachment=False, content_type='application/pdf', filename=filename)


class DeathCertificatePDFView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        declaration = get_object_or_404(get_visible_deaths_qs(request.user), pk=kwargs.get('pk'))
        if declaration.status != DeathDeclaration.Status.CONFIRMED:
            return HttpResponseForbidden("Certificate is not available. The declaration must be confirmed first.")
            
        pdf_buffer = generate_death_certificate_pdf(declaration)
        
        # Log download
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.EXPORT,
            model_name="DeathCertificate",
            record_id=str(declaration.id),
            details=f"Downloaded death certificate for declaration {declaration.declaration_number}.",
            ip_address=request.META.get('REMOTE_ADDR')
        )
        
        filename = f"Death_Certificate_{declaration.declaration_number}.pdf"
        return FileResponse(pdf_buffer, as_attachment=False, content_type='application/pdf', filename=filename)


# -------------------------------------------------------------
# 6. Bulk Operations Views (pandas based Excel handlers)
# -------------------------------------------------------------
class BulkImportView(CouncilOfficerRequiredMixin, TemplateView):
    template_name = 'registry/bulk_import.html'

    def post(self, request, *args, **kwargs):
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            messages.error(request, "Please select an Excel or CSV file to upload.")
            return render(request, self.template_name)

        subdivision = request.user.subdivision
        if not subdivision and not request.user.is_super_admin:
            messages.error(request, "Your account is not linked to any Subdivision (Council). Upload denied.")
            return render(request, self.template_name)

        # In case of Super Admin, they select subdivision from the form
        if request.user.is_super_admin:
            sub_id = request.POST.get('subdivision')
            if not sub_id:
                messages.error(request, "Super Admins must choose a Subdivision to import into.")
                context = self.get_context_data()
                return render(request, self.template_name, context)
            subdivision = Subdivision.objects.get(id=sub_id)

        try:
            records_created = import_citizens_from_excel(excel_file, subdivision, request.user)
            messages.success(request, f"Bulk upload completed! Successfully registered {records_created} citizens.")
            return redirect('registry:citizen_list')
        except ValidationError as e:
            # Handle specific lines validation list
            context = self.get_context_data()
            context['validation_errors'] = e.messages
            messages.error(request, "Bulk upload failed due to validation errors. Database changes rolled back.")
            return render(request, self.template_name, context)
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {str(e)}")
            return render(request, self.template_name)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_super_admin:
            context['subdivisions'] = Subdivision.objects.all()
        return context


class DownloadTemplateView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        template_buffer = generate_excel_template()
        filename = "CLVRS_Citizens_Import_Template.xlsx"
        return FileResponse(template_buffer, as_attachment=True, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=filename)


class ExportCSVView(CouncilOfficerRequiredMixin, View):
    """
    Exports citizens in current jurisdiction as a CSV file.
    """
    def get(self, request, *args, **kwargs):
        qs = get_visible_citizens_qs(request.user)
        
        # Prepare list
        data = []
        for c in qs:
            data.append({
                'Names': c.get_full_name(),
                'Date of Birth': c.dob.strftime('%Y-%m-%d'),
                'Gender': c.get_gender_display(),
                'National ID (NIC)': c.national_id or 'Under-18',
                'Status': c.get_current_status_display(),
                'Date of Death': c.death_date.strftime('%Y-%m-%d') if c.death_date else 'N/A',
                'Father': c.father.get_full_name() if c.father else 'N/A',
                'Mother': c.mother.get_full_name() if c.mother else 'N/A',
                'Subdivision': c.subdivision.name
            })
            
        df = pd.DataFrame(data)
        buffer = BytesIO()
        df.to_csv(buffer, index=False, encoding='utf-8')
        buffer.seek(0)
        
        # Log action
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.EXPORT,
            model_name="Citizen",
            details=f"Exported {len(data)} citizen records to CSV.",
            ip_address=request.META.get('REMOTE_ADDR')
        )
        
        filename = f"CLVRS_Export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return FileResponse(buffer, as_attachment=True, content_type='text/csv', filename=filename)


class SecureMediaView(LoginRequiredMixin, View):
    """
    Serves files from the secure media directory after verifying user permissions.
    """
    def get(self, request, path, *args, **kwargs):
        try:
            file_path = safe_join(settings.SECURE_MEDIA_ROOT, path)
        except ValueError:
            raise Http404("Invalid media path.")
            
        if not os.path.exists(file_path):
            raise Http404("Media file not found.")

        db_path = path.replace('\\', '/')
        citizen = Citizen.objects.filter(photo__icontains=db_path).first()
        
        if not citizen:
            if not (request.user.is_super_admin or request.user.is_council_officer or request.user.is_regional_admin):
                return HttpResponseForbidden("You do not have permission to access this media file.")
        else:
            visible_citizens = get_visible_citizens_qs(request.user)
            if not visible_citizens.filter(id=citizen.id).exists():
                return HttpResponseForbidden("You do not have permission to access this citizen's media file.")

        return FileResponse(open(file_path, 'rb'))

from registry.forms import RegionalAdminCreationForm, CouncilOfficerCreationForm, HospitalStaffCreationForm

class UserManagementDashboardView(RegionalAdminRequiredMixin, TemplateView):
    template_name = 'registry/user_management.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        if user.is_super_admin:
            context['regional_admins'] = User.objects.filter(role=User.Role.REGIONAL_ADMIN)
            context['regional_admin_form'] = RegionalAdminCreationForm()
        
        if user.is_super_admin or user.is_regional_admin:
            region = user.region if not user.is_super_admin else None
            
            co_qs = User.objects.filter(role=User.Role.COUNCIL_OFFICER)
            hs_qs = User.objects.filter(role=User.Role.HOSPITAL_STAFF)
            
            if region:
                co_qs = co_qs.filter(division__region=region)
                hs_qs = hs_qs.filter(division__region=region)
                
            context['council_officers'] = co_qs
            context['hospital_staff'] = hs_qs
            
            context['council_officer_form'] = CouncilOfficerCreationForm(user=user)
            context['hospital_staff_form'] = HospitalStaffCreationForm(user=user)
            
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        user = request.user
        
        if action == 'create_regional_admin' and user.is_super_admin:
            form = RegionalAdminCreationForm(request.POST)
            if form.is_valid():
                new_user = form.save(commit=False)
                new_user.role = User.Role.REGIONAL_ADMIN
                new_user.username = User.generate_id('REG')
                new_user.set_password('password123')
                new_user.save()
                messages.success(request, f"Regional Admin {new_user.username} created. Default password: 'password123'.")
            else:
                messages.error(request, f"Error: {form.errors}")
                
        elif action == 'create_council_officer' and (user.is_super_admin or user.is_regional_admin):
            form = CouncilOfficerCreationForm(request.POST, user=user)
            if form.is_valid():
                new_user = form.save(commit=False)
                new_user.role = User.Role.COUNCIL_OFFICER
                new_user.username = User.generate_id('COU')
                new_user.set_password('password123')
                new_user.save()
                messages.success(request, f"Council Officer {new_user.username} created. Default password: 'password123'.")
            else:
                messages.error(request, f"Error: {form.errors}")
                
        elif action == 'create_hospital_staff' and (user.is_super_admin or user.is_regional_admin):
            form = HospitalStaffCreationForm(request.POST, user=user)
            if form.is_valid():
                new_user = form.save(commit=False)
                new_user.role = User.Role.HOSPITAL_STAFF
                new_user.username = User.generate_id('HOS')
                new_user.set_password('password123')
                new_user.save()
                messages.success(request, f"Hospital Staff {new_user.username} created. Default password: 'password123'.")
            else:
                messages.error(request, f"Error: {form.errors}")

        return redirect('registry:user_management')
