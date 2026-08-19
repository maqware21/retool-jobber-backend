from django.urls import path

from apps.goals.api.team_annual_goal import TeamAnnualGoalView
from apps.goals.api.team_goal import TeamGoalView
from apps.goals.api.technician_annual_goal import TechnicianAnnualGoalView
from apps.goals.api.technician_goal import TechnicianGoalView

app_name = 'goals'

urlpatterns = [
    path('team/', TeamGoalView.as_view(), name='team-goal'),
    path('team/annual/', TeamAnnualGoalView.as_view(), name='team-annual-goal'),
    path('technicians/', TechnicianGoalView.as_view(), name='technician-goals'),
    path('technicians/annual/', TechnicianAnnualGoalView.as_view(), name='technician-annual-goals'),
]
