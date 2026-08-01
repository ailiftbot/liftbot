import os
from typing import List

import google.generativeai as genai
import numpy as np


class GeminiEmbeddings:
    """Google Gemini text-embedding-004."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
        if self.api_key:
            genai.configure(api_key=self.api_key)
        self.model = 'models/text-embedding-004'

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key:
            # Deterministic local fallback so Docker boots without keys
            return [self._hash_embed(t) for t in texts]
        vectors = []
        for text in texts:
            result = genai.embed_content(model=self.model, content=text)
            vectors.append(result['embedding'])
        return vectors

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

    @staticmethod
    def _hash_embed(text: str, dim: int = 768) -> List[float]:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(dim)
        vec = vec / (np.linalg.norm(vec) + 1e-9)
        return vec.tolist()
