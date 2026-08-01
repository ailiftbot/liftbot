from django import forms

from .models import AIEmployee


class AIEmployeeForm(forms.ModelForm):
    class Meta:
        model = AIEmployee
        fields = (
            'name',
            'role',
            'personality',
            'language',
            'greeting_message',
            'avatar',
            'brand_color',
            'is_active',
        )
        labels = {
            'name': 'Employee name',
            'role': 'Job title / role',
            'personality': 'Personality tone',
            'greeting_message': 'Greeting message',
        }
