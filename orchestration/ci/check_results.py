#!/usr/bin/env python3
"""
Independent re-validation of every pushed result.

Runs in CI (GitHub Actions on the results repo) — separate from the worker that
produced the results, so the doer is never the only judge. Re-applies the exact
same gate (verify.validate_result) and, when jsonschema is installed, the JSON
Schema too. Exits non-zero if any result is invalid, failing the push/PR.

Usage:
  python3 orchestration/ci/check_results.py [results_dir]
    (default results_dir = <repo_root>/results/codec_library)
"""
import json
import sys
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ORCH_DIR.parent
sys.path.insert(0, str(ORCH_DIR))

from verify import validate_result  # noqa: E402


def _load_schema_validator():
    """Return a callable(result)->list[str] using jsonschema, or None if absent."""
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads((ORCH_DIR / "schemas" / "codec_result.json").read_text())
    validator = jsonschema.Draft7Validator(schema)

    def _check(result):
        return [e.message for e in validator.iter_errors(result)]

    return _check


def main(argv):
    results_dir = Path(argv[1]) if len(argv) > 1 else REPO_ROOT / "results" / "codec_library"
    schema_check = _load_schema_validator()
    print(f"Checking results under: {results_dir}")
    print(f"JSON-Schema check: {'on' if schema_check else 'off (jsonschema not installed)'}")

    files = sorted(results_dir.rglob("*.json")) if results_dir.exists() else []
    if not files:
        print("No results found — nothing to validate.")
        return 0

    invalid = 0
    passes = 0
    by_status = {}
    for f in files:
        rel = f.relative_to(results_dir.parent) if results_dir in f.parents else f
        try:
            r = json.loads(f.read_text())
        except Exception as e:
            print(f"  ✗ {rel}: unreadable JSON ({e})")
            invalid += 1
            continue

        status = r.get("status", "?")
        by_status[status] = by_status.get(status, 0) + 1
        if status == "pass":
            passes += 1

        errors = []
        _, gate_errors = validate_result(r)
        errors += gate_errors
        if schema_check:
            errors += schema_check(r)

        if errors:
            print(f"  ✗ {rel} [{status}]")
            for e in errors[:4]:
                print(f"      - {e}")
            invalid += 1
        else:
            print(f"  ✓ {rel} [{status}]")

    print("\n" + "-" * 50)
    print(f"{len(files)} result(s): " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"{passes} pass, {invalid} invalid")
    if invalid:
        print("RESULT: FAIL — invalid results must be fixed or re-run before merge.")
        return 1
    print("RESULT: OK — every result is gate-valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
