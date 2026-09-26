"""`answer(question, config) -> AnswerRecord` (wave 7a contract, point 5).

Runs: router, retriever (wave 5a), optional reranker (wave 6), context
(this wave's `context.py`), prompt, LLM, citation check. Writes one row to
`run_log` (schema in `citation_rag.index.schema`) with every id list, the
context token count, the answer, the citations, model, thinking flag, and
per-stage latency.

`config["mode"]` is `"rag"` (run B: our RAG system) or `"no_docs"` (run A:
no retrieval, no citations required -- plan section 11). `config["router"]`
is `"oracle"` (use a given golden case's own `companies`) or `"llm"` (route
with an LLM client); passing `companies=` directly skips routing.

This module owns no collaborators of its own: the retriever, reranker, and
LLM client are all passed in by the caller (tests use fixtures/fakes; the
wave-6 orchestrator's `run_all.py` wires in the real ones after GO).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from citation_rag.answer.base import NoRerank, Reranker
from citation_rag.answer.cite import CitationCheck, check_citations
from citation_rag.answer.context import ContextBlock, _qualify, build_context
from citation_rag.answer.prompt import AnswerParseError, build_prompt, parse_response
from citation_rag.search import vector as _vector
from citation_rag.search.router import LLMRouter, OracleRouter, Router


@dataclass
class AnswerRecord:
    run_log_id: "int | None"
    question: str
    router: dict
    retrieved_ids: list
    reranked_ids: list
    section_ids: list
    context_tokens: int
    blocks: "list[ContextBlock]"
    answer: str
    citations: list
    answerable: bool
    citation_check: CitationCheck
    model: str
    thinking: bool
    latency_ms: dict = field(default_factory=dict)


def _make_router(config: dict, case: Any, router_client: Any, company_directory: Any) -> Router:
    mode = config.get("router", "oracle")
    if mode == "oracle":
        if case is None:
            raise ValueError("router mode 'oracle' requires `case` (a golden case with `.companies`)")
        return OracleRouter(case)
    if mode == "llm":
        if router_client is None:
            raise ValueError("router mode 'llm' requires `router_client`")
        return LLMRouter(router_client, company_directory or [])
    raise ValueError(f"unknown router mode: {mode!r}")


def _parse_answer(raw: str, *, require_citations: bool) -> dict:
    """Parse the model's JSON reply, degrading rather than raising on bad
    output (an unparsable answer should fail the citation check, not crash
    the pipeline -- same judgment call as `search.router.LLMRouter`)."""
    try:
        return parse_response(raw, require_citations=require_citations)
    except AnswerParseError:
        return {"answer": raw, "citations": [], "answerable": False}


def _write_run_log(record: AnswerRecord, config: dict, pool: Any, schema: "str | None") -> int:
    pool = pool or _vector.get_pool()
    table = _qualify("run_log", schema)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table} (
                    question, config, router, retrieved_ids, reranked_ids,
                    section_ids, context_tokens, answer, citations, model,
                    thinking, latency_ms
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    record.question,
                    json.dumps(config),
                    json.dumps(record.router),
                    record.retrieved_ids or None,
                    record.reranked_ids or None,
                    record.section_ids or None,
                    record.context_tokens,
                    record.answer,
                    json.dumps(record.citations),
                    record.model,
                    record.thinking,
                    json.dumps(record.latency_ms),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def answer(
    question: str,
    config: dict,
    *,
    case: Any = None,
    companies: "list[str] | str | None" = None,
    retriever: Any = None,
    reranker: "Reranker | None" = None,
    llm_client: Any = None,
    router_client: Any = None,
    company_directory: Any = None,
    pool: Any = None,
    schema: "str | None" = None,
    log: bool = True,
) -> AnswerRecord:
    if llm_client is None:
        raise ValueError("llm_client is required")

    mode = config.get("mode", "rag")
    if mode not in ("rag", "no_docs"):
        raise ValueError(f"config['mode'] must be 'rag' or 'no_docs', got {mode!r}")

    latency_ms: dict[str, float] = {}
    t0 = time.perf_counter()

    if companies is not None:
        routed = {"companies": companies, "unresolved": []}
    else:
        router = _make_router(config, case, router_client, company_directory)
        t = time.perf_counter()
        routed = router.route(question)
        latency_ms["router_ms"] = (time.perf_counter() - t) * 1000

    retrieved_ids: list = []
    reranked_ids: list = []
    section_ids: list = []
    context_tokens = 0
    blocks: list[ContextBlock] = []

    if mode == "rag":
        if retriever is None:
            raise ValueError("config['mode'] == 'rag' requires a retriever")

        t = time.perf_counter()
        results = list(retriever(question, routed["companies"]))
        latency_ms["retrieve_ms"] = (time.perf_counter() - t) * 1000
        retrieved_ids = [r.chunk_id for r in results]

        top = config.get("top", 8)
        active_reranker = reranker or NoRerank()
        t = time.perf_counter()
        results = active_reranker.rerank(question, results, top)
        latency_ms["rerank_ms"] = (time.perf_counter() - t) * 1000
        reranked_ids = [r.chunk_id for r in results]

        t = time.perf_counter()
        blocks = build_context(
            results,
            budget_tokens=config.get("context_budget_tokens", 20_000),
            section_window_tokens=config.get("section_window_tokens", 4_000),
            pool=pool,
            schema=schema,
        )
        latency_ms["context_ms"] = (time.perf_counter() - t) * 1000
        section_ids = [b.section_id for b in blocks]
        context_tokens = sum(_block_tokens(b) for b in blocks)

    prompt = build_prompt(question, blocks, mode=mode)

    t = time.perf_counter()
    raw = llm_client.complete(prompt)
    latency_ms["llm_ms"] = (time.perf_counter() - t) * 1000
    for attr, key in (
        ("last_wall_time_s", "llm_wall_time_s"),
        ("last_input_tokens", "llm_input_tokens"),
        ("last_output_tokens", "llm_output_tokens"),
    ):
        val = getattr(llm_client, attr, None)
        if val is not None:
            latency_ms[key] = val

    parsed = _parse_answer(raw, require_citations=(mode == "rag"))
    answer_text = parsed["answer"]
    citations = parsed.get("citations", [])
    answerable = bool(parsed.get("answerable", True))

    if mode == "rag":
        citation_check = check_citations(answer_text, citations, answerable, blocks)
    else:
        citation_check = CitationCheck(
            valid=True,
            invalid_refs=[],
            unquoted_markers=[],
            quotes_not_found=[],
            refused_with_citations=False,
            citation_map={},
        )

    latency_ms["total_ms"] = (time.perf_counter() - t0) * 1000

    record = AnswerRecord(
        run_log_id=None,
        question=question,
        router=routed,
        retrieved_ids=retrieved_ids,
        reranked_ids=reranked_ids,
        section_ids=section_ids,
        context_tokens=context_tokens,
        blocks=blocks,
        answer=answer_text,
        citations=citations,
        answerable=answerable,
        citation_check=citation_check,
        model=config.get("model") or getattr(llm_client, "model", "unknown"),
        thinking=bool(config.get("thinking", False)),
        latency_ms=latency_ms,
    )

    if log:
        record.run_log_id = _write_run_log(record, config, pool, schema)

    return record


def _block_tokens(block: ContextBlock) -> int:
    from citation_rag.chunk.tokens import count_tokens

    return count_tokens(block.text)
