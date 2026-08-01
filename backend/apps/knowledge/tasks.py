import logging

import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.conf import settings
from pypdf import PdfReader

from .models import KnowledgeSource

logger = logging.getLogger(__name__)


def _rag_headers():
    return {'X-Internal-Token': settings.RAG_INTERNAL_TOKEN}


@shared_task
def ingest_knowledge_source(source_id: int):
    try:
        source = KnowledgeSource.objects.select_related('employee', 'employee__workspace').get(pk=source_id)
    except KnowledgeSource.DoesNotExist:
        return

    source.status = KnowledgeSource.Status.PROCESSING
    source.error_message = ''
    source.save(update_fields=['status', 'error_message', 'updated_at'])

    try:
        text = source.content
        if source.source_type == KnowledgeSource.SourceType.PDF and source.file:
            reader = PdfReader(source.file.path)
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        elif source.source_type == KnowledgeSource.SourceType.URL and source.source_url:
            resp = requests.get(source.source_url, timeout=30, headers={'User-Agent': 'LiftBotTrainer/1.0'})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(['script', 'style', 'noscript']):
                tag.decompose()
            text = ' '.join(soup.get_text(separator=' ').split())

        if not text or not text.strip():
            raise ValueError('No text extracted from this source.')

        payload = {
            'employee_id': str(source.employee_id),
            'source_id': str(source.id),
            'title': source.title,
            'text': text,
        }
        r = requests.post(
            f'{settings.RAG_SERVICE_URL}/ingest',
            json=payload,
            headers=_rag_headers(),
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        source.faiss_doc_id = data.get('doc_id', '')
        source.chunk_count = data.get('chunks', 0)
        source.content = text[:50_000]
        source.status = KnowledgeSource.Status.READY
        source.save()
    except Exception as exc:
        logger.exception('Ingest failed for source %s', source_id)
        source.status = KnowledgeSource.Status.FAILED
        source.error_message = str(exc)[:2000]
        source.save(update_fields=['status', 'error_message', 'updated_at'])
