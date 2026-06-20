#!/usr/bin/env python3
"""
seed_queue.py — drop jobs into the queue the autonomous loop walks.

A job is queue/<codec>_<stage>.json with {id, codec, stage, spec_url, priority}.
The loop pulls these, generates a verifier in-loop when one is missing, gates the
result, and dequeues. Build plan: M0 first for a stdlib codec we don't ship a
verifier for yet (bz2), then widen.

Usage:
  python seed_queue.py                      # default: bz2 m0 (the proving job)
  python seed_queue.py bz2 zlib --stages m0 # M0 for several codecs
  python seed_queue.py bz2 --all-stages     # m0..m4 for bz2 (m1/m3/m4 report honest)
  python seed_queue.py bz2:m0 lzma:m2       # explicit codec:stage pairs
  python seed_queue.py --dir /tmp/q bz2     # write somewhere other than the repo queue/
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from git_queue import QUEUE_DIR

STAGES = ("m0", "m1", "m2", "m3", "m4")

SPEC_URL = {
    "bz2": "https://en.wikipedia.org/wiki/Bzip2",
    "bzip2": "https://en.wikipedia.org/wiki/Bzip2",
    "zlib": "https://www.rfc-editor.org/rfc/rfc1950",
    "gzip": "https://www.rfc-editor.org/rfc/rfc1952",
    "lzma": "https://tukaani.org/xz/xz-file-format.txt",
}


def write_job(out_dir: Path, codec: str, stage: str, priority: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": f"{codec}_{stage}",
        "codec": codec,
        "stage": stage,
        "spec_url": SPEC_URL.get(codec, ""),
        "priority": priority,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = out_dir / f"{job['id']}.json"
    path.write_text(json.dumps(job, indent=2))
    return path


def parse_targets(args) -> list[tuple[str, str]]:
    """Expand CLI args into (codec, stage) pairs."""
    pairs: list[tuple[str, str]] = []
    if args.all_stages:
        stages = list(STAGES)
    else:
        stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    for token in args.codecs or ["bz2"]:
        if ":" in token:                       # explicit codec:stage
            codec, stage = token.split(":", 1)
            pairs.append((codec, stage))
        else:
            for stage in stages:
                pairs.append((token, stage))
    return pairs


def main():
    ap = argparse.ArgumentParser(description="Seed the codec decomposition queue.")
    ap.add_argument("codecs", nargs="*", help="codec names or codec:stage pairs (default: bz2)")
    ap.add_argument("--stages", default="m0", help="comma-separated stages (default: m0)")
    ap.add_argument("--all-stages", action="store_true", help="seed m0..m4")
    ap.add_argument("--dir", default=str(QUEUE_DIR), help="queue directory")
    args = ap.parse_args()

    out_dir = Path(args.dir)
    pairs = parse_targets(args)
    # bz2 first, then the rest; stage order m0<m2<m1<m3<m4 (oracle-strength order).
    stage_rank = {"m0": 0, "m2": 1, "m1": 2, "m3": 3, "m4": 4}
    pairs.sort(key=lambda cs: (cs[0] != "bz2", stage_rank.get(cs[1], 9), cs[0]))

    print(f"Seeding {len(pairs)} job(s) into {out_dir}")
    for i, (codec, stage) in enumerate(pairs):
        if stage not in STAGES:
            print(f"  ! skipping invalid stage: {codec}:{stage}")
            continue
        path = write_job(out_dir, codec, stage, priority=len(pairs) - i)
        print(f"  + {path.name}")


if __name__ == "__main__":
    main()
