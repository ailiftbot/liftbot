import logging

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.shortcuts import render
from django.utils import timezone

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
    recipient = topic_recipient_map()[topic]
    label = TOPIC_LABELS[topic]
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


def contact_view(request):
    if request.method != 'POST':
        return render(request, 'marketing/talk_to_us.html', {'form': ContactForm()})

    form = ContactForm(request.POST)
    if not form.is_valid():
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    try:
        send_contact_inquiry(
            form.cleaned_data['full_name'].strip(),
            form.cleaned_data['email'].strip(),
            form.cleaned_data['topic'],
            form.cleaned_data['message'].strip(),
        )
    except Exception:
        logger.exception('Contact form email failed')
        form.add_error(None, 'We could not send your message. Please try again.')
        return render(request, 'marketing/talk_to_us.html', {'form': form})

    return render(request, 'marketing/talk_to_us.html', {'submitted': True})
