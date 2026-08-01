from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.workspaces.views import user_workspace

from .models import BillingPlan, Invoice


@login_required
def billing_home(request):
    workspace = user_workspace(request.user)
    plans = BillingPlan.objects.filter(is_active=True)
    invoices = Invoice.objects.filter(workspace=workspace)[:20] if workspace else []
    return render(request, 'billing/home.html', {
        'workspace': workspace,
        'plans': plans,
        'invoices': invoices,
    })
