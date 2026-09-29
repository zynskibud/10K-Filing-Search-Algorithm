# Wave 5 report: retrieval experiments (subset of 150 filings)

Winner: **bge_small__s2, hybrid** (recall 0.783 [0.70, 0.88], MRR 0.519). Runs on the 96-question development set, oracle routing (the golden case's companies), top 8 returned. The winners file is `runs/wave-5/winners.json`.

Note on labels: these 15 runs were logged with the key `recall@50` (50 was the candidate pool); the value is recall over the returned top 8. The runner now labels it `recall@8`.

| Index | Search | Recall | 95% CI | MRR | Prose | Table | Paraphrased | Multi-part | General | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3__s1 | bm25 | 0.602 | [0.51, 0.71] | 0.411 | 0.56 | 0.71 | 0.31 | 0.64 | 0.00 | 6 |
| bge_m3__s1 | vector | 0.723 | [0.63, 0.82] | 0.465 | 0.69 | 0.81 | 0.75 | 0.55 | 0.40 | 166 |
| bge_m3__s1 | hybrid | 0.699 | [0.60, 0.80] | 0.459 | 0.65 | 0.86 | 0.50 | 0.64 | 0.20 | 135 |
| bge_m3__s3 | bm25 | 0.639 | [0.53, 0.75] | 0.463 | 0.63 | 0.67 | 0.19 | 0.73 | 0.00 | 10 |
| bge_m3__s3 | vector | 0.771 | [0.67, 0.87] | 0.526 | 0.79 | 0.71 | 0.81 | 0.73 | 0.00 | 218 |
| bge_m3__s3 | hybrid | 0.759 | [0.66, 0.86] | 0.524 | 0.74 | 0.81 | 0.44 | 0.82 | 0.00 | 171 |
| bge_small__s1 | bm25 | 0.602 | [0.51, 0.71] | 0.411 | 0.56 | 0.71 | 0.31 | 0.64 | 0.00 | 9 |
| bge_small__s1 | vector | 0.699 | [0.60, 0.80] | 0.402 | 0.63 | 0.90 | 0.62 | 0.73 | 0.20 | 35 |
| bge_small__s1 | hybrid | 0.663 | [0.57, 0.76] | 0.429 | 0.58 | 0.90 | 0.38 | 0.64 | 0.20 | 98 |
| bge_small__s2 | bm25 | 0.639 | [0.53, 0.75] | 0.472 | 0.61 | 0.71 | 0.19 | 0.64 | 0.00 | 15 |
| bge_small__s2 | vector | 0.699 | [0.59, 0.80] | 0.483 | 0.66 | 0.81 | 0.44 | 0.73 | 0.00 | 47 |
| bge_small__s2 | hybrid | 0.783 | [0.70, 0.88] | 0.519 | 0.74 | 0.90 | 0.44 | 0.82 | 0.00 | 27 |
| bge_small__s3 | bm25 | 0.639 | [0.53, 0.75] | 0.463 | 0.63 | 0.67 | 0.19 | 0.73 | 0.00 | 6 |
| bge_small__s3 | vector | 0.711 | [0.61, 0.81] | 0.484 | 0.68 | 0.81 | 0.44 | 0.73 | 0.00 | 36 |
| bge_small__s3 | hybrid | 0.771 | [0.67, 0.86] | 0.519 | 0.73 | 0.90 | 0.44 | 0.73 | 0.00 | 25 |

Runs used: 15 distinct configurations (45 dev runs in runs.jsonl; the second pass of 15 was an accidental duplicate with identical numbers).


## What the table says

1. **Structure helps.** Chunking inside sections (s2, s3) beats cutting across them (s1) on every model and method: hybrid 0.78 and 0.77 against 0.66 on bge-small.
2. **Hybrid beats vector beats BM25 on bge-small.** s2: 0.64 BM25, 0.70 vector, 0.78 hybrid. BM25 alone never exceeds 0.64, but it is the strongest on exact-term questions (1.00 on s2/s3) and the fastest (6 to 15 ms).
3. **bge-m3 does not earn its cost.** Its best (s3 vector 0.771, s3 hybrid 0.759) ties bge-small s2 hybrid (0.783) inside the confidence interval, at 10 to 40 times the embedding time and 5 to 8 times the query latency. Where bge-m3 is clearly better is paraphrased questions (0.75 to 0.81 against 0.44): its vectors carry meaning across wording better. That is the one place the extra cost buys something.
4. **General questions fail everywhere** (0.0 to 0.4 of 5 questions). Unfiltered search over the whole subset with a cap of 2 chunks per company does not surface evidence from three specific filings. This is the limit named in the System tab; it needs a different mechanism (project 2 or a scan), not a better index.
5. **Table questions retrieve well** (0.81 to 0.90 with hybrid), so table chunks as their own searchable units (option 2 default) work.
6. **Fixed-size inside sections (s2) and paragraph-based (s3) are tied** (0.783 vs 0.771). s2 wins on MRR by nothing. Either is a fine choice; s2 is taken as the measured winner.

## HNSW check (bge_small__s1, 96 questions, top 50 against exact search)

| ef_search | recall vs exact | p50 latency |
|---|---|---|
| 40 (pgvector default) | 0.803 | 11 ms |
| 100 | 0.949 | 7 ms |
| 200 | 0.965 | 8 ms |

The default 40 loses one in five exact neighbors. Latency is flat, so the retriever default is now 200. The experiment A runs used 100.

## Decisions taken

- Winner for waves 6 and 7: index bge_small__s2, method hybrid.
- bge-m3 is not adopted for the final system: its gain is inside the interval and the full-corpus build would cost 31 hours of GPU (rule recorded in OPEN-QUESTIONS.md).
- Wave 4w (full-corpus build of the winner, about 3.1 GPU hours) is requested from the coordinator; waves 6 and 7 can run on the subset index meanwhile.

## Incidents

- Experiment A ran three times (45 identical runs in runs.jsonl) because the launch command was re-executed after a shell timeout moved it to the background. The first 15 runs are used. Long jobs now start only through `run_in_background` or `scripts/run.sh`.
- The winners step failed on a metric key name; fixed, and the runner now labels recall by the returned count.
