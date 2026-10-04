import csv
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace

from .models import Lead

PAGE_SIZE = 25
FILTER_KEYS = ('q', 'employee', 'source', 'status', 'date_from', 'date_to')


def _parse_date(value):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _day_bounds(day, end=False):
    tz = timezone.get_current_timezone()
    if end:
        return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), tz)
    return timezone.make_aware(datetime.combine(day, time.min), tz)


def _filtered_leads(request, workspace):
    """Workspace-scoped lead queryset plus the cleaned filter values."""
    params = request.GET
    filters = {k: (params.get(k) or '').strip() for k in FILTER_KEYS}
    qs = Lead.objects.filter(workspace=workspace).select_related('employee', 'session')

    q = filters['q']
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(email__icontains=q)
            | Q(phone__icontains=q) | Q(intent_summary__icontains=q)
        )
    if filters['employee'].isdigit():
        qs = qs.filter(employee_id=int(filters['employee']))
    else:
        filters['employee'] = ''
    if filters['source'] in Lead.Source.values:
        qs = qs.filter(source=filters['source'])
    else:
        filters['source'] = ''
    if filters['status'] in Lead.Status.values:
        qs = qs.filter(status=filters['status'])
    else:
        filters['status'] = ''
    date_from = _parse_date(filters['date_from'])
    date_to = _parse_date(filters['date_to'])
    if date_from:
        qs = qs.filter(created_at__gte=_day_bounds(date_from))
    else:
        filters['date_from'] = ''
    if date_to:
        qs = qs.filter(created_at__lt=_day_bounds(date_to, end=True))
    else:
        filters['date_to'] = ''
    return qs.order_by('-created_at', '-id'), filters


def _querystring(filters):
    return urlencode({k: v for k, v in filters.items() if v})


@login_required
def lead_list(request):
    workspace = user_workspace(request.user)
    qs, filters = _filtered_leads(request, workspace)
    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'leads/list.html', {
        'workspace': workspace,
        'page_obj': page_obj,
        'leads': page_obj.object_list,
        'filters': filters,
        'is_filtered': any(filters.values()),
        'querystring': _querystring(filters),
        'employees': AIEmployee.objects.filter(workspace=workspace).order_by('name') if workspace else [],
        'status_choices': Lead.Status.choices,
        'source_choices': Lead.Source.choices,
        'total_count': Lead.objects.filter(workspace=workspace).count() if workspace else 0,
    })


class _Echo:
    def write(self, value):
        return value


def _csv_safe(value):
    """Neutralise spreadsheet formula injection for visitor-supplied text."""
    value = '' if value is None else str(value)
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


@login_required
@require_GET
def lead_export(request):
    workspace = user_workspace(request.user)
    qs, _filters = _filtered_leads(request, workspace)
    writer = csv.writer(_Echo())
    header = [
        'id', 'name', 'email', 'phone', 'status', 'source', 'ai_employee',
        'intent', 'notes', 'conversation_id', 'created_at',
    ]

    def rows():
        yield writer.writerow(header)
        for lead in qs.iterator(chunk_size=500):
            yield writer.writerow([
                lead.id,
                _csv_safe(lead.name),
                _csv_safe(lead.email),
                _csv_safe(lead.phone),
                lead.status,
                lead.source,
                _csv_safe(lead.employee.name if lead.employee_id else ''),
                _csv_safe(lead.intent_summary),
                _csv_safe(lead.notes),
                lead.session_id or '',
                timezone.localtime(lead.created_at).isoformat(),
            ])

    stamp = timezone.localdate().isoformat()
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="liftbot-leads-{stamp}.csv"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def lead_detail(request, pk):
    workspace = user_workspace(request.user)
    lead = get_object_or_404(
        Lead.objects.select_related('employee', 'session'), pk=pk, workspace=workspace,
    )
    if request.method == 'POST':
        lead.notes = (request.POST.get('notes') or '').strip()[:10000]
        update_fields = ['notes', 'updated_at']
        status = request.POST.get('status')
        if status:
            if status not in Lead.Status.values:
                messages.error(request, 'Choose a valid status.')
                return redirect('lead_detail', pk=lead.pk)
            lead.status = status
            update_fields.append('status')
        lead.save(update_fields=update_fields)
        messages.success(request, 'Lead updated.')
        return redirect('lead_detail', pk=lead.pk)
    tasks = lead.tasks.select_related('employee').order_by('-created_at')[:20]
    return render(request, 'leads/detail.html', {
        'workspace': workspace,
        'lead': lead,
        'tasks': tasks,
        'status_choices': Lead.Status.choices,
    })


@login_required
@require_POST
def lead_update_status(request, pk):
    workspace = user_workspace(request.user)
    lead = get_object_or_404(Lead, pk=pk, workspace=workspace)
    status = request.POST.get('status')
    wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    if status not in Lead.Status.values:
        if wants_json:
            return JsonResponse({'ok': False, 'error': 'Invalid status'}, status=400)
        messages.error(request, 'Choose a valid status.')
    else:
        lead.status = status
        lead.save(update_fields=['status', 'updated_at'])
        if wants_json:
            return JsonResponse({'ok': True, 'status': lead.status, 'label': lead.get_status_display()})
        messages.success(request, f'Lead marked as {lead.get_status_display().lower()}.')
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect('leads')
