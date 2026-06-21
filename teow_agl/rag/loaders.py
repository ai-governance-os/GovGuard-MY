"""Document loaders for RAG. Returns plain text per file."""
from __future__ import annotations

from pathlib import Path


def load_text(path: Path) -> str:
    """Best-effort plain-text extraction. Returns empty string on failure."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".md", ".txt", ".rst", ".log", ".json", ".yaml", ".yml", ".csv"):
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".docx":
            return _load_docx(path)
        if suffix == ".pdf":
            return _load_pdf(path)
        # unknown extension — try as text anyway, give up if it's binary
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ""
    except Exception:
        return ""


def _load_docx(path: Path) -> str:
    try:
        from docx import Document  # type: ignore
    except ImportError:
        return ""
    try:
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def _load_pdf(path: Path) -> str:
    """Optional PDF support — falls back to '' if pypdf isn't installed."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


SUPPORTED_EXTENSIONS = (
    ".md", ".txt", ".rst", ".log", ".json", ".yaml", ".yml", ".csv",
    ".docx", ".pdf",
)
