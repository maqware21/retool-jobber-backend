from django.contrib import admin

from apps.alerts.models import AlertRule


class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'rule_type', 'user', 'threshold_value', 'severity', 'is_enabled', 'is_active', 'updated_at')
    search_fields = ('tenant__business_name', 'user__name')
    list_filter = ('rule_type', 'severity', 'is_enabled', 'is_active')


admin.site.register(AlertRule, AlertRuleAdmin)
