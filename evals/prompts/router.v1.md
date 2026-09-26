You read one question about SEC 10-K filings. Pull out every company the question names, by any form: full legal name, short name, ticker symbol, or a clear description (for example "the iPhone maker").

Question: {question}

Reply with JSON only, in this exact shape:
{{"candidates": [{{"name": "<company name or description, or null>", "ticker": "<ticker symbol, or null>"}}], "none": <true if no company is named>}}

If the question names no company (a general question), reply with an empty "candidates" list and "none": true.
