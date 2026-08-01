from django.db import models


class Lead(models.Model):
    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='leads')
    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.SET_NULL, null=True, related_name='leads')
    session = models.ForeignKey('chat.ChatSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='leads')
    name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.name or self.email or f'Lead {self.id}'
