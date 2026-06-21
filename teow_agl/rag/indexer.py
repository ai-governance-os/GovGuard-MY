"""
RAG indexer: scan workspace_roots, chunk each supported file, persist
to a JSONL index. Honors profile.sensitive_patterns so credentials and
private files don't get embedded into LLM-bound briefs.

Index file format (one JSON object per line):
  {"chunk_id": "f0001_c00", "path": "...", "text": "...", "tokens": [...], "len": 123}

Plus a header line:
  {"_header": true, "version": "10.7.3", "created_at": "...", "n_files": 12, "n_chunks": 87}
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..policies.governance_profile import ProfileView
from ..util.path_guard import matches_any
from .bm25 import tokenize
from .loaders import SUPPORTED_EXTENSIONS, load_text


def _chunk_text(text: str, *, target: int = 700, overlap: int = 80) -> list[str]:
    """Sliding-window chunker on character count. Good enough for BM25;
    no token budget concerns since we're not feeding it to an LLM directly."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]
    chunks: list[str] = []
    i = 0
    while i < len(text):
        end = min(len(text), i + target)
        # try to break at a paragraph or sentence boundary near `end`
        if end < len(text):
            for boundary in ("\n\n", "\n", ". ", ".  "):
                cut = text.rfind(boundary, i, end)
                if cut != -1 and cut > i + target // 2:
                    end = cut + len(boundary)
                    break
        chunks.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return [c for c in chunks if c]


def _is_excluded(path: str, profile: ProfileView) -> bool:
    # never index credentials, .env, anything matching sensitive_patterns
    return matches_any(path, profile.sensitive_patterns)


def _walk_files(roots: list[str]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        p = Path(root)
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                out.append(p)
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            # skip RAG state itself, traces, screenshots
            s = str(f).replace("\\", "/").lower()
            if "/state/rag/" in s or "/traces/" in s or "/_screenshots/" in s:
                continue
            out.append(f)
    return out


def build_index(
    *,
    roots: list[str],
    profile: ProfileView,
    out_path: Path,
    chunk_target: int = 700,
    chunk_overlap: int = 80,
) -> dict:
    """Build a fresh index. Returns header summary dict."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = _walk_files(roots)
    n_files = 0
    n_chunks = 0
    excluded: list[str] = []
    started = time.time()

    with out_path.open("w", encoding="utf-8") as f:
        # write header LAST (after we know counts) — for now write a placeholder, then rewrite
        f.write(json.dumps({"_header": True, "version": "10.7.3-rag-indexing"}) + "\n")
        for fp in files:
            spath = str(fp)
            if _is_excluded(spath, profile):
                excluded.append(spath)
                continue
            text = load_text(fp)
            if not text:
                continue
            file_id = hashlib.md5(spath.encode("utf-8")).hexdigest()[:10]
            chunks = _chunk_text(text, target=chunk_target, overlap=chunk_overlap)
            if not chunks:
                continue
            n_files += 1
            for i, chunk in enumerate(chunks):
                chunk_id = f"{file_id}_c{i:03d}"
                tokens = tokenize(chunk)
                f.write(json.dumps({
                    "chunk_id": chunk_id, "path": spath, "text": chunk,
                    "tokens": tokens, "len": len(chunk),
                }, ensure_ascii=False) + "\n")
                n_chunks += 1

    header = {
        "_header": True, "version": "10.7.3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 2),
        "n_files": n_files, "n_chunks": n_chunks,
        "n_excluded": len(excluded), "roots": list(roots),
    }
    # rewrite header line in place by reading + replacing first line
    body = out_path.read_text(encoding="utf-8").split("\n", 1)
    out_path.write_text(json.dumps(header) + "\n" + (body[1] if len(body) > 1 else ""),
                        encoding="utf-8")
    return header


def index_summary(index_path: Path) -> dict | None:
    if not index_path.exists():
        return None
    with index_path.open("r", encoding="utf-8") as f:
        first = f.readline().strip()
    if not first:
        return None
    try:
        header = json.loads(first)
    except json.JSONDecodeError:
        return None
    if not header.get("_header"):
        return None
    return header
