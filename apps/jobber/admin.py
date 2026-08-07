from django.contrib import admin

from apps.jobber.models import (
    JobberAccount,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberSyncRun,
    JobberUser,
    JobberVisit,
)


class JobberAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'jobber_account_id', 'is_active', 'expires_at', 'created_at')
    search_fields = ('tenant__business_name', 'jobber_account_id')
    list_filter = ('is_active',)
    # Tokens are secrets — never editable through the admin.
    readonly_fields = (
        'access_token', 'refresh_token', 'token_type', 'scope',
        'expires_at', 'jobber_account_id', 'created_at', 'updated_at',
    )


class JobberClientAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'name', 'jobber_id', 'is_active', 'synced_at')
    search_fields = ('tenant__business_name', 'name', 'jobber_id')
    list_filter = ('is_active',)


class JobberUserAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'name', 'jobber_id', 'is_account_admin', 'is_account_owner', 'is_active', 'synced_at')
    search_fields = ('tenant__business_name', 'name', 'jobber_id')
    list_filter = ('is_active', 'is_account_admin', 'is_account_owner')


class JobberJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'job_number', 'client', 'status_display', 'total', 'is_active', 'synced_at')
    search_fields = ('tenant__business_name', 'title', 'jobber_id')
    list_filter = ('is_active', 'job_status')


class JobberVisitAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'job', 'assigned_user', 'is_active', 'synced_at')
    search_fields = ('tenant__business_name', 'jobber_id')
    list_filter = ('is_active',)


class JobberInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'invoice_number', 'client', 'status_display', 'amount', 'balance', 'is_active', 'synced_at')
    search_fields = ('tenant__business_name', 'jobber_id')
    list_filter = ('is_active', 'invoice_status')


class JobberSyncRunAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'status', 'started_at', 'finished_at', 'error_message')
    list_filter = ('status',)
    search_fields = ('tenant__business_name',)


admin.site.register(JobberAccount, JobberAccountAdmin)
admin.site.register(JobberClient, JobberClientAdmin)
admin.site.register(JobberUser, JobberUserAdmin)
admin.site.register(JobberJob, JobberJobAdmin)
admin.site.register(JobberVisit, JobberVisitAdmin)
admin.site.register(JobberInvoice, JobberInvoiceAdmin)
admin.site.register(JobberSyncRun, JobberSyncRunAdmin)
