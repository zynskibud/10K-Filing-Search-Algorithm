# Wave 2d stopping point (TIMING window)

Stopped at 2026-09-27T03:02:17Z on the coordinator's TIMING order.
- Re-parsed with the fixed parser before the stop: 1333 of 1,333 files (those newer than reports/contracts/wave-2d.md in data/parsed/).
- Parse workers killed. The run is resumable: rerun scripts/parse.sh without --force to skip files already written, after restoring the files older than the contract with --force on those only, or rerun everything with --force (about the same cost).
- The wave 2d subagent was stopped; its code changes in citation_rag/parse/ are on disk, uncommitted, and get committed when the wave finishes after RESUME.
