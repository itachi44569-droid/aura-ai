"""
RAG (Retrieval-Augmented Generation) system.
- Vector store: ChromaDB (free, local)
- Embeddings:   sentence-transformers all-MiniLM-L6-v2 (free, runs locally)
- Ingest:       plain text, .txt, .pdf, .json, .md files
- Each client gets their own isolated collection.
"""
import os
import re
from pathlib import Path
from typing import Optional

# ── Lazy imports (only loaded if RAG is enabled) ───────────────────────────────

_chromadb   = None
_embedder   = None
_chroma_client = None

def _get_chroma(persist_dir: str):
    global _chromadb, _chroma_client
    if _chroma_client is None:
        import chromadb
        _chromadb      = chromadb
        _chroma_client = chromadb.PersistentClient(path=persist_dir)
    return _chroma_client

def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")  # 80MB, very fast
    return _embedder

# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk(text: str, size: int = 400, overlap: int = 80) -> list[str]:
    """Split text into overlapping chunks for better retrieval."""
    text   = re.sub(r"\s+", " ", text).strip()
    chunks = []
    start  = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start += size - overlap
    return chunks

# ── RAG class ─────────────────────────────────────────────────────────────────

class RAG:
    def __init__(self, persist_dir: str = "./chroma_db"):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

    def _collection(self, client_id: str):
        client = _get_chroma(self.persist_dir)
        # Sanitize collection name (ChromaDB rules)
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id)[:63] or "default"
        return client.get_or_create_collection(name=safe)

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_text(self, text: str, source: str = "manual", client_id: str = "default"):
        """Ingest raw text into the vector store."""
        chunks    = _chunk(text)
        embedder  = _get_embedder()
        vectors   = embedder.encode(chunks).tolist()
        col       = self._collection(client_id)
        col.add(
            documents  = chunks,
            embeddings = vectors,
            ids        = [f"{source}_{i}" for i in range(len(chunks))],
            metadatas  = [{"source": source, "chunk": i} for i in range(len(chunks))],
        )
        return len(chunks)

    def ingest_file(self, path: str, client_id: str = "default") -> int:
        """Ingest a file (.txt, .md, .pdf, .json) into the vector store."""
        p    = Path(path)
        ext  = p.suffix.lower()
        text = ""
        if ext in (".txt", ".md"):
            text = p.read_text(encoding="utf-8", errors="ignore")
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(p))
                text   = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                raise ImportError("Install pypdf: pip install pypdf")
        elif ext == ".json":
            import json
            data = json.loads(p.read_text())
            text = json.dumps(data, indent=2)
        else:
            raise ValueError(f"Unsupported file type: {ext}")
        if not text.strip():
            return 0
        return self.ingest_text(text, source=p.name, client_id=client_id)

    def ingest_url(self, url: str, client_id: str = "default") -> int:
        """Fetch a webpage and ingest its text content."""
        import urllib.request
        from html.parser import HTMLParser
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
                self._skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ("script","style","nav","footer"):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ("script","style","nav","footer"):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip and data.strip():
                    self.parts.append(data.strip())
        req  = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8","ignore")
        p    = TextExtractor()
        p.feed(html)
        text = " ".join(p.parts)
        return self.ingest_text(text, source=url, client_id=client_id)

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(self, query: str, client_id: str = "default", top_k: int = 3) -> list[str]:
        """Return the top-k most relevant text chunks for a query."""
        try:
            col      = self._collection(client_id)
            count    = col.count()
            if count == 0:
                return []
            embedder = _get_embedder()
            vec      = embedder.encode([query]).tolist()
            results  = col.query(
                query_embeddings = vec,
                n_results        = min(top_k, count),
                include          = ["documents","metadatas"],
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            out = []
            for doc, meta in zip(docs, metas):
                src = meta.get("source", "")
                out.append(f"[{src}] {doc}" if src else doc)
            return out
        except Exception:
            return []

    def list_sources(self, client_id: str = "default") -> list[str]:
        """List all ingested source names for a client."""
        try:
            col   = self._collection(client_id)
            items = col.get(include=["metadatas"])
            seen  = set()
            for m in items.get("metadatas", []):
                seen.add(m.get("source","unknown"))
            return sorted(seen)
        except Exception:
            return []

    def clear(self, client_id: str = "default"):
        """Remove all documents for a client."""
        try:
            client = _get_chroma(self.persist_dir)
            safe   = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id)[:63]
            client.delete_collection(safe)
        except Exception:
            pass
