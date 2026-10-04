from django import forms

from .models import KnowledgeSource

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class KnowledgeSourceForm(forms.ModelForm):
    class Meta:
        model = KnowledgeSource
        fields = ('source_type', 'title', 'content', 'source_url', 'file', 'is_public')
        widgets = {
            'content': forms.Textarea(attrs={'rows': 6}),
        }

    def clean(self):
        cleaned = super().clean()
        source_type = cleaned.get('source_type')
        if source_type == KnowledgeSource.SourceType.PDF and not cleaned.get('file'):
            self.add_error('file', 'Upload a PDF file.')
        if source_type == KnowledgeSource.SourceType.URL and not cleaned.get('source_url'):
            self.add_error('source_url', 'Enter a URL to crawl.')
        elif source_type == KnowledgeSource.SourceType.URL:
            from apps.workspaces.net import is_safe_public_url
            if not is_safe_public_url(cleaned['source_url'], require_https=False):
                self.add_error('source_url', 'Enter a public website address.')
        upload = cleaned.get('file')
        if source_type == KnowledgeSource.SourceType.PDF and upload:
            if not upload.name.lower().endswith('.pdf'):
                self.add_error('file', 'Only PDF files are supported.')
            elif upload.size > MAX_UPLOAD_BYTES:
                self.add_error('file', 'PDF must be 20 MB or smaller.')
        if source_type in (KnowledgeSource.SourceType.TEXT, KnowledgeSource.SourceType.FAQ) and not cleaned.get('content'):
            self.add_error('content', 'Paste the training content.')
        return cleaned
