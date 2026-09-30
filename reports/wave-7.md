# Wave 7 report: answer runs (Qwen3 8B), rerank GPU arms

Ran 2026-09-29 19:33Z to 2026-09-30 04:53Z under gpu.lock, exit 0. Dev split, 96 questions (83 answerable, 13 unanswerable). Configuration from the winners: bge_small__s2 over the full corpus (wave 4w), hybrid search, monoT5 reranker (50 in, 8 out), small-to-big context up to 20k tokens.

## Answer runs (before judging)

| Run | Context | Thinking | Answered | Refused | Valid citations | p50 time |
|---|---|---|---|---|---|---|
| A_think | none | on | 3 | 93 | n/a | 1.0 s |
| A_nothink | none | off | 3 | 93 | n/a | 1.1 s |
| B_think | RAG | on | 80 | 16 | 71.9% | 61 s |
| B_nothink | RAG | off | 81 | 15 | 78.1% | 70 s |

"Valid citations" = share of answers whose every citation points to a sent block and whose quote is found in that block (code check, no judge).

By type, run B (both settings): fact lookup 21/21 answered, number from table 21/21, exact term 9/9, multi-part 11/11, paraphrased 13/16, general 3 to 4 of 5, unanswerable: answered 2 of 13 (should refuse all 13).

## What it says (so far)
- Without documents, Qwen refuses 93 of 96. The no-retrieval baseline knows almost nothing about these post-cutoff filings, as designed.
- With RAG it answers 80 to 81 of 96, and refuses 11 of the 13 unanswerable questions correctly.
- About one in four answers has a citation problem (a quote not found in its block, or a marker without a citation). This is the number to improve.
- **Thinking did not change anything measurable, and it probably was not applied.** Median output was under 100 tokens in both runs; with thinking on, Qwen normally writes hundreds of thinking tokens. The shared client sends `think` to Ollama's generate endpoint together with `format: json`; the two may not combine. To verify before any claim about thinking.
- Time per question is 50 to 70 s: about 15 s of monoT5 reranking on CPU and 35 s of Qwen generation on 5k input tokens.

## Correctness: not yet measured
The judge (gpt-oss:20b) has not scored these answers, and calibration needs your 40 labels first. Correctness, faithfulness, and citation support come in the next step.

## Rerank GPU arms
| Reranker | Result |
|---|---|
| MMR | recall@8 0.663 [0.55, 0.76], MRR 0.465. Not comparable to wave 6: it ran on the full-corpus index (607k chunks) while wave 6 ran on the 150-filing subset. |
| listwise Qwen | failed: HTTP read timeout on the first call (50 candidates in one prompt). Needs a longer timeout or fewer candidates. |

## Still to do (GPU)
1. Export the 40-answer calibration sheet; you label it (about 1 hour).
2. Judge the 4 runs with gpt-oss:20b, then measure agreement with your labels.
3. Check the thinking flag; rerun B_think only if it was not applied.
4. Listwise reranker with a 20-candidate prompt and a 900 s timeout.
