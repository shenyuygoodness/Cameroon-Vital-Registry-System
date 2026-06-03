from django.contrib import admin
from django.utils.html import format_html

from registry.models import (
    Region, Division, Subdivision,
    Citizen, Household, HouseholdMember,
    BirthDeclaration, DeathDeclaration, AuditLog,
)


def _get_request_ip(request):
    remote = request.META.get('REMOTE_ADDR', '')
    from django.conf import settings
    if remote in getattr(settings, 'TRUSTED_PROXIES', set()):
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if xff:
            return xff.split(',')[0].strip()
    return remote


class AuditedModelAdmin(admin.ModelAdmin):
    """
    Base class that writes an AuditLog entry for every admin save and delete.
    Inherit from this instead of ModelAdmin for all sensitive models.
    """

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        action = AuditLog.Action.UPDATE if change else AuditLog.Action.CREATE
        changed = ', '.join(form.changed_data) if change and form.changed_data else 'new record'
        AuditLog.objects.create(
            user=request.user,
            action=action,
            model_name=obj.__class__.__name__,
            record_id=str(obj.pk),
            details=f"Admin {'updated' if change else 'created'} {obj.__class__.__name__} pk={obj.pk}. Fields: {changed}.",
            ip_address=_get_request_ip(request),
        )

    def delete_model(self, request, obj):
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.DELETE,
            model_name=obj.__class__.__name__,
            record_id=str(obj.pk),
            details=f"Admin deleted {obj.__class__.__name__} pk={obj.pk}: {obj}.",
            ip_address=_get_request_ip(request),
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.Action.DELETE,
                model_name=obj.__class__.__name__,
                record_id=str(obj.pk),
                details=f"Admin bulk-deleted {obj.__class__.__name__} pk={obj.pk}.",
                ip_address=_get_request_ip(request),
            )
        super().delete_queryset(request, queryset)


# ── Administrative geography ──────────────────────────────────────────────────

@admin.register(Region)
class RegionAdmin(AuditedModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name', 'code')


@admin.register(Division)
class DivisionAdmin(AuditedModelAdmin):
    list_display = ('name', 'region')
    list_filter = ('region',)
    search_fields = ('name',)


@admin.register(Subdivision)
class SubdivisionAdmin(AuditedModelAdmin):
    list_display = ('name', 'division')
    list_filter = ('division__region',)
    search_fields = ('name',)


# ── Citizens ──────────────────────────────────────────────────────────────────

@admin.register(Citizen)
class CitizenAdmin(AuditedModelAdmin):
    list_display = ('get_full_name', 'gender', 'dob', 'current_status', 'subdivision', 'is_deleted')
    list_filter = ('gender', 'current_status', 'is_deleted', 'subdivision__division__region')
    search_fields = ('first_name', 'last_name', 'national_id_hash')
    readonly_fields = ('national_id_hash', 'created_at', 'updated_at')
    exclude = ('national_id',)  # Never expose decrypted NIC in admin list/form

    def get_queryset(self, request):
        return self.model.all_objects.all()

    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = 'Full Name'


# ── Declarations ─────────────────────────────────────────────────────────────

@admin.register(BirthDeclaration)
class BirthDeclarationAdmin(AuditedModelAdmin):
    list_display = ('declaration_number', 'child_last_name', 'child_first_name', 'child_dob', 'status', 'created_at')
    list_filter = ('status', 'subdivision__division__region')
    search_fields = ('declaration_number', 'child_first_name', 'child_last_name')
    readonly_fields = (
        'declaration_number', 'mother_national_id_hash', 'father_national_id_hash',
        'created_at', 'updated_at', 'confirmed_at',
    )
    exclude = ('mother_national_id', 'father_national_id')  # Never show plaintext NICs in admin


@admin.register(DeathDeclaration)
class DeathDeclarationAdmin(AuditedModelAdmin):
    list_display = ('declaration_number', 'citizen', 'death_date', 'status', 'created_at')
    list_filter = ('status', 'subdivision__division__region')
    search_fields = ('declaration_number', 'citizen__first_name', 'citizen__last_name')
    readonly_fields = ('declaration_number', 'created_at', 'updated_at', 'confirmed_at')


# ── Households ────────────────────────────────────────────────────────────────

@admin.register(Household)
class HouseholdAdmin(AuditedModelAdmin):
    list_display = ('household_number', 'head', 'subdivision', 'created_at')
    search_fields = ('household_number',)


@admin.register(HouseholdMember)
class HouseholdMemberAdmin(AuditedModelAdmin):
    list_display = ('citizen', 'household', 'relationship_to_head')
    list_filter = ('relationship_to_head',)


# ── Audit Log (read-only) ─────────────────────────────────────────────────────

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'model_name', 'record_id', 'ip_address')
    list_filter = ('action', 'model_name')
    search_fields = ('user__username', 'details', 'record_id')
    readonly_fields = ('timestamp', 'user', 'action', 'model_name', 'record_id', 'details', 'ip_address')
    ordering = ('-timestamp',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
