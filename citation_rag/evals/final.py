"""Wave 8c: the one-time run on the sealed test set.

CLI:
    uv run python -m citation_rag.evals.final --unseal

Runs, once, on `evals/golden/test.jsonl`: the final RAG (`final/rag.py`), the
LangGraph build (`langgraph_build/`), and run A (Qwen, no documents) as the
baseline. For each system it measures retrieval (the wave 4 to 7 harness,
`citation_rag.evals.runner.run_eval`), answer quality (the calibrated judge,
`citation_rag.evals.judge`), latency, and cost (local models: no API dollars,
so token counts and wall time). Outputs go to `evals/results/final/`.

The test set is used one time. `--unseal` is required, and the run refuses
when `evals/results/FINAL_DONE` exists: it prints why, changes nothing, and
returns 1. The marker is written before the first question runs, so a crash
does not reopen the seal. To repeat a crashed run, delete the marker by hand
and accept that the test set is no longer sealed.

Each system is a `System`: an optional retriever (for retrieval metrics) and
an `answer_fn(question, companies) -> {"answer", "citations", "answerable",
"blocks"}`. Tests pass fakes through `run_final(systems=...)`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from citation_rag.evals import judge as judge_mod
from citation_rag.evals.golden import GoldenCase, load_golden
from citation_rag.evals.runner import Result, run_eval

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
MARKER_NAME = "FINAL_DONE"
TEST_GOLDEN = PROJECT_ROOT / "evals" / "golden" / "test.jsonl"
FINAL_SYSTEM, LANGGRAPH_SYSTEM, BASELINE_SYSTEM = "final_rag", "langgraph", "run_A"

AnswerFn = Callable[[str, "list[str] | str"], dict]


@dataclass
class System:
    name: str
    answer_fn: AnswerFn
    retriever: Any = None  # (question, companies) -> list[Result]; None for run A
    config: dict = field(default_factory=dict)
    tokens_fn: "Callable[[], dict] | None" = None  # {"input_tokens", "output_tokens"} since the last call


class SealedError(RuntimeError):
    """The test set was already used (marker present), or --unseal is missing."""


def marker_path(results_dir: "str | Path | None" = None) -> Path:
    return Path(results_dir or RESULTS_DIR) / MARKER_NAME


def check_seal(results_dir: "str | Path | None" = None) -> Path:
    """Path of the marker if the run may go ahead. Raises `SealedError` if it exists."""
    marker = marker_path(results_dir)
    if marker.exists():
        raise SealedError(
            f"{marker} exists: the test set was already used. Nothing was run. "
            "The sealed test set runs one time."
        )
    return marker


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_blocks(raw: Any) -> list[dict]:
    """Normalize context blocks (dicts, dataclasses, or Results) to {ref, text}."""
    out = []
    for i, b in enumerate(raw or [], start=1):
        if isinstance(b, dict):
            out.append({"ref": b.get("ref", i), "text": b.get("text", "")})
        else:
            out.append({"ref": getattr(b, "ref", i), "text": getattr(b, "text", "")})
    return out


def answer_all(system: System, cases: "list[GoldenCase]", out_path: Path) -> dict:
    """Answer every case with one system; write judge-ready rows (the run_all
    row shape) to `out_path`. Returns latency and token totals."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    latencies: list[float] = []
    tokens = {"input_tokens": 0, "output_tokens": 0}
    have_tokens = False
    with out_path.open("w", encoding="utf-8") as f:
        for case in cases:
            t0 = time.perf_counter()
            res = system.answer_fn(case.question, case.companies)
            latencies.append((time.perf_counter() - t0) * 1000)
            if system.tokens_fn is not None:
                t = system.tokens_fn() or {}
                for key in tokens:
                    if t.get(key) is not None:
                        tokens[key] += t[key]
                        have_tokens = True
            f.write(
                json.dumps(
                    {
                        "id": case.id,
                        "question": case.question,
                        "type": case.type,
                        "reference": case.answer,
                        "answer": {
                            "answer": res.get("answer", ""),
                            "citations": res.get("citations", []),
                            "answerable": res.get("answerable", True),
                        },
                        "blocks": _as_blocks(res.get("blocks") or res.get("context")),
                        "citation_check": res.get("citation_check"),
                        "latency_ms": latencies[-1],
                    }
                )
                + "\n"
            )
    ordered = sorted(latencies)
    return {
        "n_answered": len(latencies),
        "latency_ms_p50": ordered[len(ordered) // 2] if ordered else None,
        "latency_ms_total": sum(latencies),
        "cost": {
            "api_usd": 0.0,
            "wall_s": sum(latencies) / 1000.0,
            "input_tokens": tokens["input_tokens"] if have_tokens else None,
            "output_tokens": tokens["output_tokens"] if have_tokens else None,
        },
    }


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def summary_markdown(summary: dict) -> str:
    lines = [
        "| System | Recall@8 | MRR | Correct share | Faithfulness | Latency p50 ms | API cost USD |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, row in summary["systems"].items():
        if row.get("error"):
            lines.append(f"| {name} | error: {row['error']} | | | | | |")
            continue
        metrics = (row.get("retrieval") or {}).get("metrics", {})
        recall = next((v["value"] for k, v in metrics.items() if k.startswith("recall@") and k.count("_") == 0 and "tok" not in k), None)
        mrr = metrics.get("mrr", {}).get("value")
        judged = row.get("judged", {})
        lines.append(
            f"| {name} | {_pct(recall)} | {_pct(mrr)} | {_pct(judged.get('correct_share'))} | "
            f"{_pct(judged.get('faithfulness_mean'))} | {_pct(row['answers']['latency_ms_p50'])} | "
            f"{row['answers']['cost']['api_usd']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def run_final(
    *,
    unseal: bool,
    systems: "list[System]",
    judge_client: Any,
    golden_path: "str | Path | None" = None,
    results_dir: "str | Path | None" = None,
) -> dict:
    """The one-time run. Raises `SealedError` if `unseal` is false or the
    marker exists (before any question runs)."""
    if not unseal:
        raise SealedError("the test set is sealed: pass --unseal to run it (one time only)")
    results_dir = Path(results_dir or RESULTS_DIR)
    marker = check_seal(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"status": "started", "started": _now()}), encoding="utf-8")

    golden_path = Path(golden_path or TEST_GOLDEN)
    cases = load_golden(golden_path)
    out_dir = results_dir / "final"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {"started": _now(), "n_questions": len(cases), "systems": {}}
    for system in systems:
        row: dict[str, Any] = {"config": system.config}
        try:
            if system.retriever is not None:
                record = run_eval(
                    {**system.config, "system": system.name},
                    "test",
                    system.retriever,
                    golden_path=golden_path,
                    unseal=True,
                    results_dir=out_dir,
                )
                row["retrieval"] = {"run_id": record.run_id, "metrics": record.metrics}
            answers_path = out_dir / f"{system.name}.answers.jsonl"
            row["answers"] = answer_all(system, cases, answers_path)
            row["judged"] = judge_mod.score_answers(
                answers_path,
                judge_client,
                out_path=out_dir / f"{system.name}.judged.jsonl",
                golden_path=golden_path,
            )
        except Exception as exc:  # one broken system must not lose the others' results
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[final] {system.name}: failed, {row['error']}", file=sys.stderr, flush=True)
        summary["systems"][system.name] = row
        print(f"[final] {system.name}: done", file=sys.stderr, flush=True)

    summary["finished"] = _now()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_markdown(summary), encoding="utf-8")
    marker.write_text(
        json.dumps({"status": "done", "started": summary["started"], "finished": summary["finished"]}),
        encoding="utf-8",
    )
    return summary


# -- the real systems (GPU class; never built in unit tests) -----------------


def _load_final_rag():
    path = PROJECT_ROOT / "final" / "rag.py"
    spec = importlib.util.spec_from_file_location("final_rag", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ciks(companies: "list[str] | str | None") -> "list[str] | None":
    return None if not companies or companies == "general" else list(companies)


def build_final_rag_system() -> System:
    rag = _load_final_rag()
    conn = rag._get_conn()
    table = f"chunks_{rag.CONFIG['index']}"
    bm25 = rag._load_bm25(rag.CONFIG["index"]) if rag.CONFIG["search"] in ("bm25", "hybrid") else None

    def retriever(question: str, companies: "list[str] | str") -> "list[Result]":
        rows = rag.retrieve(conn, table, bm25, question, _ciks(companies))
        return [Result(*(r[f] for f in Result._fields)) for r in rows]

    def answer_fn(question: str, companies: "list[str] | str") -> dict:
        return rag.answer_question(question, _ciks(companies))

    return System(FINAL_SYSTEM, answer_fn, retriever, config={k: rag.CONFIG[k] for k in ("index", "search", "reranker", "thinking")} | {"k": 50, "top": rag.TOP})


def build_langgraph_system() -> System:
    from langchain_postgres import PGVector

    from citation_rag.settings import Settings
    from langgraph_build.adapters import make_answer_fn, make_retriever
    from langgraph_build.ingest import DEFAULT_COLLECTION_NAME, _psycopg_connection_string, load_embeddings
    from langgraph_build.pipeline import DEFAULT_K, build_graph, load_chat_model

    vectorstore = PGVector(
        embeddings=load_embeddings(),
        collection_name=DEFAULT_COLLECTION_NAME,
        connection=_psycopg_connection_string(Settings().database_url),
        use_jsonb=True,
    )
    graph = build_graph(vectorstore, load_chat_model(), k=DEFAULT_K)
    fn = make_answer_fn(graph)

    def answer_fn(question: str, companies: "list[str] | str") -> dict:
        return fn(question, companies)

    return System(LANGGRAPH_SYSTEM, answer_fn, make_retriever(vectorstore, k=DEFAULT_K), config={"k": DEFAULT_K, "top": DEFAULT_K})


def build_baseline_system(thinking: bool) -> System:
    """Run A: Qwen with no documents (the wave 7 `no_docs` mode, no run_log rows)."""
    from citation_rag.answer.llm import OllamaChat
    from citation_rag.answer.pipeline import answer

    llm = OllamaChat(thinking=thinking)

    def answer_fn(question: str, companies: "list[str] | str") -> dict:
        rec = answer(question, {"mode": "no_docs", "thinking": thinking}, companies=companies, llm_client=llm, log=False)
        return {"answer": rec.answer, "citations": rec.citations, "answerable": rec.answerable, "blocks": []}

    def tokens() -> dict:
        return {"input_tokens": llm.last_input_tokens, "output_tokens": llm.last_output_tokens}

    return System(BASELINE_SYSTEM, answer_fn, None, config={"mode": "no_docs", "thinking": thinking}, tokens_fn=tokens)


def build_default_systems() -> "list[System]":
    rag = _load_final_rag()
    systems = [build_final_rag_system(), build_langgraph_system(), build_baseline_system(bool(rag.CONFIG["thinking"]))]
    return systems


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.evals.final")
    parser.add_argument("--unseal", action="store_true", help="required: run the sealed test set, one time")
    parser.add_argument("--judge-model", default="gpt-oss:20b")
    args = parser.parse_args(argv)

    if not args.unseal:
        print("refused: the test set is sealed. Pass --unseal to run it (one time only).", file=sys.stderr)
        return 2
    try:
        check_seal()
    except SealedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    summary = run_final(
        unseal=True,
        systems=build_default_systems(),
        judge_client=judge_mod.OllamaJudge(model=args.judge_model),
    )
    print(summary_markdown(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
