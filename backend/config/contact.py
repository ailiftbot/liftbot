import json
import logging

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

TOPIC_CHOICES = [
    ('sales', 'Sales'),
    ('product', 'Product'),
    ('partnerships', 'Partnerships'),
    ('support', 'Support'),
    ('general', 'General'),
]

TOPIC_LABELS = dict(TOPIC_CHOICES)


def topic_recipient_map():
    sales = getattr(settings, 'CONTACT_EMAIL_SALES', 'sales@liftbot.app')
    product = getattr(settings, 'CONTACT_EMAIL_PRODUCT', 'contact@liftbot.app')
    support = getattr(settings, 'CONTACT_EMAIL_SUPPORT', 'support@liftbot.app')
    return {
        'sales': sales,
        'partnerships': sales,
        'product': product,
        'general': product,
        'support': support,
    }


class ContactForm(forms.Form):
    full_name = forms.CharField(max_length=120, required=True)
    email = forms.EmailField(required=True)
    topic = forms.ChoiceField(choices=TOPIC_CHOICES, required=True)
    message = forms.CharField(min_length=10, max_length=5000, required=True)


def send_contact_inquiry(full_name, email, topic, message):
    recipient = topic_recipient_map().get(topic, getattr(settings, 'CONTACT_EMAIL_SUPPORT', 'support@liftbot.app'))
    label = TOPIC_LABELS.get(topic, 'General')
    body = (
        f'New LiftBot contact form inquiry\n'
        f'================================\n\n'
        f'Name:    {full_name}\n'
        f'Email:   {email}\n'
        f'Topic:   {label}\n'
        f'Sent at: {timezone.now().isoformat()}\n\n'
        f'Message:\n{message}\n'
    )
    mail = EmailMessage(
        subject=f'[LiftBot Contact] {label} — {full_name}',
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.app'),
        to=[recipient],
        reply_to=[email],
    )
    mail.send(fail_silently=False)
    return recipient


def _is_json_request(request):
    accept = request.headers.get('Accept', '')
    x_req = request.headers.get('X-Requested-With', '')
    content_type = getattr(request, 'content_type', '') or ''
    return 'application/json' in accept or x_req == 'XMLHttpRequest' or 'application/json' in content_type


def contact_view(request):
    if request.method == 'OPTIONS':
        res = HttpResponse()
        res['Access-Control-Allow-Origin'] = '*'
        res['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        res['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRFToken, X-Requested-With'
        return res

    is_json = _is_json_request(request)

    if request.method != 'POST':
        return render(request, 'marketing/talk_to_us.html', {'form': ContactForm()})

    data = request.POST
    if request.content_type and 'application/json' in request.content_type:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}

    form = ContactForm(data)
    if not form.is_valid():
        if is_json:
            return JsonResponse({
                'status': 'error',
                'message': 'Please correct the errors in the form.',
                'errors': form.errors,
            }, status=400)
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    try:
        send_contact_inquiry(
            form.cleaned_data['full_name'].strip(),
            form.cleaned_data['email'].strip(),
            form.cleaned_data['topic'],
            form.cleaned_data['message'].strip(),
        )
    except Exception as exc:
        logger.exception('Contact form email failed to send: %s', exc)
        err_msg = 'We could not send your message due to a server error. Please try again or email us directly at support@liftbot.app.'
        if is_json:
            return JsonResponse({'status': 'error', 'message': err_msg}, status=500)
        form.add_error(None, err_msg)
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    if is_json:
        return JsonResponse({
            'status': 'success',
            'message': 'Thank you! Your message has been sent. Our team will get back to you shortly.',
        })

    return render(request, 'marketing/talk_to_us.html', {'submitted': True})
