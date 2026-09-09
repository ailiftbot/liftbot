from django.urls import path

from . import views

urlpatterns = [
    path('', views.billing_home, name='billing'),
    path('checkout/<slug:plan_slug>/', views.create_checkout, name='billing_checkout'),
    path('webhook/stripe/', views.stripe_webhook, name='stripe_webhook'),
    path('webhook/save/', views.save_webhook, name='save_workspace_webhook'),

    # Onboarding (pre-login) billing gate
    path('onboarding/', views.onboarding_billing_view, name='billing_onboarding'),
    path('onboarding/pay/', views.onboarding_initiate_payment, name='billing_onboarding_pay'),
    path('onboarding/status/', views.onboarding_payment_status, name='billing_onboarding_status'),
]