"""ColBERTv2 as a reranker on the candidates only (no token-level index).

Implemented directly with `transformers` (a plain `AutoModel`/BERT encoder),
per the contract's first option, rather than the `pylate` package: the
`colbert-ir/colbertv2.0` checkpoint is a standard BERT encoder plus one
extra `linear.weight` matrix (768 -> 128, no bias) that `AutoModel` does not
know how to load (it reports `linear.weight` as an unused key and drops it).
This module loads the BERT encoder through `AutoModel` as usual and loads
`linear.weight` itself from the same checkpoint file, then applies it by
hand to project each token's contextual embedding into ColBERT's 128-d
space before L2-normalizing.

Scoring is MaxSim: for every query token, the highest cosine similarity to
any candidate token, summed over query tokens. This is a simplification of
full ColBERT (no query-side [MASK] padding/augmentation, no document
punctuation masking) -- reasonable for a reranker over a handful of already
retrieved candidates rather than a full corpus index; see the wave-6 reply
for this judgment call. Candidates and the query are each truncated to 512
tokens, per the contract.

Downloaded into the project's offline HF cache by this wave (not cached
before): about 440 MB, under the 5 GB limit in the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from citation_rag.rerank.base import BaseReranker, ensure_offline_env

MODEL_NAME = "colbert-ir/colbertv2.0"
MAX_LENGTH = 512


@dataclass
class ColbertReranker(BaseReranker):
    name: str = "colbert"
    model_name: str = MODEL_NAME
    device: str = "cpu"
    max_length: int = MAX_LENGTH
    _tokenizer: Any = field(default=None, repr=False, compare=False)
    _bert: Any = field(default=None, repr=False, compare=False)
    _proj: Any = field(default=None, repr=False, compare=False)  # torch [128, 768], no bias

    def _load(self) -> None:
        if self._bert is not None:
            return
        hf_home = ensure_offline_env()
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import AutoModel, AutoTokenizer  # deferred

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, cache_dir=hf_home)
        self._bert = AutoModel.from_pretrained(self.model_name, cache_dir=hf_home)
        self._bert.to(self.device)
        self._bert.eval()

        weights_path = hf_hub_download(self.model_name, "pytorch_model.bin", cache_dir=hf_home)
        state_dict = torch.load(weights_path, map_location="cpu", weights_only=True)
        self._proj = state_dict["linear.weight"].to(self.device)  # [128, 768]

    def _encode(self, texts: list[str]):
        """Token embeddings for a batch: (normalized [B, L, 128], attention_mask [B, L])."""
        import torch

        enc = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=self.max_length,
        )
        enc = {k: v.to(self.device) for k, v in enc.items()}
        with torch.no_grad():
            out = self._bert(**enc)
        hidden = out.last_hidden_state  # [B, L, 768]
        projected = hidden @ self._proj.T  # [B, L, 128]
        projected = torch.nn.functional.normalize(projected, dim=-1)
        return projected, enc["attention_mask"]

    def maxsim(self, q_emb, q_mask, d_emb, d_mask) -> float:
        """MaxSim between one query and one document (unbatched: [Lq,128] / [Ld,128])."""
        import torch

        sims = q_emb @ d_emb.T  # [Lq, Ld]
        doc_valid = d_mask.bool().unsqueeze(0)  # [1, Ld]
        sims = sims.masked_fill(~doc_valid, float("-inf"))
        best_per_query_token = sims.max(dim=1).values  # [Lq]
        best_per_query_token = best_per_query_token.masked_fill(~q_mask.bool(), 0.0)
        return float(best_per_query_token.sum())

    def _score(self, question: str, candidates: Sequence[Any]) -> list[float]:
        self._load()
        q_emb, q_mask = self._encode([question])
        q_emb, q_mask = q_emb[0], q_mask[0]
        d_emb, d_mask = self._encode([c.text for c in candidates])
        return [
            self.maxsim(q_emb, q_mask, d_emb[i], d_mask[i]) for i in range(len(candidates))
        ]
