from django.urls import path

from . import settings_views, views

# The marketing home ('home') lives in config/urls.py.
urlpatterns = [
    path('helloworld/', views.helloworld, name='helloworld'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analytics/', views.analytics, name='analytics'),
    path('settings/', settings_views.workspace_settings, name='settings'),
    path('settings/team/invite/', settings_views.team_invite, name='team_invite'),
    path('settings/team/invites/<int:invite_id>/revoke/', settings_views.team_invite_revoke, name='team_invite_revoke'),
    path('settings/team/<int:membership_id>/remove/', settings_views.team_member_remove, name='team_member_remove'),
    path('settings/team/<int:membership_id>/role/', settings_views.team_member_role, name='team_member_role'),
    path('settings/workspace/delete/', settings_views.workspace_delete, name='workspace_delete'),
    path('settings/account/delete/', settings_views.account_delete, name='account_delete'),
    path('workspaces/new/', settings_views.workspace_create, name='workspace_create'),
    path('workspaces/switch/', settings_views.workspace_switch, name='workspace_switch'),
    path('invite/<str:token>/', settings_views.invite_accept, name='invite_accept'),
]
