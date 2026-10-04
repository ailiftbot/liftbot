import logging

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.shortcuts import render
from django.utils import timezone

from .spam import honeypot_triggered, rate_limited

logger = logging.getLogger(__name__)

INDUSTRY_CHOICES = [
    ('real-estate', 'Real Estate'),
    ('travel', 'Travel & Tourism'),
    ('ecommerce', 'E-commerce'),
    ('education', 'Education'),
    ('healthcare', 'Healthcare'),
    ('professional-services', 'Professional Services'),
    ('hospitality', 'Hospitality'),
    ('other', 'Other'),
]

COMPANY_SIZE_CHOICES = [
    ('1-10', '1–10 employees'),
    ('11-50', '11–50 employees'),
    ('51-200', '51–200 employees'),
    ('200+', '200+ employees'),
]


class EarlyAccessForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    business_name = forms.CharField(max_length=200)
    work_email = forms.EmailField()
    phone = forms.CharField(max_length=40, required=False)
    website = forms.URLField(required=False)
    industry = forms.ChoiceField(choices=INDUSTRY_CHOICES)
    company_size = forms.ChoiceField(choices=COMPANY_SIZE_CHOICES)
    help_with = forms.CharField(required=False, widget=forms.Textarea)


def send_early_access_inquiry(data):
    recipient = getattr(settings, 'EARLY_ACCESS_EMAIL', 'ailiftbot@gmail.com')
    industry_label = dict(INDUSTRY_CHOICES).get(data['industry'], data['industry'])
    size_label = dict(COMPANY_SIZE_CHOICES).get(data['company_size'], data['company_size'])
    body = (
        f'New LiftBot early access request\n'
        f'=================================\n\n'
        f'Full Name:     {data["full_name"]}\n'
        f'Business Name: {data["business_name"]}\n'
        f'Work Email:    {data["work_email"]}\n'
        f'Phone:         {data.get("phone") or "-"}\n'
        f'Website:       {data.get("website") or "-"}\n'
        f'Industry:      {industry_label}\n'
        f'Company Size:  {size_label}\n'
        f'Sent at:       {timezone.now().isoformat()}\n\n'
        f'What they want help with:\n{data.get("help_with") or "-"}\n'
    )
    mail = EmailMessage(
        subject=f'[LiftBot Early Access] {data["business_name"]} — {data["full_name"]}',
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.app'),
        to=[recipient],
        reply_to=[data['work_email']],
    )
    mail.send(fail_silently=False)
    return recipient


def early_access_view(request):
    if request.method != 'POST':
        return render(request, 'marketing/early_access.html', {'form': EarlyAccessForm()})

    form = EarlyAccessForm(request.POST)
    if honeypot_triggered(request):
        return render(request, 'marketing/early_access.html', {'submitted': True})
    if rate_limited(request, 'early_access'):
        form.add_error(None, 'Too many requests from your network. Please try again in an hour.')
        return render(request, 'marketing/early_access.html', {'form': form}, status=429)
    if not form.is_valid():
        return render(request, 'marketing/early_access.html', {'form': form})

    try:
        send_early_access_inquiry(form.cleaned_data)
    except Exception:
        logger.exception('Early access email failed')
        form.add_error(None, 'We could not submit your request. Please try again.')
        return render(request, 'marketing/early_access.html', {'form': form})

    return render(request, 'marketing/early_access.html', {'submitted': True})