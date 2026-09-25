You split a model's answer into its atomic factual claims.

Answer: {answer}

List every distinct factual claim the answer makes, as short standalone sentences. Do not add claims that are not in the answer. Do not judge whether the claims are true.

Reply with JSON only, in this exact shape:
{{"reason": "<one sentence on how you split the answer>", "claims": ["<claim 1>", "<claim 2>"]}}
