from django.urls import path

from . import views

urlpatterns = [
    path('config/', views.widget_config, name='widget_config'),
    path('message/', views.widget_chat, name='widget_chat'),
    path('lead/', views.widget_lead, name='widget_lead'),
]
