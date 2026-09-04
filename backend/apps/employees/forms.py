from django import forms

from apps.chat.constants import ALL_CAPABILITIES, CAPABILITY_LABELS

from .models import AIEmployee


class AIEmployeeForm(forms.ModelForm):
    capability_choices = forms.MultipleChoiceField(
        choices=[(c, CAPABILITY_LABELS[c]) for c in ALL_CAPABILITIES],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='What this AI Employee can do',
    )

    class Meta:
        model = AIEmployee
        fields = (
            'name',
            'department',
            'role',
            'personality',
            'language',
            'greeting_message',
            'handoff_email',
            'avatar',
            'brand_color',
            'is_active',
        )
        labels = {
            'name': 'Employee name',
            'department': 'Department',
            'role': 'Job title / role',
            'personality': 'Personality tone',
            'greeting_message': 'Greeting message',
            'handoff_email': 'Team handoff email',
            'brand_color': 'Brand color',
            'avatar': 'Avatar',
            'is_active': 'Active on website',
            'language': 'Language',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Maya', 'autocomplete': 'off'}),
            'department': forms.TextInput(attrs={'placeholder': 'e.g. Support, Sales'}),
            'language': forms.TextInput(attrs={'placeholder': 'en'}),
            'greeting_message': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Hi! How can I help you today?'}),
            'handoff_email': forms.EmailInput(attrs={'placeholder': 'team@yourcompany.com'}),
            'brand_color': forms.TextInput(attrs={'placeholder': '#7C3AED', 'spellcheck': 'false'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['capability_choices'].initial = self.instance.capabilities if self.instance.capabilities is not None else self.instance.default_capabilities()
        self.fields['capability_choices'].help_text = 'Choose the work this teammate can take on for visitors.'
        self.fields['greeting_message'].help_text = 'First message visitors see when the widget opens.'
        self.fields['handoff_email'].help_text = 'Used when a visitor asks to speak with your team.'
        self.fields['brand_color'].help_text = 'Widget header color. Use a hex value like #7C3AED.'

    def save(self, commit=True):
        employee = super().save(commit=False)
        caps = self.cleaned_data.get('capability_choices')
        employee.capabilities = caps if caps is not None else employee.default_capabilities()
        employee.system_prompt = employee.build_system_prompt()
        if commit:
            employee.save()
        return employee
