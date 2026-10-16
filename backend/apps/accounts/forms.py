from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.db.models import Q

from .models import UserProfile


def stale_unverified_users(email):
    """
    Users with this email who never proved they own it. A new signup with the
    same address replaces them (typo'd / abandoned signups would otherwise lock
    the address forever).

    "Never verified" covers users with no profile row at all (legacy / half-
    created signups) and users who logged in but stopped at the OTP step —
    the onboarding gate keeps those out of the app, so they own nothing worth
    keeping. Anyone in an active (paid) workspace is never treated as stale.
    """
    unverified = Q(profile__isnull=True) | Q(profile__is_verified=False, profile__email_verified=False)
    return User.objects.filter(
        Q(email__iexact=email) | Q(username__iexact=email),
        unverified,
        is_staff=False,
        is_superuser=False,
    ).exclude(memberships__workspace__is_active=True).exclude(owned_workspaces__is_active=True)


class SignUpForm(UserCreationForm):
    full_name = forms.CharField(max_length=150, required=True)
    # username = email, and User.username is max 150 chars.
    email = forms.EmailField(required=True, max_length=150)
    company_name = forms.CharField(max_length=200, required=True, help_text='Your company / workspace name')
    accept_terms = forms.BooleanField(
        required=True,
        label='I agree to the Terms of Service and Privacy Policy',
        error_messages={'required': 'Please accept the Terms of Service and Privacy Policy to continue.'},
    )

    class Meta:
        model = User
        fields = ('full_name', 'email', 'company_name', 'password1', 'password2')

    def __init__(self, *args, invite=None, **kwargs):
        self.invite = invite
        super().__init__(*args, **kwargs)
        if invite is not None:
            # Joining an existing workspace — no company / workspace of their own.
            self.fields.pop('company_name')
            self.fields['email'].initial = invite.email
            self.fields['email'].help_text = 'Use the address your invite was sent to.'
        # Keep the consent checkbox last.
        self.order_fields([f for f in self.fields if f != 'accept_terms'] + ['accept_terms'])

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if self.invite is not None and email != self.invite.email.strip().lower():
            raise forms.ValidationError(f'This invite was sent to {self.invite.email}. Sign up with that address.')
        existing = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        if existing.exclude(pk__in=stale_unverified_users(email).values('pk')).exists():
            raise forms.ValidationError('An account with this email already exists. Log in instead.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['full_name'][:150]
        if commit:
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'full_name': self.cleaned_data['full_name'],
                    'email_verified': False,
                    'is_verified': False,
                },
            )
        return user


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label='Email', max_length=150)

    def clean_username(self):
        # Emails are stored lowercase as the username at signup, so normalise
        # here too or authenticate() silently fails on case mismatches.
        return self.cleaned_data['username'].lower().strip()


class OTPVerifyForm(forms.Form):
    code = forms.CharField(
        label='Verification code',
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'pattern': '[0-9]{6}',
            'placeholder': '000000',
        }),
    )

    def clean_code(self):
        return self.cleaned_data['code'].strip()
