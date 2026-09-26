"""Answer prompt: `evals/prompts/answer.v1.md` plus the block/question builder.

Contract (wave 7a, point 2): the model is told to answer only from the given
blocks, cite every claim with `[ref]`, give the exact quote per citation, and
refuse cleanly (a fixed sentence, no citations) when the blocks do not
contain the answer. Output is JSON: `answer` (prose with `[n]` markers),
`citations` (list of `{ref, quote}`), `answerable` (bool).

`mode="no_docs"` (plan section 11, run A) sends no blocks at all and asks the
model to answer from its own training knowledge or say it does not know;
citations are not required in that mode. Judgment call: this second
instruction is not its own versioned prompts/ file. It is a short, fixed
no-retrieval baseline used only for run A, not a piece of the RAG system
under test, so it lives as a constant next to the builder instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from citation_rag.answer.context import ContextBlock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "evals" / "prompts" / "answer.v1.md"

NO_DOCS_TEMPLATE = """You are answering a question about a company's SEC 10-K filing, from your \
own training knowledge only. No filing text is given to you.

Rules:
- Answer from what you know, if you are confident.
- If you do not know, say exactly: "I do not know." Do not guess.
- Keep the answer short.

Reply with JSON only, in this exact shape:
{{"answer": "<prose>", "answerable": <true or false>}}

Question: {question}
"""


class AnswerParseError(ValueError):
    """Raised when the model's raw response is not the expected JSON shape."""


def _load_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def format_block(block: "ContextBlock") -> str:
    header = (
        f"[{block.ref}] {block.company} | FY{block.fiscal_year} | "
        f"Item {block.item} | {block.section_title} | pages {block.page_start}-{block.page_end}"
    )
    return f"{header}\n{block.text}"


def build_prompt(question: str, blocks: "list[ContextBlock]", mode: str = "rag") -> str:
    """Build the full prompt string sent to the answer LLM.

    `mode="rag"` (default): the instructions plus every block plus the
    question. `mode="no_docs"`: the no-retrieval baseline instructions plus
    the question only (`blocks` is ignored).
    """
    if mode == "no_docs":
        return NO_DOCS_TEMPLATE.format(question=question)
    if mode != "rag":
        raise ValueError(f"mode must be 'rag' or 'no_docs', got {mode!r}")
    blocks_text = "\n\n".join(format_block(b) for b in blocks)
    template = _load_template()
    return template.format(blocks=blocks_text, question=question)


def parse_response(raw: str, *, require_citations: bool = True) -> dict[str, Any]:
    """Parse the model's JSON reply into a dict with `answer`, `citations`,
    `answerable`. Raises AnswerParseError on malformed output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnswerParseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AnswerParseError("response JSON must be an object")
    if "answer" not in data:
        raise AnswerParseError("response JSON is missing 'answer'")
    data.setdefault("answerable", True)
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        raise AnswerParseError("'citations' must be a list")
    if not require_citations:
        citations = []
    data["citations"] = citations
    return data
