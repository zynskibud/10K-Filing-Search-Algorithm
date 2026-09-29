# Wave 6 report: rerankers (CPU arms)

Index bge_small__s2, hybrid search, 50 candidates in, top 8 out, 96 dev questions, oracle routing. Rerankers ran on CPU.

| Reranker | Recall@8 | 95% CI | MRR | Table | Paraphrased | p50 latency |
|---|---|---|---|---|---|---|
| none | 0.795 | [0.71, 0.88] | 0.519 | 0.90 | 0.44 | 45 ms |
| ColBERTv2 | 0.602 | [0.49, 0.70] | 0.366 | 0.62 | 0.50 | 9.0 s |
| bge-reranker-v2-m3 | 0.819 | [0.73, 0.90] | 0.541 | 0.90 | 0.50 | 20.1 s |
| monoT5 | 0.831 | [0.75, 0.90] | 0.558 | 0.95 | 0.75 | 8.9 s |
| MMR | skipped: the runner did not pass its vector lookup | | | | | |
| listwise Qwen | waits for the GPU GO (wave 7) | | | | | |

## What it says
- monoT5 and the cross-encoder add 2 to 4 points of recall and MRR. The gain is inside the confidence interval on 96 questions.
- monoT5 is the best reranker here and the clear winner on paraphrased questions (0.75 against 0.44).
- ColBERTv2 hurts (0.60): the simplified implementation (no query augmentation) is weaker than fusion order.
- Cost on CPU: 200 to 450 times the latency of no reranker. On the GPU this drops by roughly 10 times.

## Decision
Final system: monoT5 on the GPU if wave 7 shows the answer gain holds; otherwise no reranker. MMR is re-run with its lookup when the GPU arm runs.
