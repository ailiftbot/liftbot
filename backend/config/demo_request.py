import csv
import json
import logging
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

PRODUCT_CHOICES = [
    ('Online Browser Testing', 'Online Browser Testing'),
    ('AI Sales & Support', 'AI Sales & Support'),
    ('AI Employee', 'AI Employee'),
    ('AI Lead Qualification', 'AI Lead Qualification'),
    ('AI Receptionist', 'AI Receptionist'),
    ('Customer Support Automation', 'Customer Support Automation'),
    ('Custom AI Solution', 'Custom AI Solution'),
]


class DemoRequestForm(forms.Form):
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=True)
    country_code = forms.CharField(max_length=15, required=True, initial='+91')
    phone = forms.CharField(max_length=30, required=True)
    product = forms.CharField(max_length=120, required=True)
    message = forms.CharField(max_length=4000, required=False)


def send_demo_email_alert(data):
    recipient = getattr(settings, 'CONTACT_EMAIL_SALES', 'sales@liftbot.app')
    support = getattr(settings, 'CONTACT_EMAIL_SUPPORT', 'support@liftbot.app')
    recipients = list({recipient, support, 'ailiftbot@gmail.com'})

    subject = f"[LiftBot Demo Request] {data['first_name']} {data['last_name']} — {data['product']}"
    body = (
        "New Personal Demo Request\n"
        "=========================\n\n"
        f"Timestamp:        {data['timestamp']}\n"
        f"First Name:       {data['first_name']}\n"
        f"Last Name:        {data['last_name']}\n"
        f"Work Email:       {data['email']}\n"
        f"Country Code:     {data['country_code']}\n"
        f"Phone Number:     {data['phone']}\n"
        f"Product Selected: {data['product']}\n\n"
        f"Message:\n{data.get('message') or '(None provided)'}\n"
    )

    mail = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'LiftBot <noreply@liftbot.app>'),
        to=recipients,
        reply_to=[data['email']],
    )
    mail.send(fail_silently=False)


def append_to_excel_and_csv(data):
    data_dir = settings.BASE_DIR / 'data'
    data_dir.mkdir(exist_ok=True, parents=True)

    headers = [
        'Timestamp',
        'First Name',
        'Last Name',
        'Work Email',
        'Country Code',
        'Phone Number',
        'Product Selected',
        'Message'
    ]

    row_data = [
        data['timestamp'],
        data['first_name'],
        data['last_name'],
        data['email'],
        data['country_code'],
        data['phone'],
        data['product'],
        data.get('message', ''),
    ]

    # 1. Append to CSV
    csv_file = data_dir / 'demo_requests.csv'
    is_new_csv = not csv_file.exists() or csv_file.stat().st_size == 0
    try:
        with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if is_new_csv:
                writer.writerow(headers)
            writer.writerow(row_data)
    except Exception as exc:
        logger.exception("Failed to write demo request to CSV: %s", exc)

    # 2. Append to Excel (.xlsx) using openpyxl if available
    try:
        import openpyxl
        xlsx_file = data_dir / 'demo_requests.xlsx'
        if not xlsx_file.exists():
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Demo Leads"
            ws.append(headers)
        else:
            wb = openpyxl.load_workbook(xlsx_file)
            ws = wb.active

        ws.append(row_data)
        wb.save(xlsx_file)
    except ImportError:
        logger.info("openpyxl not installed, saved to CSV.")
    except Exception as exc:
        logger.exception("Failed to write demo request to Excel: %s", exc)


def sync_to_google_sheets(data):
    webhook_url = getattr(settings, 'GOOGLE_SHEETS_WEBHOOK_URL', None) or getattr(settings, 'DEMO_WEBHOOK_URL', None)
    if not webhook_url:
        return

    payload = json.dumps({
        'timestamp': data['timestamp'],
        'first_name': data['first_name'],
        'last_name': data['last_name'],
        'work_email': data['email'],
        'country_code': data['country_code'],
        'phone_number': data['phone'],
        'product_selected': data['product'],
        'message': data.get('message', ''),
    }).encode('utf-8')

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={'Content-Type': 'application/json', 'User-Agent': 'LiftBot-Webhook/1.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            logger.info("Google Sheets webhook sync status: %s", response.status)
    except Exception as exc:
        logger.warning("Google Sheets webhook sync failed (non-blocking): %s", exc)


def schedule_demo_view(request):
    if request.method == 'OPTIONS':
        res = HttpResponse()
        res['Access-Control-Allow-Origin'] = '*'
        res['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        res['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRFToken, X-Requested-With'
        return res

    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    if request.content_type and 'application/json' in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            payload = {}
    else:
        payload = request.POST

    form = DemoRequestForm(payload)
    if not form.is_valid():
        return JsonResponse({
            'status': 'error',
            'message': 'Please fill all required fields properly.',
            'errors': form.errors,
        }, status=400)

    cleaned = form.cleaned_data
    lead_data = {
        'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
        'first_name': cleaned['first_name'].strip(),
        'last_name': cleaned['last_name'].strip(),
        'email': cleaned['email'].strip().lower(),
        'country_code': cleaned['country_code'].strip(),
        'phone': cleaned['phone'].strip(),
        'product': cleaned['product'].strip(),
        'message': cleaned.get('message', '').strip(),
    }

    # 1. Excel & CSV Auto-Sync
    append_to_excel_and_csv(lead_data)

    # 2. Email alert
    try:
        send_demo_email_alert(lead_data)
    except Exception as exc:
        logger.exception("Failed to send demo alert email: %s", exc)

    # 3. Google Sheets / Webhook Auto-Sync
    try:
        sync_to_google_sheets(lead_data)
    except Exception as exc:
        logger.exception("Failed to sync to Google Sheets webhook: %s", exc)

    return JsonResponse({
        'status': 'success',
        'message': 'Your personal demo request has been scheduled! Our team will contact you shortly.',
    })
