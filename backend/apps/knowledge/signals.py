from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.employees.models import AIEmployee

from .models import KnowledgeSource
from .tasks import rag_delete_employee, rag_delete_source


@receiver(post_delete, sender=KnowledgeSource)
def remove_source_vectors(sender, instance, **kwargs):
    employee_id, source_id = instance.employee_id, instance.pk
    if instance.file:
        instance.file.delete(save=False)
    transaction.on_commit(lambda: rag_delete_source(employee_id, source_id))


@receiver(post_delete, sender=AIEmployee)
def remove_employee_index(sender, instance, **kwargs):
    employee_id = instance.pk
    transaction.on_commit(lambda: rag_delete_employee(employee_id))
