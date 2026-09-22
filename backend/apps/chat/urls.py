from django.urls import path

from . import views

urlpatterns = [
    path('conversations/', views.conversations_list, name='conversations'),
    path('conversations/<int:pk>/', views.conversation_detail, name='conversation_detail'),
    path('conversations/<int:pk>/takeover/', views.conversation_takeover, name='conversation_takeover'),
    path('conversations/<int:pk>/release/', views.conversation_release, name='conversation_release'),
    path('conversations/<int:pk>/reply/', views.conversation_reply, name='conversation_reply'),
    path('conversations/<int:pk>/poll/', views.conversation_poll, name='conversation_poll'),
    path('conversations/<int:pk>/assign/', views.conversation_assign, name='conversation_assign'),
    path('conversations/<int:pk>/resolve/', views.conversation_resolve, name='conversation_resolve'),
    path('conversations/<int:pk>/tags/', views.conversation_tags, name='conversation_tags'),
    path('conversations/<int:pk>/suggest/', views.conversation_suggest, name='conversation_suggest'),
    path('saved-replies/', views.canned_responses, name='canned_responses'),
    path('tasks/', views.tasks_list, name='employee_tasks'),
    path('tasks/<int:pk>/status/', views.task_update_status, name='task_update_status'),
]
