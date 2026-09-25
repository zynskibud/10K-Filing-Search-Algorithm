You are grading a financial-filing question answering system.

Question: {question}
Reference answer: {reference_answer}
Model answer: {answer}

Judge the model answer against the reference answer for this question. Choose exactly one label:
- "correct": the model answer states the same fact as the reference answer, with no material error.
- "partial": the model answer captures part of the reference answer, or is directionally right but incomplete or slightly off.
- "incorrect": the model answer contradicts the reference answer, or is unrelated to it.

Write your reason first, then the label. Reply with JSON only, in this exact shape:
{{"reason": "<one or two sentences>", "label": "correct" | "partial" | "incorrect"}}
