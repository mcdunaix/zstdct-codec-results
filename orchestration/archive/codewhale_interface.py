#!/usr/bin/env python3
"""
Codewhale Interface

Codewhale calls this to:
  1. Get the next job to work on
  2. Submit results back to the queue

Usage (from codewhale/deepseek):
  python orchestration/codewhale_interface.py --get-job
  python orchestration/codewhale_interface.py --submit-job <job_id> --result-file <path>
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
import time

ORCH_ROOT = Path(__file__).parent
JOBS_DIR = ORCH_ROOT / "jobs"
RESULTS_DIR = ORCH_ROOT / "results" / "codec_library"


class Job:
    """Minimal job representation."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            return cls(**json.load(f))


def list_jobs(state_dir):
    """Read jobs from a state directory."""
    jobs = []
    for f in sorted(state_dir.glob("*.json")):
        try:
            jobs.append(Job.from_file(f))
        except Exception as e:
            print(f"Warning: {f.name}: {e}", file=sys.stderr, flush=True)
    return jobs


def reclaim_abandoned(timeout_hours=24):
    """Move stale jobs back to pending."""
    now = time.time()
    reclaimed = []
    for state_dir in [JOBS_DIR / "assigned", JOBS_DIR / "running"]:
        for f in state_dir.glob("*.json"):
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours > timeout_hours:
                dst = JOBS_DIR / "pending" / f.name
                f.rename(dst)
                reclaimed.append(f.stem)
    return reclaimed


def get_job():
    """Return the next pending job as JSON to stdout."""
    # Reclaim stale jobs first
    reclaim_abandoned(timeout_hours=24)

    # Get top pending job (sorted by priority)
    pending = list_jobs(JOBS_DIR / "pending")
    if not pending:
        print(json.dumps({"error": "no_pending_jobs"}), flush=True)
        sys.exit(1)

    # Sort by priority (descending), then by id
    sorted_pending = sorted(pending, key=lambda j: (-getattr(j, "priority", 0), getattr(j, "id", "")))
    job = sorted_pending[0]

    # Move to assigned
    src = JOBS_DIR / "pending" / f"{job.id}.json"
    dst = JOBS_DIR / "assigned" / f"{job.id}.json"
    src.rename(dst)

    # Return job as JSON to stdout
    job_data = {k: v for k, v in job.__dict__.items() if not k.startswith("_")}
    print(json.dumps(job_data), flush=True)
    sys.exit(0)


def submit_job(job_id, result_file):
    """
    Accept job results from codewhale and move to codec_library.
    Result file should be JSON with status, metrics, notes, etc.
    """
    result_path = Path(result_file)
    if not result_path.exists():
        print(f"Error: Result file not found: {result_file}", file=sys.stderr, flush=True)
        sys.exit(1)

    try:
        with open(result_path) as f:
            result_data = json.load(f)
    except Exception as e:
        print(f"Error: Failed to load result: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    # Find the job in assigned/running
    job_file = None
    for state_dir in [JOBS_DIR / "assigned", JOBS_DIR / "running"]:
        candidate = state_dir / f"{job_id}.json"
        if candidate.exists():
            job_file = candidate
            break

    if not job_file:
        print(f"Error: Job not found in assigned/running: {job_id}", file=sys.stderr, flush=True)
        sys.exit(1)

    # Read job metadata
    try:
        job = Job.from_file(job_file)
    except Exception as e:
        print(f"Error: Failed to load job: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    # Copy result to codec_library
    codec_name = getattr(job, "codec", "unknown")
    stage_name = getattr(job, "stage", "unknown")
    codec_dir = RESULTS_DIR / codec_name
    codec_dir.mkdir(parents=True, exist_ok=True)

    result_dest = codec_dir / f"{stage_name}.json"
    with open(result_dest, "w") as f:
        json.dump(result_data, f, indent=2)

    # Move job to completed
    completed_file = JOBS_DIR / "completed" / f"{job_id}.json"
    status = result_data.get("status", "unknown")
    job_data = job.__dict__.copy()
    job_data["status"] = status
    job_data["completed_at"] = datetime.now().isoformat()

    with open(completed_file, "w") as f:
        json.dump(job_data, f, indent=2)

    job_file.unlink()

    print(json.dumps({
        "status": "ok",
        "job_id": job_id,
        "result_file": str(result_dest)
    }), flush=True)
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Codewhale Interface")
    parser.add_argument("--get-job", action="store_true",
                        help="Get next pending job")
    parser.add_argument("--submit-job", type=str,
                        help="Submit results for job")
    parser.add_argument("--result-file", type=str,
                        help="Path to result JSON file")

    args = parser.parse_args()

    if args.get_job:
        get_job()
    elif args.submit_job and args.result_file:
        submit_job(args.submit_job, args.result_file)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
