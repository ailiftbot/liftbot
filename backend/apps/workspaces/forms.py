from django import forms

from .models import WorkspaceMembership

ASSIGNABLE_ROLES = [
    (WorkspaceMembership.Role.ADMIN, 'Admin'),
    (WorkspaceMembership.Role.MEMBER, 'Member'),
]


class WorkspaceInviteForm(forms.Form):
    email = forms.EmailField(max_length=150)
    role = forms.ChoiceField(choices=ASSIGNABLE_ROLES, initial=WorkspaceMembership.Role.MEMBER)

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class MemberRoleForm(forms.Form):
    role = forms.ChoiceField(choices=ASSIGNABLE_ROLES)


class WorkspaceCreateForm(forms.Form):
    name = forms.CharField(max_length=200, label='Workspace name')

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name:
            raise forms.ValidationError('Enter a workspace name.')
        return name
