You check whether a cited chunk of a filing backs up a claim sentence.

Claim sentence: {claim_sentence}
Cited chunk text: {cited_chunk_text}

Choose exactly one label:
- "supports": the cited chunk states or directly implies the claim sentence.
- "does_not_support": the cited chunk does not state or imply the claim sentence.

Write your reason first, then the label. Reply with JSON only, in this exact shape:
{{"reason": "<one or two sentences>", "label": "supports" | "does_not_support"}}
