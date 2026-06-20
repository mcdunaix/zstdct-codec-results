# Archived: file-based state machine (superseded)

These two modules are the **original file-based** orchestration prototype:

- `hermes_orchestrator.py` — pending/assigned/running/completed dirs under `jobs/`,
  claim-by-rename, mtime-based reclaim. Did **no** result validation (counted files only).
- `codewhale_interface.py` — `--get-job` / `--submit-job` CLI over the same `jobs/` layout.

The **live** system is git-based and lives one level up:

- `git_queue.py` — `queue/` + `results/codec_library/`, claim-by-delete, GitHub as the bus.
- `codewhale_autonomous.py` — the autonomous loop.
- `verify.py` — the legitimacy gate (status derived from measured evidence).

The two designs use **different on-disk layouts** (`jobs/{pending,assigned,...}` vs
`queue/` + `results/`) and contradicted each other. They are kept here for reference only.
Do not run them against the results repo — they would create an empty parallel `jobs/`
tree and report a meaningless 0/0/0/0. If a stale-job reclaim or priority queue is ever
wanted, port that logic into `git_queue.py` rather than reviving these.
