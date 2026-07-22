from django.contrib import admin

from apps.users.models import User


class UserAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'first_name', 'last_name', 'role', 'is_active', 'is_admin_created', 'created_at')
    search_fields = ('email', 'first_name', 'last_name')
    list_filter = ('is_active', 'is_admin_created', 'is_superuser')
    readonly_fields = ('created_at', 'updated_at', 'last_login')

    def role(self, obj):
        return obj.role
    role.short_description = 'Role'

    def save_model(self, request, obj, form, change):
        if obj.pk:
            orig = User.objects.get(pk=obj.pk)
            if obj.password != orig.password:
                obj.set_password(obj.password)
        else:
            obj.set_password(obj.password)
        obj.save()


admin.site.register(User, UserAdmin)
