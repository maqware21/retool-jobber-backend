from django.contrib import admin

from apps.goals.models import TeamAnnualGoal, TeamGoal, TechnicianAnnualGoal, TechnicianGoal


class TeamGoalAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'month', 'goal_amount', 'is_active', 'updated_at')
    search_fields = ('tenant__business_name',)
    list_filter = ('is_active',)


class TechnicianGoalAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'user', 'month', 'goal_amount', 'is_active', 'updated_at')
    search_fields = ('tenant__business_name', 'user__name')
    list_filter = ('is_active',)


class TeamAnnualGoalAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'year', 'goal_amount', 'is_active', 'updated_at')
    search_fields = ('tenant__business_name',)
    list_filter = ('is_active',)


class TechnicianAnnualGoalAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'user', 'year', 'goal_amount', 'is_active', 'updated_at')
    search_fields = ('tenant__business_name', 'user__name')
    list_filter = ('is_active',)


admin.site.register(TeamGoal, TeamGoalAdmin)
admin.site.register(TechnicianGoal, TechnicianGoalAdmin)
admin.site.register(TeamAnnualGoal, TeamAnnualGoalAdmin)
admin.site.register(TechnicianAnnualGoal, TechnicianAnnualGoalAdmin)
