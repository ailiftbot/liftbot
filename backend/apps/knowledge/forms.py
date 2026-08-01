from django import forms

from .models import KnowledgeSource


class KnowledgeSourceForm(forms.ModelForm):
    class Meta:
        model = KnowledgeSource
        fields = ('source_type', 'title', 'content', 'source_url', 'file')
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
        if source_type in (KnowledgeSource.SourceType.TEXT, KnowledgeSource.SourceType.FAQ) and not cleaned.get('content'):
            self.add_error('content', 'Paste the training content.')
        return cleaned
