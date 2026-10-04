"""Text embeddings.

* With ``GOOGLE_API_KEY`` set: Gemini (``google-genai`` SDK), model from
  ``GEMINI_EMBED_MODEL`` (default ``gemini-embedding-001``) truncated to
  ``EMBED_DIM`` (default 768) dims, batched, with retry/backoff on 429/5xx.
* Without a key: a deterministic hashed bag-of-words embedding. It is stable
  across processes/restarts (hashlib, not ``hash()``) and gives meaningful
  lexical similarity, so offline/dev retrieval actually works.

Every embedder exposes a ``signature`` string. The vector store records it
alongside stored vectors and re-embeds when it changes (e.g. a key was added
or the model was switched), so vectors from different spaces never mix.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time
from collections import Counter
from typing import List

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DIM = 768
EMBED_BATCH_SIZE = 100

_TOKEN_RE = re.compile(r'\w+', re.UNICODE)
_STOPWORDS = frozenset(
    'a an and are as at be but by can do does for from has have how i if in is it its '
    'me my of on or our so than that the their them then there these they this to us '
    'was we were what when where which who why will with you your'.split()
)


def tokenize(text: str) -> List[str]:
    """Lower-cased word tokens (shared by the hash embedder and BM25)."""
    return _TOKEN_RE.findall((text or '').lower())


def _l2_normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype('float32')


class HashEmbeddings:
    """Deterministic feature-hashed bag-of-words (unigrams + bigrams)."""

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim
        self.signature = f'hash-bow-v1:{dim}'
        self.mode = 'offline'
        self.model = 'hash-bow-v1'

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode('utf-8'), digest_size=8).digest()
        value = int.from_bytes(digest, 'little')
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return value % self.dim, sign

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype='float32')
        tokens = [t for t in tokenize(text) if t not in _STOPWORDS]
        feats: Counter = Counter(tokens)
        feats.update(f'{a} {b}' for a, b in zip(tokens, tokens[1:]))
        for feat, tf in feats.items():
            idx, sign = self._bucket(feat)
            weight = 1.0 + math.log(tf)
            if ' ' in feat:
                weight *= 0.5  # bigrams are a bonus, not the main signal
            vec[idx] += sign * weight
        return vec

    def embed_documents(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype='float32')
        return _l2_normalise(np.stack([self._embed_one(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class GeminiEmbeddings:
    """Gemini embeddings via the ``google-genai`` SDK."""

    def __init__(self, api_key: str, model: str | None = None, dim: int = DEFAULT_DIM):
        from google import genai
        from google.genai import types

        self._types = types
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=30_000),  # milliseconds
        )
        self.model = model or os.getenv('GEMINI_EMBED_MODEL', 'gemini-embedding-001')
        self.dim = dim
        self.signature = f'gemini:{self.model}:{dim}'
        self.mode = 'gemini'
        self.max_retries = int(os.getenv('EMBED_MAX_RETRIES', '5'))

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, 'code', None)
        if isinstance(code, int):
            return code == 429 or code >= 500
        name = type(exc).__name__.lower()
        return 'timeout' in name or 'connect' in name

    def _embed_batch(self, batch: List[str], task_type: str) -> List[List[float]]:
        config = self._types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.models.embed_content(model=self.model, contents=batch, config=config)
                values = [e.values for e in (resp.embeddings or [])]
                if len(values) != len(batch):
                    raise RuntimeError(f'Expected {len(batch)} embeddings, got {len(values)}')
                return values
            except Exception as exc:  # noqa: BLE001
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    raise
                logger.warning('Embedding call failed (%s); retrying in %.1fs', exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise RuntimeError('unreachable')

    def _embed(self, texts: List[str], task_type: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype='float32')
        rows: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            rows.extend(self._embed_batch(texts[start:start + EMBED_BATCH_SIZE], task_type))
        mat = np.asarray(rows, dtype='float32')
        if mat.ndim != 2 or mat.shape[1] != self.dim:
            raise RuntimeError(f'Unexpected embedding shape {mat.shape}')
        # Truncated (MRL) gemini-embedding-001 vectors are not unit length.
        return _l2_normalise(mat)

    def embed_documents(self, texts: List[str]) -> np.ndarray:
        return self._embed(texts, 'RETRIEVAL_DOCUMENT')

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], 'RETRIEVAL_QUERY')[0]


def get_embedder():
    dim = int(os.getenv('EMBED_DIM', str(DEFAULT_DIM)))
    api_key = os.getenv('GOOGLE_API_KEY', '').strip()
    if api_key:
        return GeminiEmbeddings(api_key=api_key, dim=dim)
    logger.warning('GOOGLE_API_KEY not set: using offline hashed bag-of-words embeddings')
    return HashEmbeddings(dim=dim)
