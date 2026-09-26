You are answering a question about SEC 10-K filings, using only the numbered blocks of filing text below.

Rules:
- Answer only from the blocks. Do not use outside knowledge.
- Cite every claim with the block number in brackets, for example [1].
- For each citation, give the exact quote from the block that supports it.
- If the blocks do not contain the answer, say exactly: "The filings do not contain this information." Give no citations in that case.
- Keep the answer short.

Reply with JSON only, in this exact shape:
{{"answer": "<prose with [n] markers>", "citations": [{{"ref": <int>, "quote": "<exact quote from the block>"}}], "answerable": <true or false>}}

{blocks}

Question: {question}
