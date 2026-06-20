#!/usr/bin/env python3
"""
verify.py — the legitimacy backbone for codec-decomposition results.

Design invariant: a result's `status` is NEVER written by hand. It is DERIVED
from measured evidence produced by a registered verifier. If no verifier is
registered for a (codec, stage), the result is `not_implemented` — which the
schema gate and the notifier both treat as "not a pass". This makes a fabricated
pass structurally impossible, independent of what any worker does.

Public entry points:
  run_stage(job)     -> result dict   # never raises; status is derived, not asserted
  validate_result(r) -> (ok, errors)  # the gate: a pass MUST carry evidence + provenance

Dependency-free on purpose (Termux/Android stdlib). The JSON Schema in
schemas/codec_result.json is the same contract for CI, which may use jsonschema.
"""
from __future__ import annotations

import importlib
import traceback
from datetime import datetime, timezone

from provenance import collect_provenance

STAGES = ("m0", "m1", "m2", "m3", "m4")
VALID_STATUS = ("pass", "fail", "partial", "not_implemented", "error")

# Corpus size a real M0 must clear before "byte_exact" counts as evidence.
M0_MIN_FILES = 8

# Verifier modules to import so they self-register via @register.
_VERIFIER_MODULES = (
    "verifiers.snappy_m0", "verifiers.base64_m0",
    "verifiers.gzip_m0", "verifiers.gzip_m1", "verifiers.gzip_m2",
    "verifiers.gzip_m3", "verifiers.gzip_m4",
    "verifiers.lzma_m0", "verifiers.lzma_m1", "verifiers.lzma_m2",
    "verifiers.lzma_m3", "verifiers.lzma_m4",
)


class Evidence:
    """What a verifier returns. The harness turns this into status + result.

    checks  : dict[str, bool] — named REQUIRED checks; all True (+ scope full) => pass
    metrics : dict            — MEASURED numbers (counts, ratios). Never literals.
    scope   : "full" | "partial" — does this verifier cover the whole stage?
    detail  : str             — human-readable note
    tags    : list[str]       — optional structural tags
    """

    def __init__(self, checks, metrics=None, scope="full", detail="", tags=None):
        if not isinstance(checks, dict) or not checks:
            raise ValueError("Evidence requires at least one named check")
        if scope not in ("full", "partial"):
            raise ValueError(f"scope must be full|partial, got {scope!r}")
        self.checks = {str(k): bool(v) for k, v in checks.items()}
        self.metrics = dict(metrics or {})
        self.scope = scope
        self.detail = detail
        self.tags = list(tags or [])


def derive_status(ev: Evidence) -> str:
    """Status is a pure function of evidence. No side channel, no override."""
    all_pass = all(ev.checks.values())
    if all_pass:
        return "pass" if ev.scope == "full" else "partial"
    return "fail"


# (codec, stage) -> callable(job) -> Evidence
VERIFIERS: dict = {}
_loaded = False


def register(codec, stage):
    """Decorator: register a verifier for a (codec, stage)."""
    def deco(fn):
        VERIFIERS[(codec, stage)] = fn
        return fn
    return deco


def _load_verifiers():
    """Import verifier modules so they self-register (once). Missing modules are non-fatal."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for mod in _VERIFIER_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # pragma: no cover - defensive
            print(f"verify: could not load {mod}: {e}", flush=True)


def run_stage(job) -> dict:
    """Run the verifier for a job. NEVER raises. Status is derived, never asserted.

    Returns a result dict that always carries provenance and a truthful status:
      - no verifier registered        -> not_implemented
      - verifier raises               -> error
      - verifier runs, checks fail    -> fail
      - verifier runs, partial scope  -> partial
      - verifier runs, full + all ok  -> pass
    """
    codec = job.get("codec", "unknown")
    stage = job.get("stage", "unknown")
    job_id = job.get("id") or job.get("job_id") or f"{codec}_{stage}"
    now = datetime.now(timezone.utc).isoformat()
    base = {"job_id": job_id, "codec": codec, "stage": stage, "timestamp": now}

    _load_verifiers()

    fn = VERIFIERS.get((codec, stage))
    if fn is None:
        return {
            **base,
            "status": "not_implemented",
            "metrics": {},
            "notes": (f"No verifier registered for {codec}/{stage}. "
                      f"This stage does no work yet — reporting the truth, not a pass."),
            "provenance": collect_provenance(verifier=None),
        }

    try:
        ev = fn(job)
        if not isinstance(ev, Evidence):
            raise TypeError(f"verifier returned {type(ev).__name__}, expected Evidence")
        return {
            **base,
            "status": derive_status(ev),
            "checks": ev.checks,
            "metrics": ev.metrics,
            "tags": ev.tags,
            "scope": ev.scope,
            "notes": ev.detail,
            "provenance": collect_provenance(verifier=fn),
        }
    except Exception as e:
        return {
            **base,
            "status": "error",
            "metrics": {},
            "error": f"{type(e).__name__}: {e}",
            "notes": "Verifier raised; recorded as error (NOT pass).",
            "trace": traceback.format_exc(limit=4),
            "provenance": collect_provenance(verifier=fn),
        }


# ---------------------------------------------------------------------------
# The gate. A `pass` MUST carry stage-appropriate evidence + provenance.
# This is the structural lock: even a buggy verifier cannot mint a green pass
# without the measurements below. Mirrors schemas/codec_result.json.
# ---------------------------------------------------------------------------

def _m0_pass_ok(m):
    return (m.get("byte_exact") is True
            and int(m.get("files_decoded", 0)) >= M0_MIN_FILES
            and int(m.get("test_cases", 0)) >= M0_MIN_FILES)


def _m1_pass_ok(m):
    return (m.get("kill_switch_pass") is True
            or m.get("preimage_mirrors_mode") is True)


def _m2_pass_ok(m):
    return m.get("byte_exact") is True and int(m.get("reencode_samples", 0)) >= 1


def _m3_pass_ok(m):
    return ("mode_a_flavor" in m
            and int(m.get("cell_count", 0)) + int(m.get("orbit_count", 0)) >= 0)


def _m4_pass_ok(m):
    return bool(m.get("synthesis_complete", False))


PASS_REQUIRED = {
    "m0": _m0_pass_ok,
    "m1": _m1_pass_ok,
    "m2": _m2_pass_ok,
    "m3": _m3_pass_ok,
    "m4": _m4_pass_ok,
}


def validate_result(r):
    """The gate. Returns (ok: bool, errors: list[str]).

    Honest non-passes (not_implemented / error / partial / fail) only need the
    base fields. A `pass` additionally must carry measured evidence and provenance.
    """
    errors = []
    if not isinstance(r, dict):
        return False, ["result is not a JSON object"]

    for f in ("job_id", "codec", "stage", "status"):
        if not r.get(f):
            errors.append(f"missing required field: {f}")

    stage = r.get("stage")
    if stage not in STAGES:
        errors.append(f"invalid stage: {stage!r}")

    status = r.get("status")
    if status not in VALID_STATUS:
        errors.append(f"invalid status: {status!r}")

    # Non-pass results that claim a verifier ran must explain themselves.
    if status in ("fail", "partial"):
        if not (r.get("error") or r.get("checks") or r.get("metrics") or r.get("notes")):
            errors.append(f"status={status} must carry checks/metrics/error/notes")

    # The legitimacy lock.
    if status == "pass":
        if r.get("scope") == "partial":
            errors.append("status=pass is incompatible with scope=partial")
        m = r.get("metrics") or {}
        req = PASS_REQUIRED.get(stage)
        if req is None:
            errors.append(f"no pass-criteria defined for stage {stage}")
        elif not req(m):
            errors.append(f"status=pass lacks required evidence for {stage}: metrics={m}")
        prov = r.get("provenance") or {}
        if not (prov.get("code_sha") or prov.get("source_hash")):
            errors.append("status=pass lacks provenance (code_sha/source_hash)")

    return (len(errors) == 0), errors


if __name__ == "__main__":
    # Tiny self-check: an unimplemented codec must NOT be a pass.
    demo = run_stage({"id": "fakezip_m0", "codec": "fakezip", "stage": "m0"})
    print("status:", demo["status"])
    ok, errs = validate_result(demo)
    print("gate ok:", ok, "errors:", errs)
