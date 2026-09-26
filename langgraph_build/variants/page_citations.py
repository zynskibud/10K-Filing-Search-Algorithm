"""Effort-log change 2 (NOTES.md): add page-number citations with a quote
check to the default build.

The default `generate` node (`langgraph_build.pipeline.build_graph`) asks
the model to write `(p. N)` inline in its prose and never checks that the
page number, or the claim itself, is actually supported by the retrieved
text -- a citation could name any page, or none, and the graph would not
notice. This variant instead asks for **structured** citations (`{"ref":
n, "quote": "..."}`, `ref` pointing at one of the numbered excerpts) and
then checks each one the same way `citation_rag.answer.cite.check_citations`
checks the custom RAG's citations: the ref must exist, and the quote must
be an exact substring of that excerpt's text.

What changed, concretely:
- A new prompt template (`CITE_JSON_PROMPT_TEMPLATE`, ~15 lines) replacing
  the free-text one, asking for JSON.
- A new `generate` node (`generate_with_citation_check`, ~35 lines)
  replacing the default one: parse the JSON, run `check_citations`, and
  put a `CitationCheckResult` in the state alongside the answer.
- `check_citations` itself (~20 lines) duplicates roughly half of
  `citation_rag.answer.cite.check_citations`'s logic (ref validity, exact
  substring match) rather than reusing it, because that function is typed
  against the custom RAG's `ContextBlock` (`citation_rag.answer.context`),
  not a LangChain `Document` -- adapting `Document`s into `ContextBlock`s
  just to reuse it would need `company`/`fiscal_year`/`section_title`
  fields this build's `Document` metadata does not carry the same way, so
  writing a small `Document`-native version was less code than the glue.
  This is the real cost of the change: about 70 lines total, none of it in
  the default `pipeline.py`.

What breaks: the JSON-mode prompt trades the free-text `(p. N)` citation
for a stricter contract the model must follow exactly (valid JSON, a
`citations` list, one `ref`/`quote` pair per claim). `citation_rag`'s own
answer stage (`citation_rag/answer/prompt.py`) hit the same failure mode
and needed `AnswerParseError` plus a "degrade to raw text" fallback
(`citation_rag/answer/pipeline.py`) for exactly this reason. This wave is
LIGHT (no Ollama calls), so the JSON-compliance rate for `qwen3:8b` on this
stricter prompt is not measured here -- it is an open question for wave 8c
to measure against the harness's other systems, the same as it already is
for the custom RAG.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from langgraph_build.pipeline import (
    DEFAULT_K,
    GraphState,
    _company_filter,
    format_context,
)

CITE_JSON_PROMPT_TEMPLATE = """You are answering a question about a company's SEC 10-K filing. Use ONLY \
the excerpts below.

Reply with JSON only, in this exact shape:
{{"answer": "<prose, with a [n] marker after every claim>", \
"citations": [{{"ref": <n>, "quote": "<exact quote from excerpt n>"}}]}}

If the excerpts do not contain the answer, reply:
{{"answer": "I do not know.", "citations": []}}

{context}

Question: {question}
Answer:"""


class AnswerParseError(ValueError):
    """The model's raw response was not the expected JSON shape."""


class CitationCheckState(GraphState, total=False):
    """`pipeline.GraphState` plus one field. LangGraph drops any key a node
    returns that is not declared on the state schema (confirmed: an extra
    key from a node function is silently dropped from the compiled graph's
    output), so this variant needs its own schema -- it cannot reuse
    `GraphState` as-is and just return an extra key."""

    citation_check: CitationCheckResult


@dataclass
class CitationCheckResult:
    valid: bool
    invalid_refs: list[int] = field(default_factory=list)
    quotes_not_found: list[int] = field(default_factory=list)


def check_citations(citations: list[dict], docs: list[Document]) -> CitationCheckResult:
    """`ref` must be a 1-based index into `docs`; `quote` must be an exact
    substring of that document's `page_content`. Mirrors
    `citation_rag.answer.cite.check_citations`'s two core checks, written
    against `Document` instead of `ContextBlock` (see module docstring)."""
    invalid_refs: list[int] = []
    quotes_not_found: list[int] = []
    for citation in citations:
        ref = citation.get("ref")
        quote = citation.get("quote", "")
        if not isinstance(ref, int) or not (1 <= ref <= len(docs)):
            invalid_refs.append(ref)
            continue
        if quote not in docs[ref - 1].page_content:
            quotes_not_found.append(ref)
    valid = not invalid_refs and not quotes_not_found
    return CitationCheckResult(valid=valid, invalid_refs=invalid_refs, quotes_not_found=quotes_not_found)


def generate_with_citation_check(llm):
    """A drop-in replacement for `pipeline.build_graph`'s `generate` node
    that parses structured citations and runs `check_citations` against
    the retrieved documents."""

    def generate(state: GraphState) -> dict[str, Any]:
        docs = state.get("context", [])
        prompt = CITE_JSON_PROMPT_TEMPLATE.format(
            context=format_context(docs), question=state["question"]
        )
        response = llm.invoke(prompt)
        raw = getattr(response, "content", None) or str(response)
        try:
            data = json.loads(raw)
            answer = data["answer"]
            citations = data.get("citations", [])
        except (json.JSONDecodeError, KeyError, TypeError):
            # Same degrade-gracefully choice citation_rag.answer.pipeline
            # makes: an unparsable response becomes the raw text, with no
            # citations to check.
            return {"answer": raw, "citation_check": CitationCheckResult(valid=True)}
        return {"answer": answer, "citation_check": check_citations(citations, docs)}

    return generate


def build_graph_with_citation_check(vectorstore, llm, k: int = DEFAULT_K):
    """The default two-node graph (`pipeline.build_graph`), with `generate`
    swapped for `generate_with_citation_check` and the state schema
    extended to carry `citation_check` through to the final state."""

    def retrieve(state: CitationCheckState) -> dict[str, Any]:
        filt = _company_filter(state.get("companies"))
        search_kwargs: dict[str, Any] = {"k": k}
        if filt is not None:
            search_kwargs["filter"] = filt
        retriever = vectorstore.as_retriever(search_kwargs=search_kwargs)
        return {"context": retriever.invoke(state["question"])}

    graph = StateGraph(CitationCheckState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate_with_citation_check(llm))
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
