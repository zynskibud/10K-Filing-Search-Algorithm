"""Query embedding on CPU: bge-small-en-v1.5 from the project HF cache, offline.

Used only to embed the question at retrieval time (a handful of tokens, one
call per question). This module never uses MPS or Ollama, so the wave 5
experiments run without the GPU (per the contract).

bge-small expects the query instruction prefix "Represent this sentence for
searching relevant passages: " on queries only; documents are embedded with
no instruction (see the wave 4c contract for `models.py`, not yet built as
of this wave, hence the small local loader here instead of importing it).
"""

from __future__ import annotations

import os
from functools import lru_cache

QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MODEL_NAME = "BAAI/bge-small-en-v1.5"


def _ensure_offline_env() -> str | None:
    """Set HF_HUB_OFFLINE and HF_HOME/HF_HUB_CACHE, return the cache dir to use.

    Also returns the path so callers can pass it as `cache_folder=` directly:
    `HF_HUB_CACHE` is read into a frozen constant the first time
    `huggingface_hub` is imported anywhere in the process, so if some other
    module imports it first (before this function ever runs), setting the
    env var afterwards has no effect. Passing `cache_folder=` to
    `SentenceTransformer` sidesteps that import-order hazard.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from citation_rag.settings import Settings

        hf_home = Settings().hf_home
    except Exception:
        hf_home = None
    if hf_home:
        os.environ.setdefault("HF_HOME", hf_home)
        # The project cache stores model dirs directly under HF_HOME
        # (no "hub" subfolder), so point HF_HUB_CACHE there explicitly.
        os.environ.setdefault("HF_HUB_CACHE", hf_home)
    return hf_home or os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")


@lru_cache(maxsize=1)
def _get_model():
    hf_home = _ensure_offline_env()
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, device="cpu", cache_folder=hf_home)


def embed_query(text: str) -> list[float]:
    """Encode one query string into a normalized 384-d vector (as a list of floats)."""
    model = _get_model()
    vec = model.encode(QUERY_INSTRUCTION + text, normalize_embeddings=True)
    return [float(x) for x in vec]


def embed_doc(text: str) -> list[float]:
    """Encode one document/chunk string with no instruction prefix (bge-small convention).

    Only used to build this wave's test fixture; production document
    embedding is wave 4c's job (`citation_rag.index.embed`).
    """
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return [float(x) for x in vec]
