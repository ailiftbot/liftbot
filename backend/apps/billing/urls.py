from django.urls import path

from . import views

urlpatterns = [
    path('', views.billing_home, name='billing'),
    path('checkout/<slug:plan_slug>/', views.create_checkout, name='billing_checkout'),
    path('webhook/stripe/', views.stripe_webhook, name='stripe_webhook'),
    path('webhook/save/', views.save_webhook, name='save_workspace_webhook'),
]
