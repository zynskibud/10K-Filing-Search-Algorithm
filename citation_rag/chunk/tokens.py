"""Token ruler for chunking (wave 4b, contract point 1).

All chunk sizes in this package are measured with the ``bge-small-en-v1.5``
tokenizer, loaded once from the project's offline model cache. This module
never runs model inference -- tokenizer only, CPU, no network.
"""

from __future__ import annotations

import os
from functools import lru_cache

MODEL_ID = "BAAI/bge-small-en-v1.5"


def _configure_offline_env() -> str:
    """Point the `huggingface_hub` cache lookup at the project's cache.

    `citation_rag.settings.Settings.hf_home` (from `.env`) gives the cache
    root, for example `.../Citation_Rag/.cache/hf`, now always an absolute
    path (integration-1 item 6). Returns that path so the caller can also
    pass it straight to `from_pretrained(..., cache_dir=...)`: relying on
    `HF_HUB_CACHE`/`HF_HOME` env var ordering is fragile, since
    `huggingface_hub` reads them into a frozen constant the first time it is
    imported anywhere in the process, so a module imported earlier can lock
    in a different cache dir before this function ever runs. Environment
    variables already set (for example by a test or a caller) are left
    alone.
    """
    from citation_rag.settings import Settings

    settings = Settings()
    os.environ.setdefault("HF_HOME", settings.hf_home)
    os.environ.setdefault("HF_HUB_CACHE", settings.hf_home)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    return settings.hf_home


@lru_cache(maxsize=1)
def get_tokenizer():
    """Return the cached bge-small tokenizer instance (fast, offline)."""
    hf_home = _configure_offline_env()
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(MODEL_ID, cache_dir=hf_home)


def count_tokens(text: str) -> int:
    """Number of bge-small tokens in `text`, no special tokens added."""
    if not text:
        return 0
    tok = get_tokenizer()
    return len(tok(text, add_special_tokens=False)["input_ids"])


def token_offsets(text: str) -> list[tuple[int, int]]:
    """Character `(start, end)` offset of every token in `text`, in order.

    No special tokens. Offsets are into `text` itself, so
    `text[start:end]` recovers the exact substring each token came from
    (a token can map to an empty span for a stripped character; callers
    that build chunk text from a contiguous run of these offsets get back
    an exact substring of `text`).
    """
    if not text:
        return []
    tok = get_tokenizer()
    enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    return [tuple(span) for span in enc["offset_mapping"]]
