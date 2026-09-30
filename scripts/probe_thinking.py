"""GPU probe, run at the start of 7b: does think=True produce thinking with the fixed client?
Exits 0 and prints the thinking length; exits 1 if thinking is still empty."""
import sys
from citation_rag.llm import OllamaClient
c = OllamaClient(model="qwen3:8b", think=True, timeout=900)
out = c.complete('Answer as JSON {"answer": "..."}: what is 17 * 23?')
print(f"thinking_chars={c.last_thinking_chars} output_tokens={c.last_output_tokens} response={out[:120]!r}")
sys.exit(0 if (c.last_thinking_chars or 0) > 50 else 1)
