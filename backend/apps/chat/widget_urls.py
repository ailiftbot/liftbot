from django.urls import path

from . import views

urlpatterns = [
    path('roster/', views.widget_roster, name='widget_roster'),
    path('config/', views.widget_config, name='widget_config'),
    path('message/', views.widget_chat, name='widget_chat'),
    path('action/', views.widget_action, name='widget_action'),
    path('poll/', views.widget_poll, name='widget_poll'),
    path('lead/', views.widget_lead, name='widget_lead'),
]
