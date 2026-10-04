from django.urls import path

from . import views

urlpatterns = [
    path('', views.lead_list, name='leads'),
    path('export.csv', views.lead_export, name='leads_export'),
    path('<int:pk>/', views.lead_detail, name='lead_detail'),
    path('<int:pk>/status/', views.lead_update_status, name='lead_update_status'),
]
