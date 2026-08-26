import os
import re
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_text_splitters import RecursiveCharacterTextSplitter # ADDED

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


# SMART CHUNKING (Sentences are respected now)
def chunk_text(text: str, size: int = 800, overlap: int = 120) -> List[str]:
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = splitter.split_text(text)
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
    # Retrieve the best-matching chunks. store.search() already ranks by
    # hybrid (vector + keyword) score and returns them in order, so we just
    # take the top few — no extra absolute cutoff here. A fixed threshold
    # like ">= 0.7" assumes the score is always a 0-1 cosine similarity;
    # once BM25 was mixed in that assumption broke, and short common
    # queries ("phone", "address") were silently dropping every chunk,
    # which is why the bot kept saying it had no information.
    hits = store.search(body.employee_id, body.message, top_k=4)
    top_hits = hits

    if not top_hits:
        context = "No relevant training context available for this query."
    else:
        context = '\n\n'.join(text for text, _, _ in top_hits)

    # Structured Prompt (Clean without Source Index)
    system = (
        f"### ROLE ###\n"
        f"You are {body.system_prompt}\n\n"
        
        f"### KNOWLEDGE BASE CONTEXT ###\n"
        f"{context}\n\n"
        
        f"### STRICT INSTRUCTIONS ###\n"
        f"1. Answer ONLY using the context provided above.\n"
        f"2. If the context does not contain the answer, say: 'I am sorry, I don't have that specific information yet. I will note it down and follow up.'\n"
        f"3. Do NOT invent facts or make up information.\n"
        f"4. **CRITICAL: NEVER repeat the same response or text twice.** If you have already said it, do not say it again. Always provide a unique, concise answer.\n"
        f"5. If the user asks about pricing or scheduling, proactively suggest the 'Schedule' or 'Contact' action.\n"
        f"6. Format your answer in short, easy-to-read bullet points."
    )

    history = [m.model_dump() for m in body.history]
    if not history or history[-1].get('content') != body.message:
        history.append({'role': 'visitor', 'content': body.message})

    def event_stream():
        for token in llm.stream(system, history):
            yield f'data: {token}\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(event_stream(), media_type='text/event-stream')