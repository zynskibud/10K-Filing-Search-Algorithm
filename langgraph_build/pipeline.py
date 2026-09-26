"""Wave 8b, contract point 1: the RAG task as a LangGraph two-node graph.

Node `retrieve` calls the vector store's own `as_retriever(k=8)` (contract
point 1), optionally filtered to a set of companies (CIK strings) so the
same graph can answer both a general question and a per-company one, the
way the eval harness's golden set requires. Node `generate` builds a prompt
that gives the model the retrieved documents' metadata (company, item, page
range) and asks it to cite the page number for every claim, then calls the
Ollama chat model through `langchain_ollama.ChatOllama`.

No BM25 here: `PGVector.as_retriever` has no lexical-search option built
in, and adding one is not a one-line default (contract point 1 records
this: BM25 was **not** added to the default build; see NOTES.md's effort
log for what adding it actually costs).

`build_graph` takes `llm` (and, for `retrieve`, the vector store) as
parameters rather than constructing them itself, so tests can inject a fake
chat model and never call Ollama (protocol: LIGHT, no Ollama calls).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

DEFAULT_K = 8
OLLAMA_MODEL = "qwen3:8b"

CITE_PROMPT_TEMPLATE = """You are answering a question about a company's SEC 10-K filing. Use ONLY \
the excerpts below. For every claim, cite the page it came from in the form \
(p. N), using the excerpt's own page number. If the excerpts do not contain \
the answer, say "I do not know."

{context}

Question: {question}
Answer:"""


class GraphState(TypedDict, total=False):
    question: str
    companies: list[str] | str | None
    context: list[Document]
    answer: str


def format_context(docs: list[Document]) -> str:
    """One block per retrieved document: a `[n]` header with the metadata
    the citation prompt asks the model to cite (company, item, page), then
    the document's own text."""
    parts = []
    for i, doc in enumerate(docs, start=1):
        meta = doc.metadata
        header = (
            f"[{i}] {meta.get('company')} | Item {meta.get('item')} | "
            f"page {meta.get('page_start')}"
        )
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n".join(parts)


def _company_filter(companies: list[str] | str | None) -> dict | None:
    if not companies or companies == "general":
        return None
    ciks = companies if isinstance(companies, list) else [companies]
    return {"cik": {"$in": ciks}}


def build_graph(vectorstore, llm, k: int = DEFAULT_K):
    """Compile the two-node graph (contract point 1: "a two-node graph
    (retrieve, generate)"). `vectorstore` is a `langchain_postgres.PGVector`
    (or anything with `.as_retriever(**kwargs)`); `llm` is anything with
    `.invoke(prompt) -> object with .content` (a real `ChatOllama`, or a
    fake in tests).
    """

    def retrieve(state: GraphState) -> dict[str, Any]:
        filt = _company_filter(state.get("companies"))
        search_kwargs: dict = {"k": k}
        if filt is not None:
            search_kwargs["filter"] = filt
        retriever = vectorstore.as_retriever(search_kwargs=search_kwargs)
        docs = retriever.invoke(state["question"])
        return {"context": docs}

    def generate(state: GraphState) -> dict[str, Any]:
        prompt = CITE_PROMPT_TEMPLATE.format(
            context=format_context(state.get("context", [])),
            question=state["question"],
        )
        response = llm.invoke(prompt)
        text = getattr(response, "content", None)
        if text is None:
            text = str(response)
        return {"answer": text}

    graph = StateGraph(GraphState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()


def load_chat_model(model: str = OLLAMA_MODEL, temperature: float = 0.0):
    """The real Ollama chat model, through `langchain_ollama.ChatOllama`.
    Never called in tests -- every test uses a fake `llm` object instead
    (protocol: LIGHT, no Ollama calls)."""
    from langchain_ollama import ChatOllama

    from citation_rag.settings import Settings

    settings = Settings()
    return ChatOllama(model=model, base_url=settings.ollama_url, temperature=temperature)


def answer_question(
    graph, question: str, companies: list[str] | str | None = None
) -> dict[str, Any]:
    """Run the compiled graph once and return its final state
    (`question`, `companies`, `context`, `answer`)."""
    return graph.invoke({"question": question, "companies": companies})


def main(argv: list[str] | None = None) -> int:
    """CLI: answer one question against an already-ingested collection.

    uv run --group langgraph python -m langgraph_build.pipeline \\
        --question "What customer risk does Fixture Corp describe?" \\
        --collection wave8b
    """
    import argparse

    from langchain_postgres import PGVector

    from citation_rag.settings import Settings
    from langgraph_build.ingest import (
        DEFAULT_COLLECTION_NAME,
        _psycopg_connection_string,
        load_embeddings,
    )

    parser = argparse.ArgumentParser(prog="langgraph_build.pipeline")
    parser.add_argument("--question", required=True)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--companies", nargs="*", default=None)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args(argv)

    settings = Settings()
    embeddings = load_embeddings()
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=args.collection,
        connection=_psycopg_connection_string(settings.database_url),
        use_jsonb=True,
    )
    llm = load_chat_model()
    graph = build_graph(vectorstore, llm, k=args.k)
    state = answer_question(graph, args.question, companies=args.companies)
    print(state.get("answer", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
