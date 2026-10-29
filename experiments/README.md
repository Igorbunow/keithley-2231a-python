# Experimental drafts / Экспериментальные черновики

**NOT production. NOT part of `keithley2231a`. NOT qualified for hardware.**

This repository directory is a quarantine for speculative algorithms and
one-off research. It is intentionally **outside `src/`**, excluded from the
installed Python distribution (`pyproject.toml`: `where = ["src"]`).

## Rules

- Never import these modules from `src/keithley2231a/` or the normal hardware
  qualification runner. No automatic promotion into production code.
- No instrument transports, real endpoints, SCPI, or physical output control.
- `experiments/o02_dual_psu/dual_offline.py` is **O02-A**, a synthetic
  state-machine/fault-injection sketch only. Its `PASS` is simulation-only.
- Future independent bench-orchestration projects may **review and adapt**
  ideas, or delete them outright. No compatibility guarantee.
- R4 / R5 / R6 work on the **single Keithley** must not depend on this folder.

Only the explicit offline demo `tools/o02_offline.py` and dedicated
`tests/offline/test_o02_offline_pair.py` import the O02 draft.
