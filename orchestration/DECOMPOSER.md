# Decomposer-inside-the-loop

Built 2026-06-20 per `REBUILD_SPEC.md`. Closes the gap named that day: the old
system only *ran pre-built verifiers* (decomposition was hand-done outside the
loop). Now the worker **generates the decoder itself, inside the loop**, and only
a real byte-exact match against the reference codec can mint a `pass`.

## Generation seam: a CLI agent (hermes / codewhale), not an API client

Generation is delegated to a DeepSeek-backed coding-agent CLI that holds its OWN
key. The decomposer auto-detects which is on PATH and uses the right invocation:
- **hermes** (the edge/tablet agent): `hermes -z "<prompt>" --yolo --accept-hooks`
- **codewhale**: `codewhale exec --auto "<prompt>"`

hermes is preferred (it's what's installed on the tablet; codewhale isn't). Pin
with `GEN_AGENT=hermes|codewhale`, force a model with `GEN_AGENT_MODEL`. **The
orchestration holds no API key and embeds no HTTP client** — it just shells out.

## The legitimacy split (why a machine author is safe)

The machine writes the **mechanism** only; **trusted code measures it**:

- `oracle.py` — OURS. Reference compress/decompress for the stdlib codecs
  (gzip/zlib/bz2/lzma), a fixed corpus, and the byte-exact M0/M2 probes. The
  pass/fail numbers come from here, never from anything the generated module
  asserts about itself.
- Anti-delegation — a "decoder" that just calls `bz2.decompress` would round-trip
  while decoding nothing. So a generated decoder **may not import the reference
  codec** (AST static guard) and is run with that module **poisoned at runtime**.
  It must be an independent implementation.
- `generated.py` — OURS. Loads the generated module, runs the oracle probe, and
  emits the `Evidence` the existing gate (`verify.py`) reads. Registered per
  `(codec, stage)` so `run_stage` finds it.

Net: `verify.validate_result` is still the sole authority; a fabricated pass is
structurally impossible even though a machine wrote the decoder.

## Flow (per job, inside `work_on_job`)

1. verifier already registered? → run it (unchanged path).
2. else, hard-gated stage (`m0`/`m2`) with a reference oracle? → `decomposer.decompose`:
   `codewhale exec --auto` → validate byte-exact → retry up to K → register.
3. else (`m1`/`m3`/`m4`, or no oracle, or no CLI) → fall through to `run_stage`'s
   honest `not_implemented`. **Never a fabricated pass.**

Only M0/M2 have a hard byte-exact oracle, so only those are generated. M1/M3/M4
are reported honestly rather than letting a machine assert insight it can't prove.

## Budgets / safety

`decomposer.py`, env-overridable: `DECOMP_MAX_RETRIES` (3), `DECOMP_ATTEMPT_TIMEOUT_S`
(900s per call), `DECOMP_CODEC_BUDGET_S` (3600s runaway kill switch),
`GEN_AGENT` (pin hermes/codewhale), `GEN_AGENT_MODEL`, `DECOMP_DRY_RUN` (skip the
agent call — exercises control flow offline). Plus a **trusted-file tamper guard**:
oracle/verify/generated/provenance are snapshotted around the agent call and
restored if the agent edits them (the codegen agent must never weaken the gate).

## Run it

```sh
python seed_queue.py bz2            # queue bz2 m0 (the proving job)
python codewhale_autonomous.py      # pull -> generate-in-loop -> gate -> commit -> notify
```

Verify without spending tokens: `DECOMP_DRY_RUN=1`, plus `python oracle.py`,
`python decomposer.py`, and `pytest tests/test_decomposer.py`.

## Definition of done

Queue stdlib codecs → walk away → return to gated, byte-exact-validated results
**authored by the tablet**, with honest failures where the agent couldn't crack a
stage, and zero fabricated passes. Independently checkable: the repo commits +
you operating the CLI yourself.
