from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User

from .models import UserProfile


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    full_name = forms.CharField(max_length=150, required=True)
    company_name = forms.CharField(max_length=200, required=True, help_text='Your company / workspace name')

    class Meta:
        model = User
        fields = ('full_name', 'email', 'company_name', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['full_name']
        if commit:
            user.save()
            UserProfile.objects.create(
                user=user,
                full_name=self.cleaned_data['full_name'],
                email_verified=False,
                is_verified=False,
            )
        return user


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label='Email')

    def clean_username(self):
        # Signup time email lowercase karke username mein save hoti hai,
        # isliye login pe bhi same normalize karna zaroori hai warna
        # case mismatch ki wajah se authenticate() silently fail ho jayega.
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