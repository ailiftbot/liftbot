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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['capability_choices'].initial = self.instance.capabilities if self.instance.capabilities is not None else self.instance.default_capabilities()

    def save(self, commit=True):
        employee = super().save(commit=False)
        caps = self.cleaned_data.get('capability_choices')
        employee.capabilities = caps if caps is not None else employee.default_capabilities()
        employee.system_prompt = employee.build_system_prompt()
        if commit:
            employee.save()
        return employee
