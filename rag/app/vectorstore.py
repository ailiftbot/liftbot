import os
import pickle
from pathlib import Path
from typing import List, Tuple

import faiss
import numpy as np
from rank_bm25 import BM25Okapi # ADDED

from .embeddings import GeminiEmbeddings


class EmployeeVectorStore:
    def __init__(self, base_dir: str = 'indexes'):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.embeddings = GeminiEmbeddings()

    def _paths(self, employee_id: str):
        folder = self.base_dir / str(employee_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / 'index.faiss', folder / 'meta.pkl'

    def ingest(self, employee_id: str, texts: List[str], metadatas: List[dict]) -> int:
        if not texts:
            return 0
        vectors = np.array(self.embeddings.embed_documents(texts), dtype='float32')
        faiss.normalize_L2(vectors)

        index_path, meta_path = self._paths(employee_id)
        if index_path.exists() and meta_path.exists():
            index = faiss.read_index(str(index_path))
            with open(meta_path, 'rb') as f:
                meta = pickle.load(f)
        else:
            index = faiss.IndexFlatIP(vectors.shape[1])
            meta = {'texts': [], 'metadatas': []}

        index.add(vectors)
        meta['texts'].extend(texts)
        meta['metadatas'].extend(metadatas)
        faiss.write_index(index, str(index_path))
        with open(meta_path, 'wb') as f:
            pickle.dump(meta, f)
        return len(texts)

    # HYBRID SEARCH (Vector + Keyword)
    def search(self, employee_id: str, query: str, top_k: int = 4) -> List[Tuple[str, dict, float]]:
        index_path, meta_path = self._paths(employee_id)
        if not index_path.exists():
            return []
        
        # 1. Vector Search
        index = faiss.read_index(str(index_path))
        with open(meta_path, 'rb') as f:
            meta = pickle.load(f)
        q = np.array([self.embeddings.embed_query(query)], dtype='float32')
        faiss.normalize_L2(q)
        scores, idxs = index.search(q, min(top_k * 2, index.ntotal or 1)) # Fetch 2x for mixing
        
        # 2. Keyword Search (BM25)
        tokenized_corpus = [doc.split() for doc in meta['texts']]
        bm25 = BM25Okapi(tokenized_corpus)
        bm25_scores = bm25.get_scores(query.split())
        
        # 3. Combine Scores (Hybrid)
        results = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0: continue
            hybrid_score = float(score) + (float(bm25_scores[idx]) * 0.5) # Weighted sum
            results.append((meta['texts'][idx], meta['metadatas'][idx], hybrid_score))
        
        # Sort by hybrid score and return top_k
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]