"""BM25 lexical search: our own implementation, no external ranking library.

Tokenizer: lowercase, split on runs of non-alphanumeric characters, drop
English stopwords (~50 words), keep whatever is left that is either at
least 2 characters long or is a number (so single digits like "9" survive).
No stemming.

Index: an inverted index (term -> {doc_id: term_freq}), a document frequency
per term, and a token length per document. Scored with the standard BM25
formula, k1 = 1.5, b = 0.75. Persisted with pickle to
data/bm25/{index_name}.pkl, loaded lazily by `BM25Index.load`.
"""

from __future__ import annotations

import math
import pickle
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BM25_DIR = PROJECT_ROOT / "data" / "bm25"

K1 = 1.5
B = 0.75

# About 50 common English stopwords. Judgment call: a short, unsurprising
# list rather than importing a stopword package (kept dependency-free).
STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if then so because as until while of at by for
    with about against between into through during before after above
    below to from up down in out on off over under again further once
    here there all any both each few more most
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric runs, drop stopwords and 1-char
    non-numeric tokens. No stemming."""
    tokens = _TOKEN_RE.findall(text.lower())
    out = []
    for tok in tokens:
        if tok in STOPWORDS:
            continue
        if tok.isdigit() or len(tok) >= 2:
            out.append(tok)
    return out


@dataclass
class BM25Index:
    """An inverted index over chunk `embed_text`, scored with BM25."""

    index_name: str
    postings: dict[str, dict[object, int]] = field(default_factory=dict)  # term -> {id: tf}
    doc_freq: dict[str, int] = field(default_factory=dict)  # term -> number of docs containing it
    doc_len: dict[object, int] = field(default_factory=dict)  # id -> token count
    doc_cik: dict[object, str] = field(default_factory=dict)  # id -> cik, when given
    n_docs: int = 0
    avgdl: float = 0.0

    @classmethod
    def build(cls, index_name: str, rows: Iterable[Sequence]) -> "BM25Index":
        """Build from an iterable of (id, embed_text) or (id, embed_text, cik) rows."""
        postings: dict[str, dict[object, int]] = defaultdict(dict)
        doc_len: dict[object, int] = {}
        doc_cik: dict[object, str] = {}
        n = 0
        total_len = 0
        for row in rows:
            if len(row) >= 3:
                doc_id, text, cik = row[0], row[1], row[2]
                if cik is not None:
                    doc_cik[doc_id] = cik
            else:
                doc_id, text = row[0], row[1]
            tokens = tokenize(text or "")
            doc_len[doc_id] = len(tokens)
            total_len += len(tokens)
            n += 1
            for term, count in Counter(tokens).items():
                postings[term][doc_id] = count

        idx = cls(index_name=index_name)
        idx.postings = dict(postings)
        idx.doc_freq = {term: len(docs) for term, docs in postings.items()}
        idx.doc_len = doc_len
        idx.doc_cik = doc_cik
        idx.n_docs = n
        idx.avgdl = (total_len / n) if n else 0.0
        return idx

    @staticmethod
    def _path_for(index_name: str) -> Path:
        return BM25_DIR / f"{index_name}.pkl"

    def save(self, path: Path | None = None) -> Path:
        out = path or self._path_for(self.index_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            pickle.dump(self, f)
        return out

    @classmethod
    def load(cls, index_name: str, path: Path | None = None) -> "BM25Index":
        src = path or cls._path_for(index_name)
        with src.open("rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"{src} does not contain a BM25Index")
        return obj

    def _idf(self, term: str) -> float:
        df = self.doc_freq.get(term, 0)
        # BM25 (Robertson/Okapi) idf with the +0.5 smoothing; floors at a
        # small positive number instead of going negative for very common
        # terms in a small corpus.
        return math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, doc_id: object, query_terms: Sequence[str]) -> float:
        """BM25 score of one document against a (deduplicated) term list."""
        dl = self.doc_len.get(doc_id, 0)
        avgdl = self.avgdl or 1.0
        total = 0.0
        for term in query_terms:
            docs = self.postings.get(term)
            if not docs or doc_id not in docs:
                continue
            tf = docs[doc_id]
            idf = self._idf(term)
            denom = tf + K1 * (1 - B + B * dl / avgdl)
            total += idf * (tf * (K1 + 1)) / denom
        return total

    def search(
        self,
        query: str,
        k: int = 50,
        filter_ids: Iterable[object] | None = None,
        ciks: Iterable[str] | None = None,
    ) -> list[tuple[object, float]]:
        """Top-k (id, score) pairs for `query`, best first.

        `filter_ids` restricts to a given id set (e.g. from a SQL query on
        cik). `ciks` restricts using the cik-per-id map recorded at build
        time. Both may be given together (intersection).
        """
        q_terms = list(dict.fromkeys(tokenize(query)))  # unique, order-preserving
        if not q_terms:
            return []

        allowed: set[object] | None = None
        if filter_ids is not None:
            allowed = set(filter_ids)
        if ciks is not None:
            cik_set = set(ciks)
            cik_ids = {doc_id for doc_id, c in self.doc_cik.items() if c in cik_set}
            allowed = cik_ids if allowed is None else (allowed & cik_ids)

        candidate_ids: set[object] = set()
        for term in q_terms:
            docs = self.postings.get(term)
            if docs:
                candidate_ids.update(docs.keys())
        if allowed is not None:
            candidate_ids &= allowed

        scored = [(doc_id, self.score(doc_id, q_terms)) for doc_id in candidate_ids]
        scored = [(doc_id, s) for doc_id, s in scored if s > 0]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:k]


def load_rows_from_db(index_name: str, conn, schema: str | None = None) -> list[tuple]:
    """Fetch (id, embed_text, cik) rows for `chunks_{index_name}` for `build`.

    A thin helper kept here (rather than in vector.py) because it feeds
    BM25.build directly. Not exercised against the real corpus in this wave.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", index_name):
        raise ValueError(f"unsafe index_name: {index_name!r}")
    table = f"{schema}.chunks_{index_name}" if schema else f"chunks_{index_name}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, embed_text, cik FROM {table}")
        return cur.fetchall()


def build_and_save_from_db(index_name: str, conn, schema: str | None = None) -> Path:
    rows = load_rows_from_db(index_name, conn, schema=schema)
    idx = BM25Index.build(index_name, rows)
    return idx.save()
