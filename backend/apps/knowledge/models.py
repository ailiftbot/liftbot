from django.db import models


class KnowledgeSource(models.Model):
    class SourceType(models.TextChoices):
        PDF = 'pdf', 'PDF'
        URL = 'url', 'Website URL'
        TEXT = 'text', 'Plain Text'
        FAQ = 'faq', 'FAQ'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        READY = 'ready', 'Ready'
        FAILED = 'failed', 'Failed'

    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.CASCADE, related_name='knowledge_sources')
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, help_text='Text / FAQ content or crawled page text')
    source_url = models.URLField(blank=True)
    file = models.FileField(upload_to='knowledge/%Y/%m/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    faiss_doc_id = models.CharField(max_length=64, blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.title} ({self.get_source_type_display()})'
