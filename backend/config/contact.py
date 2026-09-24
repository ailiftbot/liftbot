import json
import logging

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import JsonResponse
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
    full_name = forms.CharField(max_length=120)
    email = forms.EmailField()
    topic = forms.ChoiceField(choices=TOPIC_CHOICES)
    message = forms.CharField(min_length=10, max_length=5000)


def send_contact_inquiry(full_name, email, topic, message):
    recipients = topic_recipient_map()
    recipient = recipients.get(topic, getattr(settings, 'CONTACT_EMAIL_SUPPORT', 'support@liftbot.app'))
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
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.ai'),
        to=[recipient],
        reply_to=[email],
    )
    mail.send(fail_silently=False)
    return recipient


@csrf_exempt
def contact_view(request):
    if request.method == 'OPTIONS':
        response = JsonResponse({'status': 'ok'})
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRFToken, Authorization'
        return response

    if request.method != 'POST':
        return render(request, 'marketing/talk_to_us.html', {'form': ContactForm()})

    is_json = (
        request.content_type == 'application/json'
        or 'application/json' in request.headers.get('Accept', '')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )

    data = request.POST
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format in payload'}, status=400)

    form = ContactForm(data)
    if not form.is_valid():
        if is_json:
            return JsonResponse({'status': 'error', 'errors': form.errors.get_json_data()}, status=400)
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    try:
        recipient = send_contact_inquiry(
            form.cleaned_data['full_name'].strip(),
            form.cleaned_data['email'].strip(),
            form.cleaned_data['topic'],
            form.cleaned_data['message'].strip(),
        )
        logger.info('Contact inquiry sent to %s from %s', recipient, form.cleaned_data['email'])
    except Exception as e:
        logger.exception('Contact form email failed: %s', e)
        if is_json:
            return JsonResponse({
                'status': 'error',
                'message': 'Failed to send notification email. Please check server email configuration or try again.',
            }, status=500)
        form.add_error(None, 'We could not send your message. Please try again in a moment.')
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    if is_json:
        return JsonResponse({
            'status': 'success',
            'message': 'Thank you! Your message has been sent successfully.',
        })

    return render(request, 'marketing/talk_to_us.html', {'submitted': True})
