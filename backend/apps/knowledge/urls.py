from django.urls import path

from . import views

urlpatterns = [
    path('<int:employee_id>/', views.knowledge_list, name='knowledge_list'),
    path('<int:employee_id>/add/', views.knowledge_add, name='knowledge_add'),
    path('<int:employee_id>/<int:pk>/delete/', views.knowledge_delete, name='knowledge_delete'),
]
