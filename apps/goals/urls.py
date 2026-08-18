from django.urls import path

from apps.goals.api.team_goal import TeamGoalView
from apps.goals.api.technician_goal import TechnicianGoalView

app_name = 'goals'

urlpatterns = [
    path('team/', TeamGoalView.as_view(), name='team-goal'),
    path('technicians/', TechnicianGoalView.as_view(), name='technician-goals'),
]
