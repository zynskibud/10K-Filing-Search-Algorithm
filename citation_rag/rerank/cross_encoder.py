"""Cross-encoder reranker: BAAI/bge-reranker-v2-m3 via sentence_transformers.

Already in the project's offline HF cache (pulled in an earlier wave), so
this reranker never downloads anything. Batch 16. Max length 1024 tokens for
ordinary chunks; the contract allows 8,192 only for whole-section chunks
under chunking strategy s4. This module does not know which strategy
produced its candidates, so the caller passes `max_length=8192` explicitly
when reranking s4 candidates; the default (1024) covers every other case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from citation_rag.rerank.base import BaseReranker, ensure_offline_env

MODEL_NAME = "BAAI/bge-reranker-v2-m3"


@dataclass
class CrossEncoderReranker(BaseReranker):
    name: str = "cross_encoder"
    model_name: str = MODEL_NAME
    device: str = "cpu"
    batch_size: int = 16
    max_length: int = 1024
    _model: Any = field(default=None, repr=False, compare=False)

    def _get_model(self):
        if self._model is None:
            hf_home = ensure_offline_env()
            from sentence_transformers import CrossEncoder  # deferred: needs env vars set first

            self._model = CrossEncoder(
                self.model_name,
                device=self.device,
                max_length=self.max_length,
                cache_folder=hf_home,
            )
        return self._model

    def _score(self, question: str, candidates: Sequence[Any]) -> list[float]:
        model = self._get_model()
        pairs = [[question, c.text] for c in candidates]
        scores = model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return [float(s) for s in scores]
