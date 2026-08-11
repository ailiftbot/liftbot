from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('helloworld/', views.helloworld, name='helloworld'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analytics/', views.analytics, name='analytics'),
    path('settings/', views.workspace_settings, name='settings'),
]
