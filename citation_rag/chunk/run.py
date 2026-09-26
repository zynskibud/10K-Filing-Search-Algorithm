"""CLI: chunk parsed filings into one JSONL of chunk records (wave 4b,
contract point 5).

    uv run python -m citation_rag.chunk.run \\
        --strategy s3 --table-option 2 --out data/chunks/bge_small__s3.jsonl

Reads every `*.json` under `--parsed-dir` (default `data/parsed`, the wave
2c output) whose `checks.passed` is true, in sorted filename order, and
writes one JSONL row per chunk with every schema section 4 column except
`embedding`, plus `strategy` and `table_option`. Deterministic: the same
inputs and flags always produce byte-identical output.

`--filings` overrides `--parsed-dir` with an explicit, ordered list of
paths -- used for development on `tests/fixtures/parsed/` and
`tests/fixtures/parsed_samples/` before wave 2c's full corpus exists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from . import prose, tables

SCHEMA_ROW_ORDER = (
    "id",
    "accession_no",
    "cik",
    "item",
    "section_id",
    "table_id",
    "seq",
    "page_start",
    "page_end",
    "page_label",
    "is_table",
    "text",
    "embed_text",
    "token_count",
    "strategy",
    "table_option",
)


def chunk_one_filing(filing: dict, strategy: str, table_option: int) -> list[dict]:
    """Every chunk row for one filing, in document order: prose chunks
    first, then table chunks (table_option 2 or 3 only)."""
    prose_chunks = prose.chunk_filing(filing, strategy, table_option)

    table_chunks: list[dict] = []
    if table_option in (2, 3):
        sections_by_id = {
            section["id"]: section
            for item in filing.get("items", [])
            for section in item.get("sections", [])
        }
        for table in filing.get("tables", []):
            section = sections_by_id.get(table.get("section_id"), {})
            section_title = section.get("title") or table.get("title") or ""
            table_chunks.extend(tables.make_table_chunks(filing, table, table_option, section_title))

    accession_no = filing["accession_no"]
    rows = []
    for seq, chunk in enumerate(prose_chunks + table_chunks, start=1):
        row = {
            "id": f"{accession_no}:{seq:06d}",
            "accession_no": accession_no,
            "cik": chunk.get("cik"),
            "item": chunk.get("item"),
            "section_id": chunk.get("section_id"),
            "table_id": chunk.get("table_id"),
            "seq": seq,
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "page_label": chunk.get("page_label"),
            "is_table": bool(chunk.get("is_table", False)),
            "text": chunk.get("text"),
            "embed_text": chunk.get("embed_text"),
            "token_count": chunk.get("token_count"),
            "strategy": strategy,
            "table_option": table_option,
        }
        rows.append(row)
    return rows


def _load_filings(parsed_dir: str | None, explicit: list[str] | None) -> list[str]:
    if explicit:
        return sorted(explicit)
    return sorted(str(p) for p in Path(parsed_dir).glob("*.json"))


def run(strategy: str, table_option: int, out: str, parsed_dir: str, filings: list[str] | None) -> dict:
    """Chunk every passing filing, write the JSONL, and return the stats
    dict that `_print_stats` also prints (so tests and callers can check
    the numbers without re-parsing stdout)."""
    paths = _load_filings(parsed_dir, filings)
    all_rows: list[dict] = []
    n_skipped = 0
    for path in paths:
        filing = json.loads(Path(path).read_text(encoding="utf-8"))
        if not filing.get("checks", {}).get("passed", False):
            n_skipped += 1
            continue
        all_rows.extend(chunk_one_filing(filing, strategy, table_option))

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            ordered = {key: row[key] for key in SCHEMA_ROW_ORDER}
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")

    stats = _compute_stats(all_rows)
    stats["n_filings"] = len(paths)
    stats["n_skipped_checks"] = n_skipped
    _print_stats(strategy, table_option, stats)
    return stats


def _token_stats(token_counts: list[int]) -> dict:
    if not token_counts:
        return {"n": 0, "p50": None, "p90": None, "max": None, "share_over_400": None, "share_over_512": None}
    arr = np.array(token_counts, dtype=float)
    return {
        "n": len(arr),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "max": int(arr.max()),
        "share_over_400": float((arr > 400).mean()),
        "share_over_512": float((arr > 512).mean()),
    }


def _compute_stats(rows: list[dict]) -> dict:
    all_tc = [r["token_count"] for r in rows]
    prose_tc = [r["token_count"] for r in rows if not r["is_table"]]
    table_tc = [r["token_count"] for r in rows if r["is_table"]]
    return {
        "n_chunks": len(rows),
        "all": _token_stats(all_tc),
        "prose": _token_stats(prose_tc),
        "table": _token_stats(table_tc),
    }


def _fmt(s: dict) -> str:
    if not s["n"]:
        return "n=0"
    return (
        f"n={s['n']} p50={s['p50']:.0f} p90={s['p90']:.0f} max={s['max']} "
        f"share>400={s['share_over_400']:.1%} share>512={s['share_over_512']:.1%}"
    )


def _print_stats(strategy: str, table_option: int, stats: dict) -> None:
    print(
        f"strategy={strategy} table_option={table_option} "
        f"filings={stats['n_filings']} skipped_failed_checks={stats['n_skipped_checks']} "
        f"chunks={stats['n_chunks']}"
    )
    print(f"  all:   {_fmt(stats['all'])}")
    print(f"  prose: {_fmt(stats['prose'])}")
    print(f"  table: {_fmt(stats['table'])}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chunk parsed filings into one JSONL of chunk records.")
    parser.add_argument("--strategy", required=True, choices=list(prose.STRATEGIES))
    parser.add_argument("--table-option", type=int, default=2, choices=[0, 1, 2, 3])
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--parsed-dir",
        default="data/parsed",
        help="Directory of parsed filing JSON (schema section 1); only checks.passed=true files are used.",
    )
    parser.add_argument(
        "--filings",
        nargs="*",
        default=None,
        help="Explicit parsed filing JSON paths, overriding --parsed-dir (development, before wave 2c's corpus exists).",
    )
    args = parser.parse_args(argv)
    run(args.strategy, args.table_option, args.out, args.parsed_dir, args.filings)


if __name__ == "__main__":
    main()
