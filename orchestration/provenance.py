#!/usr/bin/env python3
"""
provenance.py — pin the code + environment that produced a result, so every
result is reproducible and auditable after the fact.

A result without provenance is a claim with no chain of custody. Every result
records: which orchestration commit produced it, whether the tree was dirty,
which device ran it, the hash of the verifier source, and a UTC timestamp.

Dependency-free (stdlib only) so it runs on Termux/Android.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ORCH_VERSION = "2.0-legitimacy"

ORCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent  # results-repo root on the device


def _git(args):
    """Run a git command in the repo root; return stdout or None."""
    try:
        out = subprocess.run(
            ["git"] + args, cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _hash_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return None


def _device_id():
    """A stable identity for the machine that produced the result."""
    for env in ("CODEWHALE_DEVICE", "TAILSCALE_HOSTNAME", "ANDROID_ID"):
        v = os.environ.get(env)
        if v:
            return v
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def collect_provenance(verifier=None) -> dict:
    """Provenance block stamped into every result. `verifier` is the callable
    that produced the evidence (None for not_implemented stages)."""
    sha = _git(["rev-parse", "HEAD"])
    porcelain = _git(["status", "--porcelain"])
    prov = {
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "device": _device_id(),
        "code_sha": sha,
        "code_dirty": bool(porcelain) if porcelain is not None else None,
        "orchestration_version": ORCH_VERSION,
    }
    if verifier is not None:
        try:
            src_file = inspect.getsourcefile(verifier)
            prov["verifier"] = getattr(verifier, "__qualname__", str(verifier))
            prov["source_hash"] = _hash_file(src_file) if src_file else None
        except Exception:
            prov["verifier"] = str(verifier)
            prov["source_hash"] = None
    return prov
