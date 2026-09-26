"""Embedding model loading for wave 4c.

Loads `bge_small` (BAAI/bge-small-en-v1.5, 384-d) and `bge_m3` (BAAI/bge-m3,
1024-d, dense only) from the project's Hugging Face cache, offline.

`HF_HOME` and `HF_HUB_OFFLINE` must be set before `sentence_transformers` (and
the `transformers` it depends on) is imported, so this module reads
`citation_rag.settings.Settings` and sets the environment first, then imports
`SentenceTransformer` lazily inside `load_embedder`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

import numpy as np

from citation_rag.settings import Settings

if TYPE_CHECKING:  # pragma: no cover
    from sentence_transformers import SentenceTransformer

# The standard bge-small query instruction. Prepended for queries only; never
# for documents (documents already carry the chunk prefix from citation_rag.chunk.prefix).
BGE_SMALL_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

MODEL_IDS = {
    "bge_small": "BAAI/bge-small-en-v1.5",
    "bge_m3": "BAAI/bge-m3",
}

DIMENSIONS = {
    "bge_small": 384,
    "bge_m3": 1024,
}

# Per the wave-4 contract: batch size 64 for bge-small, 16 for bge-m3.
BATCH_SIZES = {
    "bge_small": 64,
    "bge_m3": 16,
}

SUPPORTED_DEVICES = ("cpu", "mps")


def _ensure_offline_env() -> str:
    """Set HF_HOME (from .env via Settings) and force offline mode.

    Returns the HF_HOME path. Safe to call more than once.
    """
    settings = Settings()
    os.environ.setdefault("HF_HOME", settings.hf_home)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return os.environ["HF_HOME"]


@dataclass
class Embedder:
    """A loaded embedding model plus the encode conventions for that model."""

    name: str
    model: "SentenceTransformer"
    dim: int
    batch_size: int
    device: str

    def encode_docs(
        self,
        texts: Iterable[str],
        batch_size: int | None = None,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Encode document/chunk text. No instruction is prepended."""
        return self.model.encode(
            list(texts),
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
        )

    def encode_query(self, text: str, show_progress_bar: bool = False) -> np.ndarray:
        """Encode a single query. bge_small gets the standard instruction prefix."""
        if self.name == "bge_small":
            text = BGE_SMALL_QUERY_INSTRUCTION + text
        vectors = self.model.encode(
            [text],
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
        )
        return vectors[0]


def load_embedder(name: str, device: str = "cpu") -> Embedder:
    """Load `bge_small` or `bge_m3` from the project cache, offline.

    Args:
        name: "bge_small" or "bge_m3".
        device: "cpu" or "mps". This wave never uses "mps" in tests.
    """
    if name not in MODEL_IDS:
        raise ValueError(f"unknown embedder name: {name!r}; expected one of {sorted(MODEL_IDS)}")
    if device not in SUPPORTED_DEVICES:
        raise ValueError(f"unsupported device: {device!r}; expected one of {SUPPORTED_DEVICES}")

    hf_home = _ensure_offline_env()

    from sentence_transformers import SentenceTransformer  # deferred: needs env vars set first

    model = SentenceTransformer(MODEL_IDS[name], device=device, cache_folder=hf_home)
    return Embedder(
        name=name,
        model=model,
        dim=DIMENSIONS[name],
        batch_size=BATCH_SIZES[name],
        device=device,
    )
