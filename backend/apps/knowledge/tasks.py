import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.conf import settings
from pypdf import PdfReader

from .models import KnowledgeSource

logger = logging.getLogger(__name__)

MAX_FETCH_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5


def _rag_headers():
    return {'X-Internal-Token': settings.RAG_INTERNAL_TOKEN}


def fetch_public_page(url: str) -> str:
    """GET a customer-supplied URL, refusing internal hosts (also after redirects)."""
    from apps.workspaces.net import is_safe_public_url

    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_public_url(url, require_https=False):
            raise ValueError('This URL points to a private or unsupported address.')
        resp = requests.get(
            url, timeout=30, stream=True, allow_redirects=False,
            headers={'User-Agent': 'LiftBotTrainer/1.0'},
        )
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get('Location', ''))
            resp.close()
            continue
        resp.raise_for_status()
        body = b''
        for chunk in resp.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_FETCH_BYTES:
                raise ValueError('Page is too large to import (5 MB max).')
        resp.encoding = resp.encoding or 'utf-8'
        return body.decode(resp.encoding, errors='replace')
    raise ValueError('Too many redirects.')


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'nav', 'footer', 'svg']):
        tag.decompose()
    lines = (' '.join(line.split()) for line in soup.get_text(separator='\n').splitlines())
    return '\n'.join(line for line in lines if line)


def rag_delete_source(employee_id, source_id):
    try:
        requests.delete(
            f'{settings.RAG_SERVICE_URL}/sources/{employee_id}/{source_id}',
            headers=_rag_headers(), timeout=30,
        ).raise_for_status()
    except requests.RequestException:
        logger.exception('Could not remove vectors for source %s', source_id)


def rag_delete_employee(employee_id):
    try:
        requests.delete(
            f'{settings.RAG_SERVICE_URL}/employees/{employee_id}',
            headers=_rag_headers(), timeout=30,
        ).raise_for_status()
    except requests.RequestException:
        logger.exception('Could not remove index for employee %s', employee_id)


@shared_task(soft_time_limit=600)
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
            text = _html_to_text(fetch_public_page(source.source_url))

        if not text or not text.strip():
            raise ValueError('No text extracted from this source.')

        payload = {
            'employee_id': str(source.employee_id),
            'source_id': str(source.id),
            'title': source.title,
            'text': text,
        }
        # RAG ingest replaces any existing chunks for this source_id, so retries are safe.
        r = requests.post(
            f'{settings.RAG_SERVICE_URL}/ingest',
            json=payload,
            headers=_rag_headers(),
            timeout=600,
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
        if isinstance(exc, requests.RequestException):
            source.error_message = 'The training service is unavailable. Please retry in a moment.'
        else:
            source.error_message = str(exc)[:2000]
        source.save(update_fields=['status', 'error_message', 'updated_at'])


def queue_ingest(source):
    """Enqueue ingestion; run inline if the Celery broker is unreachable."""
    try:
        ingest_knowledge_source.delay(source.id)
    except Exception:  # noqa: BLE001 — kombu raises various connection errors
        logger.warning('Celery broker unavailable; ingesting source %s inline', source.id)
        ingest_knowledge_source(source.id)
