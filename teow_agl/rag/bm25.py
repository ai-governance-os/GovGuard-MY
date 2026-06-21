"""
Pure-Python BM25 ranker. Zero dependencies, ~300 LOC, plenty fast for
single-user personal corpora (<10k chunks).

BM25 is a 30-year-old keyword retrieval algorithm that powers Lucene,
Elasticsearch, and most pre-LLM search engines. For "find relevant
chunks of my own notes given a query" it's solid — high precision when
the query and the docs share vocabulary, which they usually do for
personal workflows.

Upgrade path: swap out `score_query` with neural embeddings later
without touching the rest of the system.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable


# Conservative English-ish stopwords. Edit configs/rag_stopwords.json to
# extend or replace at runtime — for now keep it built-in and minimal so
# we don't strip too aggressively.
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "he", "in", "is", "it", "its", "of", "on", "or",
    "she", "that", "the", "this", "to", "was", "were", "will", "with",
    "i", "you", "we", "they", "them", "us", "our", "your", "their",
    "but", "not", "can", "could", "should", "would", "may", "might",
    "do", "does", "did", "done", "had",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text) if t and t.lower() not in _STOP]


class BM25Index:
    """In-memory BM25 over a list of (chunk_id, tokens) pairs.

    Standard parameters: k1=1.5, b=0.75. These work well for
    document-length corpora 100-1000 chars per chunk.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: list[tuple[str, list[str]]] = []   # [(chunk_id, tokens)]
        self.doc_freq: Counter = Counter()             # term -> num docs containing it
        self.doc_len: list[int] = []
        self.avg_dl: float = 0.0
        self.N: int = 0

    def add(self, chunk_id: str, tokens: list[str]) -> None:
        self.docs.append((chunk_id, tokens))
        self.doc_len.append(len(tokens))
        for t in set(tokens):
            self.doc_freq[t] += 1

    def finalize(self) -> None:
        self.N = len(self.docs)
        self.avg_dl = (sum(self.doc_len) / self.N) if self.N else 0.0

    def score_query(self, query_tokens: list[str], top_k: int = 5) -> list[tuple[str, float]]:
        if not self.docs or not query_tokens:
            return []
        scores: list[tuple[str, float]] = []
        for i, (chunk_id, tokens) in enumerate(self.docs):
            if not tokens:
                continue
            tf = Counter(tokens)
            dl = self.doc_len[i] or 1
            score = 0.0
            for q in query_tokens:
                df = self.doc_freq.get(q, 0)
                if df == 0:
                    continue
                idf = math.log(1 + (self.N - df + 0.5) / (df + 0.5))
                term_freq = tf.get(q, 0)
                if term_freq == 0:
                    continue
                norm = 1 - self.b + self.b * (dl / (self.avg_dl or 1))
                score += idf * (term_freq * (self.k1 + 1)) / (term_freq + self.k1 * norm)
            if score > 0:
                scores.append((chunk_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
