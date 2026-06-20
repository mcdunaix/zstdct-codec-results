# Legitimacy layer — how a result earns its status

**Problem this fixes (audit, 2026-06-19):** the worker hardcoded `status: "pass"`
for every job. 13/13 pushed results were fabricated passes; no byte of codec data
was ever decoded. Telegram + "codec complete" reported file existence, not work.

**Fix in one sentence:** a result's status is now DERIVED from measured evidence by
a verifier, the worker can never write `status` itself, and a `pass` that lacks
evidence + provenance is rejected before it can be pushed — and re-checked again in CI.

## The chain now

```
job → verify.run_stage(job) → Evidence (measured) → derive_status() → result
    → git_queue.submit_job_result() → validate_result() GATE
         ├─ valid    → results/codec_library/   → push → Telegram (truthful emoji)
         └─ invalid  → failed/ (quarantine)      → push → ⚠️ alert     [never the library]
    → GitHub Action check_results.py → re-validates ALL results (independent judge)
```

Every arrow that used to preserve a fake status now either measures it or blocks it.

## Status semantics (only `pass` is green)

| status | meaning | emoji |
|---|---|---|
| `pass` | full-scope verifier, all checks true, evidence meets thresholds | ✅ |
| `partial` | real verifier ran but covers only part of the stage (e.g. snappy: uncompressed chunks only) | 🟡 |
| `fail` | verifier ran, a required check failed | ❌ |
| `not_implemented` | **no verifier registered** — the honest default; does no work | ⚪ |
| `error` | verifier raised | 🔴 |

A codec is "COMPLETE" only when all 5 stages are `pass` — counted, not file-existence.

## The gate (`verify.validate_result`)

Runs **on-device before push** and **in CI on every push**. A `pass` must carry:
- stage evidence (m0: `byte_exact=true`, `files_decoded ≥ 8`, `test_cases ≥ 8`; see `PASS_REQUIRED`)
- a `provenance` block with `code_sha` or `source_hash`

`not_implemented` / `error` / `partial` / `fail` only need the base fields — they are
honest non-passes and pass the gate freely. Dependency-free (stdlib) so it runs on Termux.

## Adding a real verifier

1. Create `verifiers/<codec>_<stage>.py`.
2. Implement the real check — decode a corpus of reference-library files, assert byte-exact,
   measure counts. Return an `Evidence(checks=..., metrics=..., scope=..., detail=...)`.
   **Measure every number; never write a literal.** Use `scope="partial"` if coverage is
   incomplete (it will report `partial`, never `pass`).
3. Register it: `@register("<codec>", "<stage>")`, and add the module to
   `verify._VERIFIER_MODULES`.
4. Add a test case. Run `python3 orchestration/tests/test_verify.py`.

`verifiers/snappy_m0.py` is the worked example (honest `partial`).

## Provenance

Every result carries `provenance`: orchestration `code_sha` (+ dirty flag), device id,
verifier name, source hash, UTC timestamp. A result is reproducible and auditable.

## Tests

```
python3 orchestration/tests/test_verify.py   # backbone: default-deny, gate, real pass, snappy
python3 orchestration/tests/test_queue.py    # submit gate + quarantine + completion
```

## Deploy (into the results repo) — separate, gated step

1. Copy the patched `orchestration/` into the `zstdct-codec-results` repo (replacing the old).
2. Copy `orchestration/ci/verify-results.yml` → results-repo `.github/workflows/verify-results.yml`.
3. Remove the 13 fabricated passes: either clear `results/codec_library/` or re-queue all jobs
   so the worker overwrites them with honest `not_implemented` / `partial`.
4. Commit + push. CI will then gate every future result.
5. On the device: `git pull` and restart `codewhale_autonomous.py`.

After deploy, the feed shows the truth: mostly ⚪ `not_implemented` until real verifiers land,
🟡 `partial` for snappy m0, and ✅ only where a verifier actually proved byte-exact work.
