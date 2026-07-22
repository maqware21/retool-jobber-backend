from django.contrib import admin

from apps.tenants.models import Tenant


class TenantAdmin(admin.ModelAdmin):
    list_display = ('id', 'business_name', 'is_active', 'created_at')
    search_fields = ('business_name',)
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at')


admin.site.register(Tenant, TenantAdmin)
