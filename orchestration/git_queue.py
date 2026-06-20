#!/usr/bin/env python3
"""
Git-based job queue (distributed, GitHub-backed).

State lives in a GitHub repo:
  queue/                    ← jobs to do
  results/codec_library/    ← completed results

Device pulls repo, works on jobs, commits results back.
"""

import subprocess
import json
from pathlib import Path
from datetime import datetime, timezone

from verify import validate_result

REPO_ROOT = Path(__file__).parent.parent  # results-repo root on the device
QUEUE_DIR = REPO_ROOT / "queue"
RESULTS_DIR = REPO_ROOT / "results" / "codec_library"
FAILED_DIR = REPO_ROOT / "failed"


def run_git(args):
    """Run a git command, return (success, stdout)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        print(f"Git error: {e}", flush=True)
        return False, str(e)


def pull_latest():
    """Pull latest from origin."""
    success, output = run_git(["pull", "origin", "main"])
    if not success:
        print(f"Pull failed: {output}", flush=True)
    return success


def check_git_credentials():
    """Verify git can authenticate (SSH key or stored creds)."""
    success, output = run_git(["ls-remote", "origin", "HEAD"])
    if not success:
        print(f"Auth check failed: {output}", flush=True)
        print("GitHub auth not configured. Run:", flush=True)
        print("  ssh -T git@github.com  (to test SSH key)", flush=True)
        print("  git remote -v  (to check remote URL)", flush=True)
        return False
    return True


def push_changes(message):
    """Commit all changes and push."""
    # Verify credentials first
    if not check_git_credentials():
        print(f"Skipping push: git credentials not configured", flush=True)
        return False

    # Add all changes
    success, _ = run_git(["add", "-A"])
    if not success:
        return False

    # Commit
    success, output = run_git(["commit", "-m", message])
    if not success:
        print(f"Commit failed: {output}", flush=True)
        return False

    # Push; on failure, reconcile with origin (non-fast-forward) and retry once.
    success, output = run_git(["push", "origin", "main"])
    if not success:
        print(f"Push failed: {output} — reconciling with origin and retrying", flush=True)
        run_git(["pull", "--rebase", "origin", "main"])
        success, output = run_git(["push", "origin", "main"])
    if not success:
        print(f"Push still failing: {output}", flush=True)
        print("Result is committed locally; it will be pushed on a later cycle.", flush=True)
        return False

    print(f"Pushed: {message}", flush=True)
    return True


def list_pending_jobs():
    """List all jobs in queue/ directory."""
    if not QUEUE_DIR.exists():
        return []

    jobs = []
    for job_file in sorted(QUEUE_DIR.glob("*.json")):
        try:
            with open(job_file) as f:
                job = json.load(f)
            jobs.append(job)
        except Exception as e:
            print(f"Failed to load {job_file.name}: {e}", flush=True)

    return jobs


def claim_job(job_id):
    """
    Get a specific job and remove from queue.
    Returns job data or None if not found.
    """
    job_file = QUEUE_DIR / f"{job_id}.json"
    if not job_file.exists():
        return None

    try:
        with open(job_file) as f:
            job = json.load(f)
        return job
    except Exception as e:
        print(f"Failed to load job {job_id}: {e}", flush=True)
        return None


def remove_job_from_queue(job_id):
    """Delete job from queue/ after it's complete."""
    job_file = QUEUE_DIR / f"{job_id}.json"
    if job_file.exists():
        job_file.unlink()
        return True
    return False


def save_result(codec, stage, result_json):
    """
    Save result to results/codec_library/{codec}/{stage}.json.
    Result should be a dict with: status, metrics, notes, etc.
    """
    codec_dir = RESULTS_DIR / codec
    codec_dir.mkdir(parents=True, exist_ok=True)

    result_file = codec_dir / f"{stage}.json"

    # Add metadata
    result_json["saved_at"] = datetime.now().isoformat()

    with open(result_file, "w") as f:
        json.dump(result_json, f, indent=2)

    return result_file


def save_failed(job_id, result_json, errors):
    """Quarantine a result that fails the gate so it never enters the library
    and can't re-loop through the queue."""
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    failed_file = FAILED_DIR / f"{job_id}.json"
    payload = dict(result_json)
    payload["gate_errors"] = errors
    payload["quarantined_at"] = datetime.now(timezone.utc).isoformat()
    with open(failed_file, "w") as f:
        json.dump(payload, f, indent=2)
    return failed_file


def submit_job_result(job_id, codec, stage, result_json):
    """Gate, then save + dequeue + push. Returns a dict:
      {"ok": True,  "pushed": bool, "status": str, "file": str}
      {"ok": False, "rejected": True, "errors": [...]}   # failed the gate

    A result that fails the gate is quarantined to failed/ and removed from the
    queue (so it can't re-loop) — it NEVER enters the codec library.
    """
    ok, errors = validate_result(result_json)
    if not ok:
        print(f"REJECTED {job_id}: {errors}", flush=True)
        failed_file = save_failed(job_id, result_json, errors)
        remove_job_from_queue(job_id)
        push_changes(f"Reject {job_id}: gate failed")
        return {"ok": False, "rejected": True, "errors": errors, "file": str(failed_file)}

    result_file = save_result(codec, stage, result_json)
    print(f"Saved result: {result_file}", flush=True)
    remove_job_from_queue(job_id)
    status = result_json.get("status", "unknown")
    pushed = push_changes(f"Complete {job_id}: {status}")
    return {"ok": True, "pushed": pushed, "status": status, "file": str(result_file)}


def get_codec_progress(codec):
    """
    Return (completed_stages, total_stages) for a codec.
    Check how many of [m0, m1, m2, m3, m4] are done.
    """
    stages = ["m0", "m1", "m2", "m3", "m4"]
    completed = 0

    for stage in stages:
        stage_file = RESULTS_DIR / codec / f"{stage}.json"
        if stage_file.exists():
            completed += 1

    return completed, len(stages)


def get_next_stage(codec):
    """Return the next stage to work on for a codec."""
    stages = ["m0", "m1", "m2", "m3", "m4"]
    completed, _ = get_codec_progress(codec)
    if completed < len(stages):
        return stages[completed]
    return None


def _read_status(codec, stage):
    """Return the recorded status for a stage, or None if absent/unreadable."""
    stage_file = RESULTS_DIR / codec / f"{stage}.json"
    if not stage_file.exists():
        return None
    try:
        with open(stage_file) as f:
            return json.load(f).get("status")
    except Exception:
        return None


def get_codec_pass_progress(codec):
    """(passed_stages, total) — counts status==pass, NOT mere file existence."""
    stages = ["m0", "m1", "m2", "m3", "m4"]
    passed = sum(1 for s in stages if _read_status(codec, s) == "pass")
    return passed, len(stages)


def is_codec_complete(codec):
    """True only if every stage exists AND every stage is a real pass."""
    stages = ["m0", "m1", "m2", "m3", "m4"]
    return all(_read_status(codec, s) == "pass" for s in stages)


if __name__ == "__main__":
    # Quick test
    pull_latest()
    jobs = list_pending_jobs()
    print(f"Pending jobs: {len(jobs)}")
    for j in jobs[:3]:
        print(f"  - {j.get('id')}")
