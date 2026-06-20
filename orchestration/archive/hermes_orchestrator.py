#!/usr/bin/env python3
"""
Hermes: Codec Decomposition Orchestrator

Stateless, invocation-based orchestration (safe for long-running agents).
Each call is independent — all state is on disk. No context accumulation.

Usage:
  hermes_orchestrator.py --generate-jobs       # Create job queue from job_queue.yaml
  hermes_orchestrator.py --poll                # Single poll cycle (run every 5 min from cron)
  hermes_orchestrator.py --status              # Query current state
  hermes_orchestrator.py --reclaim             # Reclaim stale jobs manually
"""

import argparse
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
import yaml

# Setup
ORCH_ROOT = Path(__file__).parent
JOBS_DIR = ORCH_ROOT / "jobs"
RESULTS_DIR = ORCH_ROOT / "results" / "codec_library"
CHECKPOINTS_DIR = ORCH_ROOT / "checkpoints"
QUEUE_FILE = ORCH_ROOT / "job_queue.yaml"

# Ensure directories exist
for d in [JOBS_DIR / "pending", JOBS_DIR / "assigned", JOBS_DIR / "running", JOBS_DIR / "completed", RESULTS_DIR, CHECKPOINTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


@dataclass
class Job:
    id: str
    codec: str
    stage: str
    priority: int
    spec_url: str
    notes: str = ""

    def to_json(self):
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


# Stateless functions (no accumulating state)

def list_jobs(state_dir):
    """Read jobs from disk (pending, assigned, running, or completed)."""
    jobs = []
    for f in sorted(state_dir.glob("*.json")):
        try:
            jobs.append(Job.from_file(f))
        except Exception as e:
            print(f"Warning: Failed to load {f.name}: {e}", flush=True)
    return jobs


def reclaim_abandoned(timeout_hours=24):
    """Move stale assigned/running jobs back to pending."""
    import time
    now = time.time()
    reclaimed = []

    for state_dir in [JOBS_DIR / "assigned", JOBS_DIR / "running"]:
        for f in state_dir.glob("*.json"):
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours > timeout_hours:
                dst = JOBS_DIR / "pending" / f.name
                f.rename(dst)
                reclaimed.append(f.stem)
                print(f"Reclaimed: {f.stem} (age={age_hours:.1f}h)", flush=True)

    return reclaimed


def generate_jobs_from_config():
    """Read job_queue.yaml and create job files in pending/."""
    if not QUEUE_FILE.exists():
        print(f"Error: Queue config not found: {QUEUE_FILE}", flush=True)
        return

    with open(QUEUE_FILE) as f:
        config = yaml.safe_load(f)

    if not config or "codecs" not in config:
        print("Error: Invalid queue config format", flush=True)
        return

    count = 0
    for codec_cfg in config["codecs"]:
        codec_name = codec_cfg["name"]
        stages = codec_cfg.get("stages", ["m0", "m1", "m2", "m3", "m4"])
        priority = codec_cfg.get("priority", 1)
        spec_url = codec_cfg.get("spec_url", "")

        for i, stage in enumerate(stages):
            job_id = f"{codec_name}_{stage}"
            stage_names = ["M0 (decoder)", "M1 (kill-switch)", "M2 (direction 1)", "M3 (direction 2)", "M4 (synthesis)"]
            job = Job(
                id=job_id,
                codec=codec_name,
                stage=stage,
                priority=priority,
                spec_url=spec_url,
                notes=f"{stage.upper()}: spoon-feed stage {stage_names[i]}"
            )
            job_file = JOBS_DIR / "pending" / f"{job_id}.json"
            with open(job_file, "w") as f:
                f.write(job.to_json())
            count += 1
            print(f"Generated: {job_id}", flush=True)

    print(f"Total: {count} jobs", flush=True)


def poll_cycle():
    """
    Single poll invocation (stateless, context-safe).
    Run every 5 minutes from cron: */5 * * * * python orchestration/hermes_orchestrator.py --poll
    """
    # Reclaim abandoned
    reclaimed = reclaim_abandoned(timeout_hours=24)

    # Count current state
    pending = list_jobs(JOBS_DIR / "pending")
    assigned = list_jobs(JOBS_DIR / "assigned")
    running = list_jobs(JOBS_DIR / "running")
    completed = list_jobs(JOBS_DIR / "completed")

    # Write checkpoint (audit trail, no hallucination risk)
    checkpoint = {
        "timestamp": datetime.now().isoformat(),
        "pending_count": len(pending),
        "assigned_count": len(assigned),
        "running_count": len(running),
        "completed_count": len(completed),
        "reclaimed_count": len(reclaimed),
        "reclaimed": reclaimed
    }

    # Use ISO timestamp in filename (sortable)
    ts = datetime.now().isoformat().replace(":", "-").replace(".", "_")
    checkpoint_file = CHECKPOINTS_DIR / f"{ts}_poll.json"
    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint, f, indent=2)

    print(json.dumps(checkpoint), flush=True)
    return checkpoint


def print_status():
    """Print current queue status (read-only query)."""
    pending = list_jobs(JOBS_DIR / "pending")
    assigned = list_jobs(JOBS_DIR / "assigned")
    running = list_jobs(JOBS_DIR / "running")
    completed = list_jobs(JOBS_DIR / "completed")

    print("\n=== Job Queue Status ===")
    print(f"Pending:   {len(pending)}")
    print(f"Assigned:  {len(assigned)}")
    print(f"Running:   {len(running)}")
    print(f"Completed: {len(completed)}")

    if pending:
        print("\nTop 5 Pending (by priority):")
        sorted_pending = sorted(pending, key=lambda j: (-j.priority, j.id))
        for j in sorted_pending[:5]:
            print(f"  {j.id:30s} (priority={j.priority})")

    if assigned:
        print("\nAssigned:")
        for j in assigned:
            print(f"  {j.id:30s}")

    if running:
        print("\nRunning:")
        for j in running:
            print(f"  {j.id:30s}")

    if completed:
        print("\nLast 5 Completed:")
        for j in completed[-5:]:
            print(f"  {j.id:30s}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Hermes: Stateless Orchestrator")
    parser.add_argument("--generate-jobs", action="store_true",
                        help="Generate jobs from job_queue.yaml")
    parser.add_argument("--poll", action="store_true",
                        help="Single poll cycle (call from cron every 5 min)")
    parser.add_argument("--status", action="store_true",
                        help="Print queue status")
    parser.add_argument("--reclaim", action="store_true",
                        help="Manually reclaim stale jobs")

    args = parser.parse_args()

    if args.generate_jobs:
        generate_jobs_from_config()
    elif args.poll:
        poll_cycle()
    elif args.reclaim:
        reclaim_abandoned(timeout_hours=24)
    elif args.status or not any(vars(args).values()):
        print_status()


if __name__ == "__main__":
    main()
