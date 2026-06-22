"""
Hybrid RAG — combines BM25 keyword search with semantic (vector) search.
Uses rank-bm25 library (free, local) + existing ChromaDB vectors.

Why hybrid?
  BM25 is great at exact keyword matches ("AAPL", "section 4.2", names).
  Semantic search is great at meaning matches ("how does photosynthesis work").
  Combining both → much better recall.

Score fusion: RRF (Reciprocal Rank Fusion) — simple, parameter-free, proven.
"""
from __future__ import annotations
import re
from typing import Optional


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


class HybridRAG:
    def __init__(self, rag_instance=None):
        """
        rag_instance: existing RAG object from core/rag.py
        corpus is built automatically when documents are ingested.
        """
        self.rag       = rag_instance
        self._corpus:  list[str]       = []
        self._bm25                     = None

    def _rebuild_bm25(self):
        if not self._corpus:
            return
        try:
            from rank_bm25 import BM25Okapi
            tokenized     = [_tokenize(doc) for doc in self._corpus]
            self._bm25    = BM25Okapi(tokenized)
        except ImportError:
            self._bm25 = None  # graceful degradation

    def add_documents(self, docs: list[str]):
        self._corpus.extend(docs)
        self._rebuild_bm25()

    async def search(self, query: str, user_id: str = "default",
                     top_k: int = 5) -> list[str]:
        semantic_results = []
        bm25_results     = []

        # 1. Semantic search via existing RAG
        if self.rag:
            try:
                semantic_results = await self.rag.search(query, user_id=user_id, top_k=top_k)
            except Exception:
                pass

        # 2. BM25 keyword search
        if self._bm25 and self._corpus:
            try:
                tokens = _tokenize(query)
                scores = self._bm25.get_scores(tokens)
                ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
                bm25_results = [self._corpus[i] for i, s in ranked[:top_k] if s > 0]
            except Exception:
                pass

        # 3. RRF fusion
        if not semantic_results and not bm25_results:
            return []
        if not semantic_results:
            return bm25_results[:top_k]
        if not bm25_results:
            return semantic_results[:top_k]

        scores: dict[str, float] = {}
        K = 60  # RRF constant
        for rank, doc in enumerate(semantic_results):
            scores[doc] = scores.get(doc, 0) + 1.0 / (K + rank + 1)
        for rank, doc in enumerate(bm25_results):
            scores[doc] = scores.get(doc, 0) + 1.0 / (K + rank + 1)

        fused = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
        return fused[:top_k]
