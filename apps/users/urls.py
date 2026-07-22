from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.users.api.login import LoginView, LogoutView, ForgotPasswordView
from apps.users.api.profile import ProfileView, ChangePasswordView
from apps.users.api.registration import RegistrationView
from apps.users.api.users import UserView

app_name = 'users'

router = DefaultRouter()
router.register('users', UserView, basename='user')

urlpatterns = [
    # ── Public ────────────────────────────────────────────────────────────────
    path('registration/', RegistrationView.as_view(), name='registration'),
    path('login/', LoginView.as_view(), name='login'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),

    # ── Authenticated ─────────────────────────────────────────────────────────
    path('logout/', LogoutView.as_view(), name='logout'),
    path('profile/', ProfileView.as_view(), name='profile'),
    path('profile/change-password/', ChangePasswordView.as_view(), name='change-password'),

] + router.urls
