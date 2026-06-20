#!/usr/bin/env python3
"""
Autonomous Codewhale loop for Android / mesh device.

Runs in an infinite loop:
  1. Pull latest from GitHub
  2. Check for pending jobs
  3. Work on next job (M0-M4)
  4. Commit + push results
  5. Send Telegram notification
  6. Repeat

Usage:
  python codewhale_autonomous.py

Or run in background (on Android):
  nohup python codewhale_autonomous.py > codewhale.log 2>&1 &
"""

import time
import sys
from datetime import datetime, timezone

from git_queue import (
    pull_latest, list_pending_jobs, submit_job_result,
    get_codec_pass_progress, is_codec_complete, REPO_ROOT
)
from notifications import (
    notify_job_start, notify_job_complete, notify_codec_complete,
    notify_error,
)
from verify import run_stage, has_verifier
from decomposer import decompose, agent_available, agent_name, GENERATABLE
from generated import register_generated
import oracle

# Assume spoon_feed module is available (in src/zstdct/)
sys.path.insert(0, str(REPO_ROOT.parent / "src"))


def maybe_generate(job):
    """Decomposer-inside-the-loop: if no verifier exists for a hard-gated
    (codec, stage), have Codewhale GENERATE the decoder, validate it byte-exact
    against the trusted oracle, and register the trusted verifier. Returns a
    short note for the log. Never raises and NEVER fabricates a pass — if
    generation fails or the stage has no hard oracle, the stage falls through to
    run_stage's honest not_implemented/partial."""
    codec, stage = job["codec"], job["stage"]
    if has_verifier(codec, stage):
        return "verifier already present (skip generation)"
    if stage not in GENERATABLE or not oracle.has_oracle(codec):
        return f"{stage}: no hard byte-exact oracle — will report honestly"
    if not agent_available():
        return "no generation agent (hermes/codewhale) on PATH — will report not_implemented"

    print(f"  No verifier for {codec}/{stage}; generating in-loop via {agent_name()}...", flush=True)
    outcome = decompose(job, log=lambda m: print(m, flush=True))
    if outcome.get("generated"):
        register_generated(codec, stage)
        v = outcome.get("validation", {})
        passed = v.get("files_decoded", v.get("reencode_samples"))
        return (f"GENERATED + byte-exact ({passed}/{v.get('test_cases')}) "
                f"in {outcome.get('attempts')} attempt(s)")
    return f"generation failed: {outcome.get('reason')} — {str(outcome.get('detail',''))[:120]}"


def work_on_job(job):
    """Run the verifier for this job, generating it in-loop first if missing.
    Status is DERIVED from measured evidence inside verify.run_stage — never
    hardcoded here. run_stage never raises; worst case is an honest 'error'."""
    job_id = job["id"]
    codec = job["codec"]
    stage = job["stage"]

    notify_job_start(job_id, codec, stage)
    print(f"\n{'='*60}", flush=True)
    print(f"Working on: {job_id}  (codec={codec}, stage={stage})", flush=True)
    print(f"Spec: {job.get('spec_url', '')}", flush=True)
    print(f"{'='*60}", flush=True)

    note = maybe_generate(job)          # may invoke Codewhale; never fabricates
    print(f"  decompose: {note}", flush=True)

    result = run_stage(job)             # authoritative, gated, provenance-stamped
    print(f"  -> status={result['status']}  metrics={result.get('metrics', {})}", flush=True)
    return result


def preflight():
    """Report readiness for in-loop generation. The orchestration holds NO API
    key — Codewhale owns its DeepSeek credentials; we only need its CLI present.
    Missing CLI is non-fatal: stages needing generation report not_implemented."""
    ok = agent_available()
    print(f"Preflight: generation agent {agent_name().upper() if ok else 'MISSING'} "
          f"(generation {'enabled' if ok else 'DISABLED — stages will report not_implemented'})",
          flush=True)
    if not ok:
        print("  -> install/login hermes (or codewhale); generation needs one on PATH.", flush=True)
    print(f"  oracle codecs: {', '.join(sorted(oracle.REFERENCE))}  | "
          f"auto-gen stages: {', '.join(GENERATABLE)}", flush=True)
    return ok


def main():
    """Main loop."""
    print("Codewhale autonomous started", flush=True)
    print(f"Repo: {REPO_ROOT}", flush=True)
    preflight()

    idle_cycles = 0
    max_idle = 12          # ~12 min of empty queue before a quiet status line
    push_fail_streak = 0   # backoff when GitHub is unreachable

    while True:
        try:
            print(f"\n[{datetime.now(timezone.utc).isoformat()}] Pulling...", flush=True)
            pull_latest()

            pending = list_pending_jobs()
            print(f"Pending jobs: {len(pending)}", flush=True)

            if not pending:
                idle_cycles += 1
                if idle_cycles >= max_idle:
                    print("No jobs. Idle.", flush=True)
                    idle_cycles = 0
                time.sleep(60)
                continue

            idle_cycles = 0
            job = pending[0]
            job_id, codec, stage = job["id"], job["codec"], job["stage"]

            result = work_on_job(job)  # never raises; status is evidence-derived
            print(f"Submitting {job_id}...", flush=True)
            outcome = submit_job_result(job_id, codec, stage, result)

            if outcome["ok"]:
                notify_job_complete(job_id, codec, stage,
                                    result.get("status"), result.get("metrics"))
                if is_codec_complete(codec):
                    passed, total = get_codec_pass_progress(codec)
                    notify_codec_complete(codec, passed, total)
                    print(f"🎉 {codec.upper()} COMPLETE ({passed}/{total} pass)", flush=True)

                if outcome.get("pushed"):
                    push_fail_streak = 0
                else:
                    # Saved + committed locally but not pushed (GitHub unreachable).
                    # The job is already dequeued, so there's no re-loop — just back
                    # off; a later cycle pushes the backlog.
                    push_fail_streak = min(push_fail_streak + 1, 6)
                    backoff = 30 * push_fail_streak
                    if push_fail_streak == 1:
                        notify_error(job_id, "Saved locally; push to GitHub failed (will retry)")
                    print(f"⚠️ Push failed; backing off {backoff}s "
                          f"(streak={push_fail_streak})", flush=True)
                    time.sleep(backoff)

            elif outcome.get("rejected"):
                # run_stage always emits gate-valid results, so this means a verifier
                # bug. It's already quarantined to failed/; surface it loudly.
                notify_error(job_id, f"GATE REJECTED: {'; '.join(outcome['errors'][:2])}")
                print(f"⛔ {job_id} rejected by gate: {outcome['errors']}", flush=True)

        except KeyboardInterrupt:
            print("\nShutdown requested", flush=True)
            break
        except Exception as e:
            print(f"Unexpected error: {e}", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
