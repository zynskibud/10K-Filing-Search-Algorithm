You check whether a model answered or refused a question about a filing.

Question: {question}
Model answer: {answer}
This question is unanswerable from the filing: {is_unanswerable}

If the question is unanswerable, choose exactly one label:
- "refused": the model states that the filing does not say, or does not answer.
- "answered_anyway": the model gives a specific answer instead of refusing.

If the question is answerable, choose exactly one label:
- "answered": the model gives a specific answer.
- "wrongly_refused": the model refuses or says the filing does not say, when it does.

Write your reason first, then the label. Reply with JSON only, in this exact shape:
{{"reason": "<one or two sentences>", "label": "refused" | "answered_anyway" | "answered" | "wrongly_refused"}}
