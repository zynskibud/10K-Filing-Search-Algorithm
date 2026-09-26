"""Final optimized RAG (wave 8a): standalone, no framework, <=300 lines.

Pipeline: BM25/vector search on one chunk table -> RRF fusion (hybrid) ->
cross-encoder rerank -> small-to-big context (20k-token budget) -> Qwen3
prompt with citations -> citation check. CONFIG holds the winning choices
from waves 5-7 (defaults: hybrid, bge-small s3, cross-encoder, thinking
off); `runs/wave-{5,6,7}/winners.json` overrides them when present. Company
routing is out of scope here: pass --company CIKs to filter, or none for
every filing. Imports only psycopg, httpx, sentence_transformers (its
tokenizer also covers "the tokenizer"), and citation_rag.settings.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import re
import sys
from pathlib import Path

import httpx
import psycopg
from citation_rag.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    "index": "bge_small__s3",       # chunk table is chunks_{index}
    "search": "hybrid",             # "bm25" | "vector" | "hybrid"
    "reranker": "cross_encoder",    # "cross_encoder" | "none"
    "thinking": False,
    "table_option": "labels_only",  # informational: baked in at index time
}
for _wave in ("wave-5", "wave-6", "wave-7"):
    _p = PROJECT_ROOT / "runs" / _wave / "winners.json"
    if _p.exists():
        CONFIG.update(json.loads(_p.read_text(encoding="utf-8")))
K, TOP, RRF_K = 50, 8, 60
CONTEXT_BUDGET = 20_000
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

ANSWER_PROMPT = """You are answering a question about SEC 10-K filings, using only the numbered blocks of filing text below.

Rules:
- Answer only from the blocks. Do not use outside knowledge.
- Cite every claim with the block number in brackets, for example [1].
- For each citation, give the exact quote from the block that supports it.
- If the blocks do not contain the answer, say exactly: "The filings do not contain this information." Give no citations in that case.
- Keep the answer short.

Reply with JSON only, in this exact shape:
{{"answer": "<prose with [n] markers>", "citations": [{{"ref": <int>, "quote": "<exact quote from the block>"}}], "answerable": <true or false>}}

{blocks}

Question: {question}
"""

SETTINGS = Settings()
os.environ.setdefault("HF_HOME", SETTINGS.hf_home)
os.environ.setdefault("HF_HUB_CACHE", SETTINGS.hf_home)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from sentence_transformers import CrossEncoder, SentenceTransformer  # noqa: E402

# -- lazy singletons: one Postgres connection, one embedder, one reranker --
_conn = _embedder = _reranker = None
def _get_conn(schema: str | None = None) -> psycopg.Connection:
    global _conn
    if _conn is None:
        _conn = psycopg.connect(SETTINGS.database_url, autocommit=True)
        _conn.execute("SET hnsw.ef_search = 100")
    if schema:
        _conn.execute(f"SET search_path TO {schema}, public")
    return _conn
def _get_embedder() -> SentenceTransformer:
    global _embedder
    _embedder = _embedder or SentenceTransformer(EMBED_MODEL, device="cpu", cache_folder=SETTINGS.hf_home)
    return _embedder
def _get_reranker() -> CrossEncoder:
    global _reranker
    _reranker = _reranker or CrossEncoder(RERANK_MODEL, device="cpu", max_length=1024, cache_folder=SETTINGS.hf_home)
    return _reranker
def count_tokens(text: str) -> int:
    """Token count under the bge-small tokenizer (the project's token ruler)."""
    return len(_get_embedder().tokenizer(text, add_special_tokens=False)["input_ids"]) if text else 0

# -- BM25: data/bm25/{index}.pkl holds a citation_rag.search.bm25.BM25Index
# instance; find_class redirects that class to a bare placeholder so loading
# it never imports citation_rag. Scoring is reimplemented below (k1=1.5,
# b=0.75 Okapi BM25, over the loaded pickle's plain postings/doc_freq/etc.)
class _PlainBM25:
    pass
class _BM25Unpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        is_bm25 = module.startswith("citation_rag") and name == "BM25Index"
        return _PlainBM25 if is_bm25 else super().find_class(module, name)
def _load_bm25(index_name: str) -> _PlainBM25:
    path = PROJECT_ROOT / "data" / "bm25" / f"{index_name}.pkl"
    with path.open("rb") as f:
        return _BM25Unpickler(f).load()
STOPWORDS = frozenset(
    """a an the and or but if then so because as until while of at by for
    with about against between into through during before after above
    below to from up down in out on off over under again further once
    here there all any both each few more most""".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and (t.isdigit() or len(t) >= 2)]
def _bm25_search(idx: _PlainBM25, query: str, k: int, ciks: list[str] | None) -> list[tuple]:
    terms = list(dict.fromkeys(_tokenize(query)))
    if not terms:
        return []
    candidates = set()
    for t in terms:
        candidates.update(idx.postings.get(t, {}))
    if ciks:
        candidates &= {i for i, c in idx.doc_cik.items() if c in set(ciks)}
    avgdl, k1, b = (idx.avgdl or 1.0), 1.5, 0.75
    scored = []
    for doc_id in candidates:
        dl = idx.doc_len.get(doc_id, 0)
        total = 0.0
        for t in terms:
            tf = idx.postings.get(t, {}).get(doc_id)
            if not tf:
                continue
            df = idx.doc_freq.get(t, 0)
            idf = math.log(1.0 + (idx.n_docs - df + 0.5) / (df + 0.5))
            total += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
        if total > 0:
            scored.append((doc_id, total))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored[:k]

def _vector_search(conn, table: str, qvec, k: int, ciks: list[str] | None) -> list[tuple]:
    vec_lit = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
    where, params = ("WHERE cik = ANY(%s) ", (vec_lit, list(ciks), k)) if ciks else ("", (vec_lit, k))
    rows = conn.execute(f"SELECT id, embedding <=> %s::vector FROM {table} {where}ORDER BY 2 LIMIT %s", params)
    return [(r[0], float(r[1])) for r in rows.fetchall()]
def rrf(lists: list[list[tuple]], k: int = RRF_K) -> list[tuple]:
    """score(id) = sum of 1/(k + rank in each list), best first."""
    totals: dict = {}
    for lst in lists:
        for rank, (doc_id, _) in enumerate(lst, start=1):
            totals[doc_id] = totals.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

def retrieve(conn, table: str, bm25_idx: "_PlainBM25 | None", question: str, ciks: list[str] | None) -> list[dict]:
    bm25_hits = _bm25_search(bm25_idx, question, K, ciks) if CONFIG["search"] in ("bm25", "hybrid") else []
    vec_hits: list[tuple] = []
    if CONFIG["search"] in ("vector", "hybrid"):
        qvec = _get_embedder().encode(QUERY_PREFIX + question, normalize_embeddings=True)
        vec_hits = _vector_search(conn, table, qvec, K, ciks)
    if CONFIG["search"] == "bm25":
        ranked = [d for d, _ in bm25_hits]
    elif CONFIG["search"] == "vector":
        ranked = [d for d, _ in vec_hits]
    else:
        ranked = [d for d, _ in rrf([bm25_hits, vec_hits])]
    cols = ["id", "text", "token_count", "section_id", "table_id", "page_start", "page_end", "accession_no", "cik"]
    q = f"SELECT {', '.join(cols)} FROM {table} WHERE id = ANY(%s)"
    rows = {r[0]: dict(zip(cols, r)) for r in conn.execute(q, (ranked,)).fetchall()} if ranked else {}
    ranked = [d for d in ranked if d in rows]
    if CONFIG["reranker"] == "cross_encoder" and ranked:
        pairs = [[question, rows[d]["text"]] for d in ranked]
        scores = _get_reranker().predict(pairs, batch_size=16, show_progress_bar=False)
        ranked = [d for _, d in sorted(zip(scores, ranked), key=lambda p: p[0], reverse=True)]
    return [rows[d] for d in ranked[:TOP]]

# -- small-to-big context: parent sections, tables filled in, 20k budget --
_TABLE_RE = re.compile(r"^\[Table: (?P<id>.+?)\]$", re.MULTILINE)
def _fill_tables(conn, text: str, table_ids: list[str], accession_no: str) -> str:
    candidates = set(table_ids) | {f"{accession_no}:{t}" for t in table_ids if ":" not in t}
    rows = conn.execute("SELECT id, text FROM tables_parsed WHERE id = ANY(%s)", (list(candidates),)).fetchall()
    by_bare = {rid.split(":", 1)[-1]: rtext for rid, rtext in rows}
    if not by_bare:
        return text
    return _TABLE_RE.sub(lambda m: m.group(0) + "\n" + by_bare.get(m.group("id"), ""), text)
_SEC_COLS = ["id", "accession_no", "item", "title", "page_start", "page_end", "text", "company", "fiscal_year"]
def build_context(conn, results: list[dict]) -> list[dict]:
    """A section that alone exceeds the whole budget is cut to what remains,
    by characters (not windowed around the matched chunks -- see README)."""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for r in results:
        sid = r.get("section_id")
        if sid:
            groups.setdefault(sid, []).append(r)
            if sid not in order:
                order.append(sid)
    if not order:
        return []
    sql = (f"SELECT {', '.join('s.' + c for c in _SEC_COLS[:-2])}, f.company, f.fiscal_year "
           "FROM sections s JOIN filings f ON f.accession_no = s.accession_no WHERE s.id = ANY(%s)")
    sections = {r[0]: dict(zip(_SEC_COLS, r)) for r in conn.execute(sql, (order,)).fetchall()}
    blocks, total, ref = [], 0, 1
    for sid in order:
        sec = sections.get(sid)
        if sec is None:
            continue
        text = sec["text"]
        table_ids = [r["table_id"] for r in groups[sid] if r.get("table_id")]
        if table_ids:
            text = _fill_tables(conn, text, table_ids, sec["accession_no"])
        tok = count_tokens(text)
        remaining = CONTEXT_BUDGET - total
        if tok > remaining:
            if blocks:
                break
            text = text[: max(remaining, 200) * 4] + "\n[...]"
            tok = count_tokens(text)
        blocks.append({
            "ref": ref, "text": text, "accession_no": sec["accession_no"],
            "company": sec["company"], "fiscal_year": sec["fiscal_year"],
            "item": sec["item"], "section_title": sec["title"],
            "page_start": sec["page_start"], "page_end": sec["page_end"],
        })
        total += tok
        ref += 1
    return blocks

# -- prompt, LLM call, parsing ---------------------------------------------
def format_block(b: dict) -> str:
    return (f"[{b['ref']}] {b['company']} | FY{b['fiscal_year']} | Item {b['item']} | "
            f"{b['section_title']} | pages {b['page_start']}-{b['page_end']}\n{b['text']}")
def build_prompt(question: str, blocks: list[dict]) -> str:
    return ANSWER_PROMPT.format(blocks="\n\n".join(format_block(b) for b in blocks), question=question)
def parse_response(raw: str) -> dict:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or "answer" not in data:
            raise ValueError("missing 'answer'")
    except (json.JSONDecodeError, ValueError):
        return {"answer": raw, "citations": [], "answerable": False}
    data.setdefault("answerable", True)
    if not isinstance(data.get("citations"), list):
        data["citations"] = []
    return data
def call_llm(prompt: str, base_url: str) -> str:
    payload = {
        "model": "qwen3:8b", "prompt": prompt, "stream": False, "format": "json",
        "options": {"temperature": 0.0, "num_ctx": 32768}, "think": CONFIG["thinking"],
    }
    resp = httpx.post(f"{base_url}/api/generate", json=payload, timeout=900.0)
    resp.raise_for_status()
    return resp.json()["response"]

_MARKER_RE = re.compile(r"\[(\d+)\]")
def _norm(s: str) -> str:
    return " ".join(s.split())
def check_citations(answer: str, citations: list[dict], answerable: bool, blocks: list[dict]) -> dict:
    by_ref = {b["ref"]: b for b in blocks}
    markers = {int(m) for m in _MARKER_RE.findall(answer)}
    invalid, bad_quote, cited = [], [], set()
    for c in citations:
        ref, quote = c.get("ref"), _norm(c.get("quote") or "")
        cited.add(ref)
        block = by_ref.get(ref)
        if block is None:
            invalid.append(ref)
        elif not quote or quote not in _norm(block["text"]):
            bad_quote.append(ref)
    unquoted = sorted(m for m in markers if m not in cited)
    refused_with_citations = (not answerable) and bool(citations)
    valid = not (invalid or bad_quote or unquoted or refused_with_citations)
    return {
        "valid": valid, "invalid_refs": invalid, "quotes_not_found": bad_quote,
        "unquoted_markers": unquoted, "refused_with_citations": refused_with_citations,
    }

def answer_question(question: str, companies: list[str] | None = None, *, schema: str | None = None, llm=None) -> dict:
    conn = _get_conn(schema)
    table = f"chunks_{CONFIG['index']}"
    bm25_idx = _load_bm25(CONFIG["index"]) if CONFIG["search"] in ("bm25", "hybrid") else None
    results = retrieve(conn, table, bm25_idx, question, companies)
    blocks = build_context(conn, results)
    prompt = build_prompt(question, blocks)
    raw = llm.complete(prompt) if llm is not None else call_llm(prompt, SETTINGS.ollama_url)
    parsed = parse_response(raw)
    check = check_citations(parsed["answer"], parsed["citations"], parsed["answerable"], blocks)
    return {"answer": parsed["answer"], "citations": parsed["citations"],
            "answerable": parsed["answerable"], "citation_check": check, "blocks": blocks}
def main() -> None:
    ap = argparse.ArgumentParser(description="Answer one question with the final RAG system.")
    ap.add_argument("question")
    ap.add_argument("--company", action="append", default=None, help="CIK to filter to (repeatable); omit for every filing")
    args = ap.parse_args()
    result = answer_question(args.question, args.company)
    print(result["answer"])
    for c in result["citations"]:
        print(f"  [{c.get('ref')}] {c.get('quote')}")
    if not result["citation_check"]["valid"]:
        print("WARNING: citation check failed:", result["citation_check"], file=sys.stderr)
if __name__ == "__main__":
    main()
