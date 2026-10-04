"""Per-employee hybrid (FAISS vector + BM25) store.

Layout: ``<base_dir>/<employee_id>/store.pkl`` holds everything for one
employee in ONE file (texts, metadatas, float32 vectors, embedder signature),
written atomically (temp file + ``os.replace``), so readers never see a torn
state and need no lock. Writers serialise per employee with a
``threading.Lock`` (same process) plus an ``fcntl`` exclusive lock on
``<base_dir>/.locks/<employee_id>.lock`` (across worker processes).

Loaded data (incl. the FAISS index and BM25 model) is cached per employee and
invalidated when the file's (mtime, inode, size) changes, so multiple workers
stay coherent.

Legacy layout (``index.faiss`` + ``meta.pkl``) is migrated on first write; its
texts are re-embedded because the old vectors came from a different model.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import math
import os
import pickle
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .embeddings import get_embedder, tokenize

logger = logging.getLogger(__name__)

STORE_FILE = 'store.pkl'
LEGACY_META = 'meta.pkl'
LEGACY_INDEX = 'index.faiss'
FORMAT_VERSION = 2
REEMBED_RETRY_SECONDS = 300
BM25_WEIGHT = 0.3


class _BM25(BM25Okapi):
    """Okapi BM25 with Lucene's always-positive IDF: log(1 + (N - n + 0.5) / (n + 0.5)).

    rank_bm25's classic IDF is 0 or negative for terms in >= half the docs,
    which zeroes out matches entirely on small knowledge bases.
    """

    def _calc_idf(self, nd):
        self.idf = {
            word: math.log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
            for word, freq in nd.items()
        }


@dataclass
class _Snapshot:
    texts: List[str]
    metadatas: List[dict]
    vectors: Optional[np.ndarray]  # None / wrong signature => needs re-embed
    signature: Optional[str]
    stamp: Tuple[int, int, int] = (0, 0, 0)
    _index: Optional[faiss.Index] = field(default=None, repr=False)
    _bm25: Optional[_BM25] = field(default=None, repr=False)
    _bm25_built: bool = field(default=False, repr=False)

    @property
    def index(self) -> Optional[faiss.Index]:
        if self._index is None and self.vectors is not None and len(self.vectors):
            idx = faiss.IndexFlatIP(self.vectors.shape[1])
            idx.add(self.vectors)
            self._index = idx
        return self._index

    @property
    def bm25(self) -> Optional[_BM25]:
        if not self._bm25_built:
            corpus = [tokenize(t) for t in self.texts]
            # BM25Okapi divides by corpus size / avg length; guard empty corpora.
            if corpus and any(corpus):
                self._bm25 = _BM25([doc or ['_'] for doc in corpus])
            self._bm25_built = True
        return self._bm25


class EmployeeVectorStore:
    def __init__(self, base_dir: str = 'indexes', embedder=None, cache_size: int | None = None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / '.locks').mkdir(exist_ok=True)
        self.embeddings = embedder or get_embedder()
        self._cache: 'OrderedDict[str, _Snapshot]' = OrderedDict()
        self._cache_size = cache_size or int(os.getenv('RAG_CACHE_SIZE', '64'))
        self._cache_lock = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._reembed_failed_at: Dict[str, float] = {}

    # ------------------------------------------------------------------ paths
    def _folder(self, employee_id: str) -> Path:
        return self.base_dir / str(employee_id)

    def _store_path(self, employee_id: str) -> Path:
        return self._folder(employee_id) / STORE_FILE

    # ---------------------------------------------------------------- locking
    @contextlib.contextmanager
    def _write_lock(self, employee_id: str) -> Iterator[None]:
        with self._locks_guard:
            tlock = self._locks.setdefault(employee_id, threading.Lock())
        with tlock:
            lock_path = self.base_dir / '.locks' / f'{employee_id}.lock'
            with open(lock_path, 'a+') as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------- load/save
    @staticmethod
    def _stamp(path: Path) -> Optional[Tuple[int, int, int]]:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_ino, st.st_size)

    def _load_legacy(self, employee_id: str) -> Optional[_Snapshot]:
        meta_path = self._folder(employee_id) / LEGACY_META
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, 'rb') as f:
                meta = pickle.load(f)
            return _Snapshot(
                texts=list(meta.get('texts', [])),
                metadatas=list(meta.get('metadatas', [])),
                vectors=None,  # old vectors are from another model: re-embed
                signature=None,
            )
        except Exception:  # noqa: BLE001
            logger.exception('Could not read legacy index for employee %s', employee_id)
            return None

    def _load(self, employee_id: str) -> Optional[_Snapshot]:
        """Return the current snapshot (cached unless the file changed)."""
        path = self._store_path(employee_id)
        stamp = self._stamp(path)
        if stamp is None:
            with self._cache_lock:
                self._cache.pop(employee_id, None)
            return self._load_legacy(employee_id)

        with self._cache_lock:
            snap = self._cache.get(employee_id)
            if snap is not None and snap.stamp == stamp:
                self._cache.move_to_end(employee_id)
                return snap

        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
        except Exception:  # noqa: BLE001
            logger.exception('Corrupt store for employee %s', employee_id)
            return None
        vectors = data.get('vectors')
        if vectors is not None:
            vectors = np.ascontiguousarray(vectors, dtype='float32')
        snap = _Snapshot(
            texts=list(data.get('texts', [])),
            metadatas=list(data.get('metadatas', [])),
            vectors=vectors,
            signature=data.get('signature'),
            stamp=stamp,
        )
        with self._cache_lock:
            self._cache[employee_id] = snap
            self._cache.move_to_end(employee_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return snap

    def _save(self, employee_id: str, texts: List[str], metadatas: List[dict],
              vectors: Optional[np.ndarray], signature: Optional[str]) -> None:
        folder = self._folder(employee_id)
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': FORMAT_VERSION,
            'texts': texts,
            'metadatas': metadatas,
            'vectors': vectors,
            'signature': signature,
        }
        fd, tmp = tempfile.mkstemp(dir=folder, prefix='.store-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, folder / STORE_FILE)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        for legacy in (LEGACY_META, LEGACY_INDEX):
            with contextlib.suppress(FileNotFoundError):
                (folder / legacy).unlink()
        with self._cache_lock:
            self._cache.pop(employee_id, None)

    def _vectors_current(self, snap: _Snapshot) -> bool:
        return (
            snap.vectors is not None
            and snap.signature == self.embeddings.signature
            and snap.vectors.ndim == 2
            and snap.vectors.shape == (len(snap.texts), self.embeddings.dim)
        )

    # ----------------------------------------------------------------- writes
    def ingest(self, employee_id: str, source_id: str, texts: List[str], metadatas: List[dict]) -> Tuple[int, int]:
        """Replace all chunks of ``source_id`` with ``texts``. Returns (added, removed).

        Embedding happens before taking the lock; failures raise and leave the
        stored data untouched.
        """
        new_vecs = self.embeddings.embed_documents(texts) if texts else None
        with self._write_lock(employee_id):
            snap = self._load(employee_id)
            old_texts = snap.texts if snap else []
            old_metas = snap.metadatas if snap else []
            keep = [i for i, m in enumerate(old_metas) if str(m.get('source_id')) != str(source_id)]
            removed = len(old_metas) - len(keep)
            kept_texts = [old_texts[i] for i in keep]
            kept_metas = [old_metas[i] for i in keep]
            if snap is not None and self._vectors_current(snap):
                kept_vecs = snap.vectors[keep]
            else:
                kept_vecs = self.embeddings.embed_documents(kept_texts)  # migrate/re-embed
            parts = [kept_vecs] + ([new_vecs] if new_vecs is not None else [])
            vectors = np.vstack(parts).astype('float32') if parts else None
            if not texts and snap is None:
                return 0, 0
            self._save(employee_id, kept_texts + list(texts), kept_metas + list(metadatas),
                       vectors, self.embeddings.signature)
        return len(texts), removed

    def delete_source(self, employee_id: str, source_id: str) -> int:
        with self._write_lock(employee_id):
            snap = self._load(employee_id)
            if snap is None:
                return 0
            keep = [i for i, m in enumerate(snap.metadatas) if str(m.get('source_id')) != str(source_id)]
            removed = len(snap.metadatas) - len(keep)
            if removed == 0:
                return 0
            current = self._vectors_current(snap)
            self._save(
                employee_id,
                [snap.texts[i] for i in keep],
                [snap.metadatas[i] for i in keep],
                snap.vectors[keep] if current else None,
                snap.signature if current else None,
            )
        return removed

    def delete_employee(self, employee_id: str) -> bool:
        with self._write_lock(employee_id):
            folder = self._folder(employee_id)
            existed = folder.exists()
            if existed:
                shutil.rmtree(folder, ignore_errors=True)
            with self._cache_lock:
                self._cache.pop(employee_id, None)
        return existed

    def _try_reembed(self, employee_id: str) -> Optional[_Snapshot]:
        """Re-embed stored chunks with the current embedder (model/dim changed)."""
        failed_at = self._reembed_failed_at.get(employee_id)
        if failed_at and time.monotonic() - failed_at < REEMBED_RETRY_SECONDS:
            return None
        try:
            with self._write_lock(employee_id):
                snap = self._load(employee_id)
                if snap is None or self._vectors_current(snap):
                    return snap
                logger.info('Re-embedding %d chunks for employee %s (%s -> %s)', len(snap.texts),
                            employee_id, snap.signature, self.embeddings.signature)
                vectors = self.embeddings.embed_documents(snap.texts)
                self._save(employee_id, snap.texts, snap.metadatas, vectors, self.embeddings.signature)
            self._reembed_failed_at.pop(employee_id, None)
            return self._load(employee_id)
        except Exception:  # noqa: BLE001
            logger.exception('Re-embedding failed for employee %s; using BM25 only', employee_id)
            self._reembed_failed_at[employee_id] = time.monotonic()
            return None

    # ------------------------------------------------------------------ reads
    def stats(self, employee_id: str) -> dict:
        snap = self._load(employee_id)
        if snap is None:
            return {'chunks': 0, 'sources': 0}
        return {'chunks': len(snap.texts), 'sources': len({str(m.get('source_id')) for m in snap.metadatas})}

    def search(self, employee_id: str, query: str, top_k: int = 4) -> List[Tuple[str, dict, float]]:
        """Hybrid search. Never raises for data/embedding problems: degrades to BM25."""
        snap = self._load(employee_id)
        if snap is None or not snap.texts:
            return []
        if not self._vectors_current(snap):
            snap = self._try_reembed(employee_id) or snap

        n = len(snap.texts)
        pool = min(n, max(top_k * 3, 10))
        candidates: Dict[int, float] = {}  # idx -> vector score

        # 1. BM25 (lexical), normalised to 0..1
        bm25_norm = np.zeros(n, dtype='float32')
        q_tokens = tokenize(query)
        if snap.bm25 is not None and q_tokens:
            raw = np.clip(np.asarray(snap.bm25.get_scores(q_tokens), dtype='float32'), 0, None)
            if raw.size and raw.max() > 0:
                bm25_norm = raw / raw.max()

        # 2. Vector search (if vectors usable and the query embeds)
        qvec = None
        if self._vectors_current(snap) and snap.index is not None:
            try:
                qvec = np.asarray(self.embeddings.embed_query(query), dtype='float32').reshape(1, -1)
                faiss.normalize_L2(qvec)
                scores, idxs = snap.index.search(qvec, pool)
                for s, i in zip(scores[0], idxs[0]):
                    if i >= 0:
                        candidates[int(i)] = float(s)
            except Exception:  # noqa: BLE001
                logger.exception('Query embedding failed for employee %s; BM25 only', employee_id)
                qvec = None

        # 3. Merge BM25 top hits into the candidate set
        for i in np.argsort(-bm25_norm)[:pool]:
            i = int(i)
            if bm25_norm[i] <= 0 or i in candidates:
                continue
            candidates[i] = float(snap.vectors[i] @ qvec[0]) if qvec is not None else 0.0

        results = [
            (snap.texts[i], snap.metadatas[i], max(vs, 0.0) + BM25_WEIGHT * float(bm25_norm[i]))
            for i, vs in candidates.items()
        ]
        results.sort(key=lambda r: r[2], reverse=True)
        return results[:top_k]
