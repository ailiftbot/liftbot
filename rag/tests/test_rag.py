"""Offline tests for the RAG service (no network, no API keys)."""
import importlib
import json
import sys
from pathlib import Path

import pytest

TOKEN = 'test-internal-token'
AUTH = {'X-Internal-Token': TOKEN}
EMP = 'emp-1'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRICING = (
    'Pricing\n\nOur Starter plan costs 49 dollars per month and includes 1000 conversations. '
    'The Pro plan costs 199 dollars per month.'
)
HOURS = (
    'Opening hours\n\nThe clinic is open Monday to Friday from 9am to 6pm. '
    'We are closed on public holidays.'
)
PARKING = 'Parking\n\nFree visitor parking is available in the basement garage behind the building.'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for key in ('GOOGLE_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('RAG_INTERNAL_TOKEN', TOKEN)
    monkeypatch.setenv('RAG_INDEX_DIR', str(tmp_path / 'indexes'))
    for mod in [m for m in sys.modules if m == 'app' or m.startswith('app.')]:
        del sys.modules[mod]
    main = importlib.import_module('app.main')
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        c.main = main
        yield c


def ingest(client, source_id, text, title='', employee_id=EMP):
    r = client.post('/ingest', headers=AUTH, json={
        'employee_id': employee_id, 'source_id': source_id, 'title': title, 'text': text,
    })
    assert r.status_code == 200, r.text
    return r.json()


def capture_chat(client, message, monkeypatch, tokens=('ok',), employee_id=EMP, top_k=1):
    """Run /chat with a fake LLM; return (system_prompt, raw_body)."""
    captured = {}

    def fake_stream(system, messages):
        captured['system'] = system
        captured['messages'] = messages
        yield from tokens

    monkeypatch.setattr(client.main.llm, 'stream', fake_stream)
    r = client.post('/chat', headers=AUTH, json={
        'employee_id': employee_id, 'system_prompt': 'You are Ava, the clinic assistant.',
        'message': message, 'history': [], 'top_k': top_k,
    })
    assert r.status_code == 200, r.text
    return captured['system'], r.text


def context_of(system):
    return system.split('### KNOWLEDGE BASE CONTEXT ###', 1)[1].split('### STRICT INSTRUCTIONS ###', 1)[0]


def test_health_reports_offline(client):
    body = client.get('/health').json()
    assert body['status'] == 'ok'
    assert body['providers'] == {'groq': False, 'gemini': False, 'openrouter': False}
    assert body['embeddings']['mode'] == 'offline'
    assert body['embeddings']['dim'] == 768


def test_ingest_then_chat_retrieves_right_chunk(client, monkeypatch):
    ingest(client, '1', PRICING, 'Pricing')
    ingest(client, '2', HOURS, 'Hours')
    ingest(client, '3', PARKING, 'Parking')

    system, _ = capture_chat(client, 'How much does the Pro plan cost per month?', monkeypatch)
    ctx = context_of(system)
    assert '199 dollars' in ctx
    assert 'clinic is open' not in ctx
    assert system.startswith('You are Ava, the clinic assistant.')
    assert 'You are You are' not in system

    system, _ = capture_chat(client, 'when is the clinic open on friday?', monkeypatch)
    assert 'Monday to Friday' in context_of(system)

    system, _ = capture_chat(client, 'where can I park?', monkeypatch)
    assert 'basement garage' in context_of(system)


def test_top_k_is_used_and_clamped(client, monkeypatch):
    ingest(client, '1', PRICING)
    ingest(client, '2', HOURS)
    ingest(client, '3', PARKING)
    system, _ = capture_chat(client, 'pricing hours parking', monkeypatch, top_k=1)
    assert len([c for c in context_of(system).strip().split('\n\n') if c]) <= 2  # one chunk (title + body)
    system, _ = capture_chat(client, 'pricing hours parking', monkeypatch, top_k=50)
    ctx = context_of(system)
    assert '199 dollars' in ctx and 'Monday to Friday' in ctx and 'basement garage' in ctx


def test_delete_source_removes_it_from_retrieval(client, monkeypatch):
    ingest(client, '1', PRICING)
    ingest(client, '2', HOURS)
    r = client.delete(f'/sources/{EMP}/1', headers=AUTH)
    assert r.status_code == 200
    assert r.json()['removed'] >= 1

    system, _ = capture_chat(client, 'How much does the Pro plan cost?', monkeypatch, top_k=10)
    ctx = context_of(system)
    assert '199 dollars' not in ctx
    assert client.main.store.stats(EMP) == {'chunks': 1, 'sources': 1}

    # Deleting again is a no-op, not an error.
    assert client.delete(f'/sources/{EMP}/1', headers=AUTH).json()['removed'] == 0


def test_delete_employee_removes_index(client, monkeypatch):
    ingest(client, '1', PRICING)
    r = client.delete(f'/employees/{EMP}', headers=AUTH)
    assert r.status_code == 200 and r.json()['deleted'] is True
    system, _ = capture_chat(client, 'Pro plan cost', monkeypatch)
    assert 'No relevant training context' in system
    assert client.delete(f'/employees/{EMP}', headers=AUTH).json()['deleted'] is False


def test_reingest_same_source_does_not_duplicate(client):
    first = ingest(client, '1', PRICING)
    second = ingest(client, '1', PRICING)
    assert first['chunks'] == second['chunks']
    assert second['replaced'] == first['chunks']
    assert client.main.store.stats(EMP)['chunks'] == first['chunks']
    third = ingest(client, '1', 'Completely new pricing text: everything is free.')
    assert third['replaced'] == second['chunks']
    hits = client.main.store.search(EMP, 'pricing', top_k=10)
    assert len(hits) == third['chunks']
    assert all('199 dollars' not in t for t, _, _ in hits)


def test_auth_rejected_without_or_with_wrong_token(client):
    payload = {'employee_id': EMP, 'source_id': '1', 'text': 'x'}
    assert client.post('/ingest', json=payload).status_code == 401
    assert client.post('/ingest', json=payload, headers={'X-Internal-Token': 'nope'}).status_code == 401
    assert client.post('/chat', json={'employee_id': EMP, 'message': 'hi'}).status_code == 401
    assert client.delete(f'/sources/{EMP}/1').status_code == 401
    assert client.delete(f'/employees/{EMP}').status_code == 401


def test_rejects_path_traversal_ids(client):
    r = client.post('/ingest', headers=AUTH, json={'employee_id': '../x', 'source_id': '1', 'text': 'x'})
    assert r.status_code == 422


def test_sse_frames_are_json_and_newlines_survive(client, monkeypatch):
    tokens = ('Hello', '\n\n- bullet one\n', '- "quoted" two', '\n')
    _, body = capture_chat(client, 'hi', monkeypatch, tokens=tokens)
    assert body.endswith('data: [DONE]\n\n')
    frames = [f for f in body.split('\n\n') if f]
    assert frames[-1] == 'data: [DONE]'
    decoded = []
    for frame in frames[:-1]:
        assert frame.startswith('data: ') and '\n' not in frame
        decoded.append(json.loads(frame[len('data: '):]))
    assert ''.join(decoded) == ''.join(tokens)


def _chat_text(client, message, employee_id=EMP):
    r = client.post('/chat', headers=AUTH, json={'employee_id': employee_id, 'message': message, 'system_prompt': 'x'})
    frames = [f for f in r.text.split('\n\n') if f]
    assert frames[-1] == 'data: [DONE]'
    return ''.join(json.loads(f[6:]) for f in frames[:-1])


def test_offline_llm_stream_end_to_end(client):
    text = _chat_text(client, 'hello', employee_id='offline-empty')
    assert "don't have that specific information" in text


def test_offline_answers_from_best_passage(client):
    client.post('/ingest', headers=AUTH, json={
        'employee_id': 'offline-kb', 'source_id': 's1', 'title': 'FAQ',
        'text': 'We are open 9am to 6pm on weekdays.\n\nFree visitor parking is behind the building on Elm Street.',
    })
    text = _chat_text(client, 'Where can I park?', employee_id='offline-kb')
    assert 'Elm Street' in text and 'open 9am' not in text


def test_chunking_preserves_paragraphs(client):
    chunks = client.main.chunk_text('Q: a?\nA:   b\t\tc\n\n\n\nQ: d?\r\nA: e')
    assert chunks == ['Q: a?\nA: b c\n\nQ: d?\nA: e']


def test_hash_embeddings_are_deterministic_and_normalised(client):
    from app.embeddings import HashEmbeddings
    import numpy as np

    a = HashEmbeddings().embed_documents(['the pro plan costs money', 'parking garage'])
    b = HashEmbeddings().embed_documents(['the pro plan costs money', 'parking garage'])
    assert a.shape == (2, 768)
    assert np.allclose(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)


def test_query_embedding_failure_falls_back_to_bm25(client, monkeypatch):
    ingest(client, '1', PRICING)
    ingest(client, '2', HOURS)

    def boom(_q):
        raise RuntimeError('embedding API down')

    monkeypatch.setattr(client.main.store.embeddings, 'embed_query', boom)
    hits = client.main.store.search(EMP, 'Pro plan cost dollars', top_k=1)
    assert hits and '199 dollars' in hits[0][0]


def test_dimension_mismatch_reembeds_instead_of_500(client, monkeypatch):
    ingest(client, '1', PRICING)
    from app.embeddings import HashEmbeddings

    # Simulate switching embedder (different model/dim) after data was stored.
    monkeypatch.setattr(client.main.store, 'embeddings', HashEmbeddings(dim=256))
    system, _ = capture_chat(client, 'Pro plan cost', monkeypatch)
    assert '199 dollars' in context_of(system)
    snap = client.main.store._load(EMP)
    assert snap.vectors.shape[1] == 256


def test_llm_fallback_does_not_append_after_partial_stream(monkeypatch):
    from app.llm import LLMFallbackChain, UNAVAILABLE_REPLY

    chain = LLMFallbackChain()

    def partial(system, messages):
        yield 'Partial'
        raise RuntimeError('connection dropped')

    def second(system, messages):
        yield 'SECOND ANSWER'

    def empty_fail(system, messages):
        raise RuntimeError('down')
        yield  # pragma: no cover

    monkeypatch.setattr(chain, '_stream_groq', partial)
    monkeypatch.setattr(chain, '_stream_gemini', second)
    assert list(chain.stream('s', [{'role': 'visitor', 'content': 'hi'}])) == ['Partial']

    monkeypatch.setattr(chain, '_stream_groq', empty_fail)
    assert list(chain.stream('s', [{'role': 'visitor', 'content': 'hi'}])) == ['SECOND ANSWER']

    monkeypatch.setattr(chain, 'configured', lambda: {'groq': True, 'gemini': True, 'openrouter': False})
    monkeypatch.setattr(chain, '_stream_gemini', empty_fail)
    assert list(chain.stream('s', [{'role': 'visitor', 'content': 'hi'}])) == [UNAVAILABLE_REPLY]


def test_refuses_to_start_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_INTERNAL_TOKEN', '')
    monkeypatch.setenv('RAG_INDEX_DIR', str(tmp_path / 'idx'))
    for mod in [m for m in sys.modules if m == 'app' or m.startswith('app.')]:
        del sys.modules[mod]
    main = importlib.import_module('app.main')
    from fastapi.testclient import TestClient

    with pytest.raises(RuntimeError, match='RAG_INTERNAL_TOKEN'):
        with TestClient(main.app):
            pass
