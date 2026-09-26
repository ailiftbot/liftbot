import json
import logging

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

try:
    import requests as http_requests
except ImportError:
    http_requests = None

logger = logging.getLogger(__name__)

# ── Country codes (common subset) ──────────────────────────────
COUNTRY_CODES = [
    ('+91', 'India (+91)'),
    ('+1', 'US / Canada (+1)'),
    ('+44', 'UK (+44)'),
    ('+971', 'UAE (+971)'),
    ('+61', 'Australia (+61)'),
    ('+65', 'Singapore (+65)'),
    ('+49', 'Germany (+49)'),
    ('+33', 'France (+33)'),
    ('+81', 'Japan (+81)'),
    ('+86', 'China (+86)'),
    ('+55', 'Brazil (+55)'),
    ('+27', 'South Africa (+27)'),
    ('+966', 'Saudi Arabia (+966)'),
    ('+974', 'Qatar (+974)'),
    ('+60', 'Malaysia (+60)'),
    ('+63', 'Philippines (+63)'),
    ('+234', 'Nigeria (+234)'),
    ('+254', 'Kenya (+254)'),
    ('+52', 'Mexico (+52)'),
    ('+62', 'Indonesia (+62)'),
]

# ── LiftBot product choices ────────────────────────────────────
PRODUCT_CHOICES = [
    ('ai-sales-employee', 'AI Sales Employee'),
    ('ai-support-employee', 'AI Support Employee'),
    ('ai-receptionist', 'AI Receptionist'),
    ('ai-lead-qualification', 'AI Lead Qualification Employee'),
    ('ai-appointment-assistant', 'AI Appointment Assistant'),
    ('custom-ai-employee', 'Custom AI Employee'),
    ('full-platform', 'Full LiftBot Platform'),
]


class BookDemoForm(forms.Form):
    first_name = forms.CharField(max_length=80, min_length=1)
    last_name = forms.CharField(max_length=80, min_length=1)
    work_email = forms.EmailField()
    country_code = forms.ChoiceField(choices=COUNTRY_CODES)
    phone = forms.CharField(max_length=20, min_length=6)
    product = forms.ChoiceField(choices=PRODUCT_CHOICES)
    message = forms.CharField(max_length=2000, required=False)

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) < 6 or len(digits) > 15:
            raise forms.ValidationError('Please enter a valid phone number (6–15 digits).')
        return phone


def _send_demo_email(data):
    """Send email notification to admin about a new demo request."""
    recipient = getattr(settings, 'BOOK_DEMO_EMAIL',
                        getattr(settings, 'EARLY_ACCESS_EMAIL', 'ailiftbot@gmail.com'))
    product_label = dict(PRODUCT_CHOICES).get(data['product'], data['product'])
    country_label = dict(COUNTRY_CODES).get(data['country_code'], data['country_code'])
    now = timezone.now()

    body = (
        f'New LiftBot Demo Request\n'
        f'========================\n\n'
        f'First Name:       {data["first_name"]}\n'
        f'Last Name:        {data["last_name"]}\n'
        f'Work Email:       {data["work_email"]}\n'
        f'Country:          {country_label}\n'
        f'Phone Number:     {data["country_code"]} {data["phone"]}\n'
        f'Selected Product: {product_label}\n'
        f'Message:          {data.get("message") or "-"}\n'
        f'Submission Date:  {now.strftime("%Y-%m-%d %H:%M:%S %Z")}\n'
    )

    mail = EmailMessage(
        subject='New LiftBot Demo Request',
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.app'),
        to=[recipient],
        reply_to=[data['work_email']],
    )
    mail.send(fail_silently=False)
    return recipient


def _log_to_google_sheet(data):
    """POST submission data to a Google Apps Script web-app endpoint."""
    webhook_url = getattr(settings, 'GOOGLE_SHEET_WEBHOOK_URL', '')
    if not webhook_url:
        logger.warning('GOOGLE_SHEET_WEBHOOK_URL not set — skipping spreadsheet log.')
        return False

    if http_requests is None:
        logger.error('requests library not installed — cannot log to Google Sheet.')
        return False

    product_label = dict(PRODUCT_CHOICES).get(data['product'], data['product'])
    country_label = dict(COUNTRY_CODES).get(data['country_code'], data['country_code'])
    now = timezone.now()

    payload = {
        'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
        'first_name': data['first_name'],
        'last_name': data['last_name'],
        'work_email': data['work_email'],
        'country': country_label,
        'phone': f'{data["country_code"]} {data["phone"]}',
        'product': product_label,
        'message': data.get('message') or '-',
    }

    try:
        resp = http_requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info('Demo request logged to Google Sheet successfully.')
        return True
    except Exception as e:
        logger.exception('Failed to log demo request to Google Sheet: %s', e)
        return False


@csrf_exempt
def book_demo_view(request):
    """API endpoint for the Book a Demo modal form."""
    if request.method == 'OPTIONS':
        response = JsonResponse({'status': 'ok'})
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRFToken, Authorization'
        return response

    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Only POST allowed.'}, status=405)

    # Parse JSON body
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)

    form = BookDemoForm(data)
    if not form.is_valid():
        return JsonResponse({'status': 'error', 'errors': form.errors.get_json_data()}, status=400)

    cleaned = form.cleaned_data

    # 1) Send email
    email_ok = False
    try:
        _send_demo_email(cleaned)
        email_ok = True
        logger.info('Demo request email sent for %s', cleaned['work_email'])
    except Exception as e:
        logger.exception('Demo request email failed: %s', e)

    # 2) Log to Google Sheet
    sheet_ok = _log_to_google_sheet(cleaned)

    # Determine response
    if email_ok or sheet_ok:
        return JsonResponse({
            'status': 'success',
            'message': 'Thank you! Your demo request has been submitted successfully. Our team will contact you shortly.',
        })
    else:
        return JsonResponse({
            'status': 'error',
            'message': 'We could not process your request right now. Please try again or contact us directly at ailiftbot@gmail.com.',
        }, status=500)
