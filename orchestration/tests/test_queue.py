#!/usr/bin/env python3
"""
Integration tests for the queue gate + completion logic, with git stubbed out
(no network, no commits). Runs anywhere:

    python3 orchestration/tests/test_queue.py

Proves:
  - submit_job_result REJECTS a fabricated pass and quarantines it (never library)
  - submit_job_result ACCEPTS an evidence-backed result into the library
  - is_codec_complete / get_codec_pass_progress count real passes, not files
"""
import json
import sys
import tempfile
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH_DIR))

import git_queue as gq          # noqa: E402
from verify import run_stage    # noqa: E402

_failures = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _failures.append(name)


# Redirect all on-disk state into a temp dir and stub git so nothing leaves the box.
_tmp = Path(tempfile.mkdtemp(prefix="cwq_"))
gq.RESULTS_DIR = _tmp / "results" / "codec_library"
gq.FAILED_DIR = _tmp / "failed"
gq.QUEUE_DIR = _tmp / "queue"
gq.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
_pushed = []
gq.push_changes = lambda msg: (_pushed.append(msg) or True)


def _write_status(codec, stage, status):
    d = gq.RESULTS_DIR / codec
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stage}.json").write_text(json.dumps({"status": status}))


def test_reject_fabricated_pass():
    print("1. fabricated pass at submit -> rejected + quarantined, not in library")
    fake = {"job_id": "brotli_m0", "codec": "brotli", "stage": "m0",
            "status": "pass", "metrics": {}, "notes": "TODO"}
    outcome = gq.submit_job_result("brotli_m0", "brotli", "m0", fake)
    check("outcome.ok is False", outcome["ok"] is False, str(outcome))
    check("marked rejected", outcome.get("rejected") is True)
    check("NOT written to library", not (gq.RESULTS_DIR / "brotli" / "m0.json").exists())
    check("quarantined to failed/", (gq.FAILED_DIR / "brotli_m0.json").exists())


def test_accept_evidence_backed_result():
    print("2. real snappy partial -> accepted into library")
    result = run_stage({"id": "snappy_m0", "codec": "snappy", "stage": "m0"})
    outcome = gq.submit_job_result("snappy_m0", "snappy", "m0", result)
    check("outcome.ok is True", outcome["ok"] is True, str(outcome))
    lib = gq.RESULTS_DIR / "snappy" / "m0.json"
    check("written to library", lib.exists())
    if lib.exists():
        saved = json.loads(lib.read_text())
        check("status is partial", saved.get("status") == "partial", saved.get("status"))
        check("carries provenance", bool(saved.get("provenance")))
        check("carries saved_at", bool(saved.get("saved_at")))


def test_completion_counts_real_passes():
    print("3. completion = all stages PASS, not just present")
    for s in ["m0", "m1", "m2", "m3"]:
        _write_status("lz4", s, "pass")
    _write_status("lz4", "m4", "partial")   # present but NOT a pass
    passed, total = gq.get_codec_pass_progress("lz4")
    check("pass progress 4/5", (passed, total) == (4, 5), f"{passed}/{total}")
    check("not complete (m4 is partial)", gq.is_codec_complete("lz4") is False)

    _write_status("lz4", "m4", "pass")
    check("complete once all pass", gq.is_codec_complete("lz4") is True)
    passed, total = gq.get_codec_pass_progress("lz4")
    check("pass progress 5/5", (passed, total) == (5, 5))


def main():
    print("=" * 64)
    print("Queue gate + completion tests")
    print("=" * 64)
    test_reject_fabricated_pass()
    test_accept_evidence_backed_result()
    test_completion_counts_real_passes()
    print("=" * 64)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
