from django.urls import path

from . import views

urlpatterns = [
    path('', views.employee_list, name='employee_list'),
    path('hire/', views.employee_hire, name='employee_hire'),
    path('<int:pk>/', views.employee_detail, name='employee_detail'),
    path('<int:pk>/edit/', views.employee_edit, name='employee_edit'),
    path('<int:pk>/fire/', views.employee_fire, name='employee_fire'),
    path('<int:pk>/playground/', views.playground, name='employee_playground'),
    path('<int:pk>/playground/history/', views.playground_history, name='playground_history'),
]