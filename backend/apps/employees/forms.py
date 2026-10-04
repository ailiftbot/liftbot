from django import forms
from django.utils.html import format_html

from apps.chat.constants import ALL_CAPABILITIES, CAPABILITY_LABELS

from .models import AIEmployee


class CapabilityCheckboxes(forms.CheckboxSelectMultiple):
    """Checkboxes plus a hidden marker so "all unchecked" differs from "not submitted".

    Browsers send nothing for unchecked boxes, so without the marker a form that
    simply doesn't render the field would wipe the employee's capabilities.
    """

    marker_suffix = '__present'

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        form_attr = self.attrs.get('form')
        marker = format_html(
            '<input type="hidden" name="{}" value="1"{}>',
            name + self.marker_suffix,
            format_html(' form="{}"', form_attr) if form_attr else '',
        )
        return html + marker

    def value_omitted_from_data(self, data, files, name):
        return name + self.marker_suffix not in data and name not in data


class AIEmployeeForm(forms.ModelForm):
    capability_choices = forms.MultipleChoiceField(
        choices=[(c, CAPABILITY_LABELS[c]) for c in ALL_CAPABILITIES],
        widget=CapabilityCheckboxes,
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
        field_name = self.add_prefix('capability_choices')
        omitted = self.fields['capability_choices'].widget.value_omitted_from_data(self.data, self.files, field_name)
        if not omitted:
            employee.capabilities = list(self.cleaned_data.get('capability_choices') or [])
        elif not employee.pk or employee.capabilities is None:
            employee.capabilities = employee.default_capabilities()
        # else: field not submitted — keep the employee's existing capabilities.
        employee.system_prompt = employee.build_system_prompt()
        if commit:
            employee.save()
        return employee
