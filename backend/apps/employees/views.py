from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.workspaces.public_urls import widget_urls
from apps.workspaces.views import user_workspace

from .forms import AIEmployeeForm
from .models import AIEmployee


def _workspace_or_redirect(request):
    workspace = user_workspace(request.user)
    if not workspace:
        messages.error(request, 'Create a workspace first.')
        return None
    return workspace


@login_required
def employee_list(request):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')
    employees = AIEmployee.objects.filter(workspace=workspace)
    return render(request, 'employees/list.html', {'employees': employees, 'workspace': workspace})


@login_required
def employee_hire(request):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')

    limit = workspace.plan.employee_limit if workspace.plan else 1
    if AIEmployee.objects.filter(workspace=workspace).count() >= limit:
        messages.warning(request, f'Your plan allows {limit} AI Employee(s). Upgrade to hire more.')
        return redirect('billing')

    if request.method == 'POST':
        form = AIEmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.workspace = workspace
            if not employee.brand_color:
                employee.brand_color = workspace.brand_color
            employee.save()
            messages.success(request, f'{employee.name} has been hired.')
            return redirect('employee_detail', pk=employee.pk)
    else:
        form = AIEmployeeForm(initial={'brand_color': workspace.brand_color})

    return render(request, 'employees/form.html', {'form': form, 'title': 'Hire an AI Employee'})


@login_required
def employee_detail(request, pk):
    workspace = _workspace_or_redirect(request)
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    urls = widget_urls(request)
    return render(request, 'employees/detail.html', {
        'employee': employee,
        'embed_snippet': employee.embed_snippet(widget_url=urls['widget'], api_base=urls['api']),
        'team_embed_snippet': workspace.team_embed_snippet(
            widget_url=urls['widget'], api_base=urls['api']
        ),
        'workspace': workspace,
    })


@login_required
def employee_edit(request, pk):
    workspace = _workspace_or_redirect(request)
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    if request.method == 'POST':
        form = AIEmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            form.save()
            messages.success(request, f'{employee.name} updated.')
            return redirect('employee_detail', pk=employee.pk)
    else:
        form = AIEmployeeForm(instance=employee)
    return render(request, 'employees/form.html', {'form': form, 'title': f'Edit {employee.name}'})


@login_required
def employee_fire(request, pk):
    workspace = _workspace_or_redirect(request)
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    if request.method == 'POST':
        name = employee.name
        employee.delete()
        messages.success(request, f'{name} has been let go.')
        return redirect('employee_list')
    return render(request, 'employees/fire_confirm.html', {'employee': employee})


@login_required
def playground(request, pk):
    workspace = _workspace_or_redirect(request)
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    urls = widget_urls(request)
    return render(request, 'employees/playground.html', {
        'employee': employee,
        'workspace': workspace,
        'public_widget_api': urls['api'],
    })
