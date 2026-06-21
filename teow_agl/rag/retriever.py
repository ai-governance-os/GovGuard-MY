"""
Load a RAG index and run BM25 queries against it.

Usage in runtime:
    r = Retriever(Path('state/rag/index.jsonl'))
    chunks = r.query("summarize my notes about X", top_k=5)
"""
from __future__ import annotations

import json
from pathlib import Path

from .bm25 import BM25Index, tokenize


class Retriever:
    def __init__(self, index_path: Path) -> None:
        self.index_path = Path(index_path)
        self.bm25 = BM25Index()
        self.chunks_by_id: dict[str, dict] = {}
        self.header: dict = {}
        self._load()

    @property
    def loaded(self) -> bool:
        return bool(self.chunks_by_id)

    @property
    def n_chunks(self) -> int:
        return len(self.chunks_by_id)

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        with self.index_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_header"):
                    self.header = rec
                    continue
                cid = rec.get("chunk_id")
                if not cid:
                    continue
                self.chunks_by_id[cid] = rec
                self.bm25.add(cid, rec.get("tokens", []))
        self.bm25.finalize()

    def query(self, query: str, top_k: int = 5, max_chars_per_chunk: int = 1200) -> list[dict]:
        if not self.loaded:
            return []
        toks = tokenize(query)
        if not toks:
            return []
        hits = self.bm25.score_query(toks, top_k=top_k)
        out: list[dict] = []
        for chunk_id, score in hits:
            rec = self.chunks_by_id.get(chunk_id)
            if not rec:
                continue
            text = rec.get("text", "")
            if max_chars_per_chunk and len(text) > max_chars_per_chunk:
                text = text[:max_chars_per_chunk] + "…"
            out.append({
                "chunk_id": chunk_id,
                "path": rec.get("path", ""),
                "score": round(float(score), 3),
                "text": text,
            })
        return out
