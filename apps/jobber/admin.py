from django.contrib import admin

from apps.jobber.models import JobberAccount


class JobberAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'jobber_account_id', 'is_active', 'expires_at', 'created_at')
    search_fields = ('tenant__business_name', 'jobber_account_id')
    list_filter = ('is_active',)
    # Tokens are secrets — never editable through the admin.
    readonly_fields = (
        'access_token', 'refresh_token', 'token_type', 'scope',
        'expires_at', 'jobber_account_id', 'created_at', 'updated_at',
    )


admin.site.register(JobberAccount, JobberAccountAdmin)
