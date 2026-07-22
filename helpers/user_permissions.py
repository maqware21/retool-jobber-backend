from rest_framework.permissions import BasePermission

from helpers.constants import USER_PERMISSIONS

# USER_PERMISSIONS index map:
#   0 → 'admin'
#   1 → 'customer'


class AdminPermission(BasePermission):
    """Grants access to admin-role users AND superadmins."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return request.user.has_perm('users.' + USER_PERMISSIONS[0][0])


class CustomerPermission(BasePermission):
    """Grants access to customer-role users only."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.has_perm('users.' + USER_PERMISSIONS[1][0])


class AdminOrCustomerPermission(BasePermission):
    """Grants access to any authenticated role (admin, customer, or superadmin)."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return (
            request.user.has_perm('users.' + USER_PERMISSIONS[0][0]) or
            request.user.has_perm('users.' + USER_PERMISSIONS[1][0])
        )


class SuperAdminPermission(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


def method_permission_classes(permissions):
    """
    Decorator to override permission_classes on a per-method basis inside a view.

    Usage:
        @method_permission_classes([AdminPermission])
        def post(self, request):
            ...
    """
    def decorator(func):
        def _decorated(self, *args, **kwargs):
            self.permission_classes = permissions
            self.check_permissions(self.request)
            return func(self, *args, **kwargs)
        return _decorated
    return decorator
