# Wave 4 report: chunking, storage, embedding (in progress)

## Chunking (full corpus, 1,194 passing filings)
| Index | Chunks | Tokens |
|---|---|---|
| bge_small__s1 / bge_m3__s1 | 488,362 | 177.7M |
| bge_small__s2 / bge_m3__s2 | 607,098 | (see eta.txt) |
| bge_small__s3 / bge_m3__s3 | 623,530 | |
| bge_m3__s4 | 486,328 | |

About twice the plan's estimate (250k chunks, 120M tokens per index). Chunk files: 1.7 GB each.

## Measured GPU (MPS) embedding speed and full-corpus ETA (1% samples, 2026-09-27)
| Index | tokens/s | ETA, full corpus |
|---|---|---|
| bge_small__s1 | 30,946 | 1.6 h |
| bge_small__s2 | 15,514 | 3.1 h |
| bge_small__s3 | 17,821 | 2.6 h |
| bge_m3__s1 | 1,958 | 25.2 h |
| bge_m3__s2 | 1,523 | 31.3 h |
| bge_m3__s3 | 1,392 | 33.1 h |
| bge_m3__s4 | 761 | 60.5 h |

bge-m3 is 10 to 40 times slower than bge-small on this GPU, and whole-section chunks (s4) are the slowest because attention cost grows with sequence length.

## Decision (default from OPEN-QUESTIONS, adjusted to the measured numbers)
The 7-index comparison cannot run on the full corpus (about 150 GPU hours). It runs on a 150-filing subset:
- the subset holds all 47 filings the dev and test golden sets point to, plus a seeded random fill to 150, passing filings only;
- indexes: bge_small s1, s2, s3 and bge_m3 s1, s3, s4 (bge_m3 s2 dropped: the s2-versus-s3 question is answered on bge-small, and bge-m3 s3 is the plan's expected winner);
- estimated GPU time about 16 h (about 0.9 h bge-small, 15 h bge-m3), resumable per shard;
- after wave 5 picks the winner, only that index is built over the full 1,194 filings (wave 4w).

## Incidents
- First run (14:48) lost 2.5 h to a battery sleep, then swapped: the ETA step loaded 1.7 GB chunk files into memory. Fixed by streaming reads (commit 650e0c3).
- Second run re-chunked everything; fixed by reusing existing chunk files and linking identical s1 to s3 files across models.
