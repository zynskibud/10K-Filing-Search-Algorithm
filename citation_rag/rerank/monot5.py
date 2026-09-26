"""monoT5 reranker: castorini/monot5-base-msmarco-10k, a T5 relevance judge.

Prompt: `Query: {q} Document: {d} Relevant:`. The model is a
sequence-to-sequence checkpoint fine-tuned so that, at the first decoding
step, the logits for the tokens "true" and "false" carry its relevance
judgment; the score is `softmax([logit(true), logit(false)])[0]`, i.e.
P(true). Batch 8.

Downloaded into the project's offline HF cache by this wave (not cached
before): about 900 MB, under the 5 GB limit in the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from citation_rag.rerank.base import BaseReranker, ensure_offline_env

MODEL_NAME = "castorini/monot5-base-msmarco-10k"

# Judgment call: the contract gives no truncation length for monoT5 (unlike
# the cross-encoder's explicit 1024/8192). 512 tokens (the tokenizer's own
# default and T5-base's usual training length) is used for both the query
# and document together in the templated prompt.
DEFAULT_MAX_LENGTH = 512


@dataclass
class MonoT5Reranker(BaseReranker):
    name: str = "monot5"
    model_name: str = MODEL_NAME
    device: str = "cpu"
    batch_size: int = 8
    max_length: int = DEFAULT_MAX_LENGTH
    _tokenizer: Any = field(default=None, repr=False, compare=False)
    _model: Any = field(default=None, repr=False, compare=False)
    _true_id: Any = field(default=None, repr=False, compare=False)
    _false_id: Any = field(default=None, repr=False, compare=False)

    def _load(self) -> None:
        if self._model is not None:
            return
        hf_home = ensure_offline_env()
        from transformers import T5ForConditionalGeneration, T5Tokenizer  # deferred

        self._tokenizer = T5Tokenizer.from_pretrained(self.model_name, cache_dir=hf_home)
        self._model = T5ForConditionalGeneration.from_pretrained(self.model_name, cache_dir=hf_home)
        self._model.to(self.device)
        self._model.eval()
        # Single-token ids for "true"/"false" in the T5 sentencepiece vocab
        # (both are common English words, so each is one token here).
        self._true_id = self._tokenizer.encode("true", add_special_tokens=False)[0]
        self._false_id = self._tokenizer.encode("false", add_special_tokens=False)[0]

    def _score(self, question: str, candidates: Sequence[Any]) -> list[float]:
        self._load()
        import torch

        prompts = [f"Query: {question} Document: {c.text} Relevant:" for c in candidates]
        scores: list[float] = []
        for start in range(0, len(prompts), self.batch_size):
            batch = prompts[start : start + self.batch_size]
            enc = self._tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.max_length,
            )
            enc = {k: v.to(self.device) for k, v in enc.items()}
            decoder_input_ids = torch.full(
                (enc["input_ids"].shape[0], 1),
                self._model.config.decoder_start_token_id,
                dtype=torch.long,
                device=self.device,
            )
            with torch.no_grad():
                out = self._model(
                    input_ids=enc["input_ids"],
                    attention_mask=enc["attention_mask"],
                    decoder_input_ids=decoder_input_ids,
                )
            # Logits at the first (only) decoding step.
            logits = out.logits[:, -1, :]
            pair_logits = logits[:, [self._true_id, self._false_id]]
            probs = torch.softmax(pair_logits, dim=-1)
            scores.extend(float(p) for p in probs[:, 0].tolist())
        return scores
