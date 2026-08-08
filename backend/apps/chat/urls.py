from django.urls import path

from . import views

urlpatterns = [
    path('conversations/', views.conversations_list, name='conversations'),
    path('conversations/<int:pk>/', views.conversation_detail, name='conversation_detail'),
    path('conversations/<int:pk>/takeover/', views.conversation_takeover, name='conversation_takeover'),
    path('conversations/<int:pk>/release/', views.conversation_release, name='conversation_release'),
    path('conversations/<int:pk>/reply/', views.conversation_reply, name='conversation_reply'),
    path('conversations/<int:pk>/poll/', views.conversation_poll, name='conversation_poll'),
    path('tasks/', views.tasks_list, name='employee_tasks'),
    path('tasks/<int:pk>/status/', views.task_update_status, name='task_update_status'),
]
