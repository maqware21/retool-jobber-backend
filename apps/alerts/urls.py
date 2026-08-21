from django.urls import path

from apps.alerts.api.alert_rules import AlertRuleDetailView, AlertRuleListView
from apps.alerts.api.triggered import AlertsTriggeredView

app_name = 'alerts'

urlpatterns = [
    path('rules/', AlertRuleListView.as_view(), name='rule-list'),
    path('rules/<int:pk>/', AlertRuleDetailView.as_view(), name='rule-detail'),
    path('triggered/', AlertsTriggeredView.as_view(), name='triggered'),
]
