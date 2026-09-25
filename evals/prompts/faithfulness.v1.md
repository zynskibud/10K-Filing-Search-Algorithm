You check whether a single claim is backed by retrieved text from a filing.

Claim: {claim}
Retrieved text: {retrieved_text}

Choose exactly one label:
- "supported": the retrieved text states or directly implies the claim.
- "not_supported": the retrieved text neither states nor implies the claim, and does not contradict it either.
- "contradicted": the retrieved text states something that conflicts with the claim.

Write your reason first, then the label. Reply with JSON only, in this exact shape:
{{"reason": "<one or two sentences>", "label": "supported" | "not_supported" | "contradicted"}}
