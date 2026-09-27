"""Parse the full corpus into `data/parsed/{accession_no}.json` (wave 2c,
deliverable 3).

    uv run python -m citation_rag.parse.run \\
        --manifest data/raw/corpus.jsonl --out data/parsed --workers 4

Resumable: a filing whose output JSON already exists is skipped unless
`--force` is given. Every manifest row gets a JSON file, even one that
raises during parsing -- a minimal error document is written instead so
`data/parsed/` always has one file per manifest row, and the row is also
recorded in `data/parse_failures.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
from pathlib import Path

from .filing import parse_filing
from .items import ITEM_PARTS, ITEM_TITLES, STANDARD_ITEMS

BATCH_SIZE = 50
MAX_WORKERS = 4


def _build_meta(row: dict) -> dict:
    return {
        "accession_no": row["accession_no"],
        "cik": row.get("cik"),
        "company": row.get("company"),
        "ticker": row.get("ticker"),
        "fiscal_year_end": row.get("report_date"),
        "fiscal_year": row.get("fiscal_year"),
        "filed_date": row.get("filed_date"),
        "filer_category": row.get("filer_category"),
        "sic": row.get("sic"),
        "source_url": row.get("source_url"),
    }


def _error_doc(meta: dict, error: str) -> dict:
    """A minimal, schema-shaped placeholder for a filing that raised during
    parsing, so `data/parsed/` still has one JSON per manifest row and the
    error is visible in the same `checks.failures` field a normal failure
    uses, rather than being a bare missing file."""
    items = [
        {
            "item": item_num,
            "part": ITEM_PARTS[item_num],
            "title": ITEM_TITLES.get(item_num, ""),
            "status": "absent",
            "page_start": None,
            "page_end": None,
            "sections": [],
        }
        for item_num in STANDARD_ITEMS
    ]
    return {
        "accession_no": meta.get("accession_no"),
        "cik": meta.get("cik"),
        "company": meta.get("company"),
        "ticker": meta.get("ticker"),
        "fiscal_year_end": meta.get("fiscal_year_end"),
        "fiscal_year": meta.get("fiscal_year"),
        "filed_date": meta.get("filed_date"),
        "filer_category": meta.get("filer_category"),
        "sic": meta.get("sic"),
        "source_url": meta.get("source_url"),
        "pages": [],
        "items": items,
        "tables": [],
        "checks": {"passed": False, "failures": [f"parse_exception: {error}"], "stats": {}},
    }


def _process_row(args) -> dict:
    """Runs in a worker process: parse one filing and write its JSON.

    Returns a small summary dict (no lxml elements, safe to pickle back to
    the main process) for progress reporting and the failures log.
    """
    row, out_dir, force = args
    accession_no = row["accession_no"]
    out_path = Path(out_dir) / f"{accession_no}.json"
    company = row.get("company")
    filer_category = row.get("filer_category")

    if out_path.exists() and not force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            passed = bool(existing.get("checks", {}).get("passed"))
            failures = existing.get("checks", {}).get("failures", [])
        except Exception:
            passed, failures = False, ["unreadable existing output"]
        return {
            "accession_no": accession_no,
            "company": company,
            "filer_category": filer_category,
            "passed": passed,
            "failures": failures,
            "skipped": True,
        }

    meta = _build_meta(row)
    path = row["path"]
    try:
        doc = parse_filing(path, meta)
    except Exception as e:  # noqa: BLE001 - must not crash the worker
        detail = f"{type(e).__name__}: {e}"
        doc = _error_doc(meta, detail)
        out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "accession_no": accession_no,
            "company": company,
            "filer_category": filer_category,
            "passed": False,
            "failures": doc["checks"]["failures"],
            "skipped": False,
            "exception": traceback.format_exc(limit=3),
        }

    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    checks = doc["checks"]
    return {
        "accession_no": accession_no,
        "company": company,
        "filer_category": filer_category,
        "passed": bool(checks.get("passed")),
        "failures": checks.get("failures", []),
        "skipped": False,
    }


def run(manifest_path: str, out_dir: str, workers: int, force: bool, limit: int | None = None) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    with open(manifest_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if limit is not None:
        rows = rows[:limit]

    args = [(row, str(out), force) for row in rows]

    # The machine protocol: when a timing benchmark takes .coord/timing.lock,
    # CPU-BULK work stops cleanly. We check between batches and, on a stop,
    # write the remaining rows to data/parse_resume.jsonl so the run can
    # continue later with `scripts/parse.sh --force --manifest data/parse_resume.jsonl`.
    timing_lock = Path(__file__).resolve().parents[3] / ".coord" / "timing.lock"
    resume_path = out.parent / "parse_resume.jsonl"

    results = []
    start = time.time()
    n = len(args)
    workers = max(1, min(workers, MAX_WORKERS))
    ctx = mp.get_context("spawn")
    done = 0
    with ctx.Pool(processes=workers) as pool:
        for b in range(0, n, BATCH_SIZE):
            if timing_lock.exists():
                remaining = rows[b:]
                with resume_path.open("w", encoding="utf-8") as fh:
                    for row in remaining:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"STOP: timing.lock present after {done}/{n} rows; "
                    f"{len(remaining)} remaining rows written to {resume_path}",
                    file=sys.stderr,
                )
                return results, False
            batch = args[b:b + BATCH_SIZE]
            for res in pool.imap_unordered(_process_row, batch):
                results.append(res)
                done += 1
            elapsed = time.time() - start
            n_pass = sum(1 for r in results if r["passed"])
            print(f"[{done}/{n}] elapsed={elapsed:.0f}s pass={n_pass}/{done}", file=sys.stderr)
    if resume_path.exists():
        resume_path.unlink()
    return results, True


def rebuild_failures(out_dir: str, failures_path: str) -> tuple[int, int]:
    """Rebuild the failure list from every JSON in out_dir, so a run that
    stopped and resumed still yields one complete list. Returns (n_files, n_failed)."""
    out = Path(out_dir)
    n_files = n_failed = 0
    with open(failures_path, "w", encoding="utf-8") as fh:
        for path in sorted(out.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            n_files += 1
            checks = doc.get("checks", {})
            if checks.get("passed"):
                continue
            n_failed += 1
            fh.write(
                json.dumps(
                    {
                        "accession_no": doc.get("accession_no"),
                        "company": doc.get("company"),
                        "filer_category": doc.get("filer_category"),
                        "failures": checks.get("failures", []),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return n_files, n_failed


def write_failures(results: list[dict], failures_path: str) -> int:
    n_failed = 0
    with open(failures_path, "w", encoding="utf-8") as fh:
        for r in results:
            if r["passed"]:
                continue
            n_failed += 1
            fh.write(
                json.dumps(
                    {
                        "accession_no": r["accession_no"],
                        "company": r["company"],
                        "filer_category": r["filer_category"],
                        "failures": r["failures"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return n_failed


def main(argv=None):
    os.nice(10)  # protocol: host parse workers run at low priority
    parser = argparse.ArgumentParser(description="Parse the full 10-K corpus into data/parsed/.")
    parser.add_argument("--manifest", required=True, help="Path to the manifest JSONL (data/raw/corpus.jsonl)")
    parser.add_argument("--out", required=True, help="Output directory (data/parsed)")
    parser.add_argument("--workers", type=int, default=4, help="Worker processes (max 4; the machine is shared)")
    parser.add_argument("--force", action="store_true", help="Re-parse filings whose output JSON already exists")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N manifest rows (testing)")
    parser.add_argument(
        "--failures-out",
        default="data/parse_failures.jsonl",
        help="Where to write the failing-filing log",
    )
    args = parser.parse_args(argv)

    if args.workers > MAX_WORKERS:
        print(
            f"--workers {args.workers} exceeds the {MAX_WORKERS}-process limit for this shared "
            f"machine; using {MAX_WORKERS}.",
            file=sys.stderr,
        )

    results, completed = run(args.manifest, args.out, args.workers, args.force, args.limit)
    n_files, n_failed = rebuild_failures(args.out, args.failures_out)

    n = len(results)
    n_pass = sum(1 for r in results if r["passed"])
    print(f"This run parsed {n} filings: {n_pass} passed, {n - n_pass} failed a check.")
    print(f"Failure list rebuilt from {n_files} parsed files: {n_failed} failing. Written to {args.failures_out}")
    if not completed:
        print("Stopped for timing.lock. Resume with: scripts/parse.sh --force --manifest data/parse_resume.jsonl")
        sys.exit(3)


if __name__ == "__main__":
    main()
