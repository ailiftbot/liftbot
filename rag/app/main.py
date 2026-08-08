import os
import re
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .llm import LLMFallbackChain
from .vectorstore import EmployeeVectorStore

app = FastAPI(title='LiftBot RAG Engine', version='0.1.0')
store = EmployeeVectorStore(base_dir=os.getenv('RAG_INDEX_DIR', 'indexes'))
llm = LLMFallbackChain()
INTERNAL_TOKEN = os.getenv('RAG_INTERNAL_TOKEN', 'liftbot-rag-internal-token')


def verify_token(x_internal_token: Optional[str] = Header(default=None)):
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail='Unauthorized')


class IngestRequest(BaseModel):
    employee_id: str
    source_id: str
    title: str = ''
    text: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    employee_id: str
    system_prompt: str
    message: str
    history: List[ChatMessage] = Field(default_factory=list)
    top_k: int = 4
    capabilities: List[str] = Field(default_factory=list)


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> List[str]:
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'liftbot-rag'}


@app.post('/ingest', dependencies=[Depends(verify_token)])
def ingest(body: IngestRequest):
    chunks = chunk_text(body.text)
    metadatas = [{'source_id': body.source_id, 'title': body.title, 'chunk': i} for i in range(len(chunks))]
    count = store.ingest(body.employee_id, chunks, metadatas)
    return {'doc_id': f'{body.employee_id}:{body.source_id}', 'chunks': count}


@app.post('/chat', dependencies=[Depends(verify_token)])
def chat(body: ChatRequest):
    hits = store.search(body.employee_id, body.message, top_k=body.top_k)
    context = '\n\n'.join(f'[{i+1}] {text}' for i, (text, _, _) in enumerate(hits)) or 'No training context available.'
    system = (
        f'{body.system_prompt}\n\n'
        f'Context from training materials:\n{context}\n\n'
        'Answer as a proactive AI Employee. Take action when appropriate — qualify, collect details, offer scheduling, confirm team handoff.\n'
        'If the context does not contain the answer, say you will note it and follow up — do not invent facts.'
    )
    history = [m.model_dump() for m in body.history]
    if not history or history[-1].get('content') != body.message:
        history.append({'role': 'visitor', 'content': body.message})

    def event_stream():
        for token in llm.stream(system, history):
            yield f'data: {token}\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(event_stream(), media_type='text/event-stream')
