import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.responses import StreamingResponse
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from .llm import LLMFallbackChain
from .vectorstore import EmployeeVectorStore

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger('liftbot.rag')

DEFAULT_TOKEN = 'liftbot-rag-internal-token'
INTERNAL_TOKEN = os.getenv('RAG_INTERNAL_TOKEN', '').strip()
TOP_K_MIN, TOP_K_MAX = 1, 10
# Employee/source ids become directory names: restrict to a safe charset.
ID_PATTERN = r'^[A-Za-z0-9_-]{1,128}$'


def check_token_config() -> None:
    if not INTERNAL_TOKEN:
        raise RuntimeError('RAG_INTERNAL_TOKEN is empty; refusing to start the RAG service.')
    if INTERNAL_TOKEN == DEFAULT_TOKEN:
        logger.warning('RAG_INTERNAL_TOKEN is the default value; set a unique secret in production.')


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_token_config()
    yield


app = FastAPI(title='LiftBot RAG Engine', version='0.2.0', lifespan=lifespan)
store = EmployeeVectorStore(base_dir=os.getenv('RAG_INDEX_DIR', 'indexes'))
llm = LLMFallbackChain()


def verify_token(x_internal_token: Optional[str] = Header(default=None)):
    if not INTERNAL_TOKEN or not x_internal_token or not secrets.compare_digest(
        x_internal_token.encode('utf-8'), INTERNAL_TOKEN.encode('utf-8')
    ):
        raise HTTPException(status_code=401, detail='Unauthorized')


class IngestRequest(BaseModel):
    employee_id: str = Field(pattern=ID_PATTERN)
    source_id: str = Field(pattern=ID_PATTERN)
    title: str = ''
    text: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    employee_id: str = Field(pattern=ID_PATTERN)
    system_prompt: str = ''
    message: str
    history: List[ChatMessage] = Field(default_factory=list)
    top_k: int = 4
    capabilities: List[str] = Field(default_factory=list)
    # Optional retrieval query when it should differ from the visitor's words
    # (e.g. "do you have packages?" should pull the pricing chunks).
    search_query: str = ''


_HSPACE_RE = re.compile(r'[ \t\f\v ]+')
_MANY_NEWLINES_RE = re.compile(r'\n{3,}')


def normalise_text(text: str) -> str:
    """Collapse runs of horizontal whitespace; keep newlines (paragraph/FAQ breaks)."""
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = _HSPACE_RE.sub(' ', text)
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return _MANY_NEWLINES_RE.sub('\n\n', text).strip()


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> List[str]:
    text = normalise_text(text)
    if not text:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=['\n\n', '\n', '. ', ' ', ''],
    )
    return [c.strip() for c in splitter.split_text(text) if c.strip()]


def build_system_prompt(employee_prompt: str, context: str) -> str:
    return (
        f'{employee_prompt.strip()}\n\n'
        f'### KNOWLEDGE BASE CONTEXT ###\n'
        f'{context}\n\n'
        f'### STRICT INSTRUCTIONS ###\n'
        f'Facts:\n'
        f'- Use only the business information above for facts, prices, packages, timings and policies. '
        f'Never invent or estimate a price, package, date or feature.\n'
        f'- If the answer is not there, say so plainly in one short line and offer to have the team '
        f'follow up, e.g. "I don\'t have that detail handy, but I can get someone from the team to '
        f'confirm. Want me to?" Vary the wording; do not apologise repeatedly.\n'
        f'- Never mention "context", "knowledge base", "documents", "training data" or "the information '
        f'provided". Speak as someone who works here ("we", "our").\n'
        f'\n'
        f'Style (this is a live website chat, not an essay):\n'
        f'- Write like a friendly, sharp person from the team texting a customer. Natural, warm, direct.\n'
        f'- Lead with the answer. Simple questions get 1-3 short sentences, no lists.\n'
        f'- No headings, no tables, no "Great question!", "Certainly!", "I hope this helps", '
        f'"Feel free to reach out", "As an AI" or similar filler. Do not restate the question.\n'
        f'- Use a bullet list only when listing several items (packages, plans, options, steps). '
        f'Start each bullet with "- ". Keep bullets to one line each.\n'
        f'- When showing packages, plans or pricing, list every one you know about as: '
        f'"- **Package name** — price: what it includes". Then one short line inviting the next step.\n'
        f'- Use **bold** only for package names and prices. No other markdown.\n'
        f'- Ask at most one question per reply, and only when it moves things forward.\n'
        f'- Reply in the same language the visitor writes in.\n'
        f'- Never repeat yourself or send two versions of the same answer.'
    )


def sse_frame(token: str) -> str:
    # JSON-encode so newlines inside a token can't break SSE framing.
    return f'data: {json.dumps(token)}\n\n'


@app.get('/health')
def health():
    emb = store.embeddings
    return {
        'status': 'ok',
        'service': 'liftbot-rag',
        'providers': llm.configured(),
        'models': llm.models(),
        'embeddings': {'mode': emb.mode, 'model': emb.model, 'dim': emb.dim},
    }


@app.post('/ingest', dependencies=[Depends(verify_token)])
def ingest(body: IngestRequest):
    chunks = chunk_text(body.text)
    metadatas = [{'source_id': body.source_id, 'title': body.title, 'chunk': i} for i in range(len(chunks))]
    try:
        count, replaced = store.ingest(body.employee_id, body.source_id, chunks, metadatas)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Ingest failed for employee %s source %s', body.employee_id, body.source_id)
        raise HTTPException(status_code=502, detail=f'Embedding/ingest failed: {type(exc).__name__}') from exc
    return {'doc_id': f'{body.employee_id}:{body.source_id}', 'chunks': count, 'replaced': replaced}


@app.delete('/sources/{employee_id}/{source_id}', dependencies=[Depends(verify_token)])
def delete_source(
    employee_id: str = Path(pattern=ID_PATTERN),
    source_id: str = Path(pattern=ID_PATTERN),
):
    removed = store.delete_source(employee_id, source_id)
    return {'employee_id': employee_id, 'source_id': source_id, 'removed': removed}


@app.delete('/employees/{employee_id}', dependencies=[Depends(verify_token)])
def delete_employee(employee_id: str = Path(pattern=ID_PATTERN)):
    deleted = store.delete_employee(employee_id)
    return {'employee_id': employee_id, 'deleted': deleted}


@app.post('/chat', dependencies=[Depends(verify_token)])
def chat(body: ChatRequest):
    # store.search() ranks by hybrid (vector + BM25) score; no absolute cutoff
    # because the combined score is not a 0-1 similarity.
    top_k = max(TOP_K_MIN, min(TOP_K_MAX, body.top_k))
    try:
        hits = store.search(body.employee_id, body.search_query or body.message, top_k=top_k)
    except Exception:  # noqa: BLE001
        logger.exception('Retrieval failed for employee %s', body.employee_id)
        hits = []

    if hits:
        context = '\n\n'.join(text for text, _, _ in hits)
    else:
        context = 'No relevant training context available for this query.'
    system = build_system_prompt(body.system_prompt, context)

    history = [m.model_dump() for m in body.history]
    if not history or history[-1].get('content') != body.message:
        history.append({'role': 'visitor', 'content': body.message})

    def event_stream():
        try:
            for token in llm.stream(system, history):
                yield sse_frame(token)
        except Exception:  # noqa: BLE001
            logger.exception('Chat stream failed for employee %s', body.employee_id)
        yield 'data: [DONE]\n\n'

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
