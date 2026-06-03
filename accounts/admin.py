from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from accounts.models import User
from registry.models import AuditLog


def _get_request_ip(request):
    from django.conf import settings
    remote = request.META.get('REMOTE_ADDR', '')
    if remote in getattr(settings, 'TRUSTED_PROXIES', set()):
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if xff:
            return xff.split(',')[0].strip()
    return remote


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'get_full_name', 'role', 'is_active', 'is_staff')
    list_filter = ('role', 'is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')

    fieldsets = BaseUserAdmin.fieldsets + (
        ('CLVRS Role & Jurisdiction', {
            'fields': ('role', 'region', 'division', 'subdivision', 'hospital_name', 'hospital_type', 'dr_incharge', 'citizen_profile'),
        }),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        action = AuditLog.Action.UPDATE if change else AuditLog.Action.CREATE
        changed = ', '.join(form.changed_data) if change and form.changed_data else 'new user'
        AuditLog.objects.create(
            user=request.user,
            action=action,
            model_name='User',
            record_id=str(obj.pk),
            details=f"Admin {'updated' if change else 'created'} User {obj.username}. Fields: {changed}.",
            ip_address=_get_request_ip(request),
        )

    def delete_model(self, request, obj):
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.DELETE,
            model_name='User',
            record_id=str(obj.pk),
            details=f"Admin deleted User {obj.username}.",
            ip_address=_get_request_ip(request),
        )
        super().delete_model(request, obj)
