from django.urls import path

from . import views

urlpatterns = [
    path('conversations/', views.conversations_list, name='conversations'),
]
