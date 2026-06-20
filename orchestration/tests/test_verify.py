#!/usr/bin/env python3
"""
Self-contained tests for the legitimacy backbone. No pytest dependency so this
runs anywhere (including Termux):  python3 orchestration/tests/test_verify.py

Proves the four behaviors that make a fabricated pass impossible:
  1. An unimplemented codec reports not_implemented, never pass.
  2. A pass that lacks measured evidence is REJECTED by the gate.
  3. A real, fully-covered round-trip mints a legitimate pass.
  4. Snappy M0 reports partial with MEASURED evidence (real decoder exercised).
Plus: a failing check -> fail (not pass); a raising verifier -> error (not pass).
"""
import struct
import sys
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH_DIR))

import verify  # noqa: E402
from verify import Evidence, register, run_stage, validate_result  # noqa: E402

_failures = []


def check(name, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _failures.append(name)


# --- A real, fully-correct toy codec to prove the pass path (stored + len header) ---

def _identity_encode(data: bytes) -> bytes:
    return struct.pack("<I", len(data)) + data


def _identity_decode(blob: bytes) -> bytes:
    (n,) = struct.unpack("<I", blob[:4])
    return blob[4:4 + n]


@register("identity", "m0")
def _verify_identity_m0(job):
    corpus = [bytes((i * 7 + k) % 256 for k in range(i + 1)) for i in range(12)]
    ok = sum(1 for d in corpus if _identity_decode(_identity_encode(d)) == d)
    n = len(corpus)
    return Evidence(
        checks={"all_byte_exact": ok == n, "corpus_min": n >= verify.M0_MIN_FILES},
        metrics={"files_decoded": ok, "test_cases": n, "byte_exact": ok == n,
                 "decoder_lines": 6},
        scope="full",
        detail=f"identity round-trip {ok}/{n}",
    )


@register("brokencodec", "m0")
def _verify_broken_m0(job):
    # A verifier that honestly reports its decoder is wrong.
    return Evidence(
        checks={"all_byte_exact": False, "corpus_min": True},
        metrics={"files_decoded": 0, "test_cases": 10, "byte_exact": False},
        scope="full",
        detail="decoder produced wrong bytes",
    )


@register("explodingcodec", "m0")
def _verify_exploding_m0(job):
    raise RuntimeError("decoder blew up")


def test_unimplemented_is_not_pass():
    print("1. unimplemented codec -> not_implemented (never pass)")
    r = run_stage({"id": "fakezip_m0", "codec": "fakezip", "stage": "m0"})
    check("status is not_implemented", r["status"] == "not_implemented", r["status"])
    check("status is not pass", r["status"] != "pass")
    check("carries provenance", bool(r.get("provenance")))
    ok, errs = validate_result(r)
    check("gate accepts honest not_implemented", ok, str(errs))


def test_gate_rejects_unbacked_pass():
    print("2. fabricated pass with no evidence -> REJECTED by gate")
    fake = {"job_id": "x_m0", "codec": "x", "stage": "m0", "status": "pass",
            "metrics": {}, "provenance": {"code_sha": "abc"}}
    ok, errs = validate_result(fake)
    check("gate rejects pass without metrics", not ok, str(errs))

    fake2 = {"job_id": "x_m0", "codec": "x", "stage": "m0", "status": "pass",
             "metrics": {"byte_exact": True, "files_decoded": 9, "test_cases": 9},
             "provenance": {}}
    ok2, errs2 = validate_result(fake2)
    check("gate rejects pass without provenance", not ok2, str(errs2))

    fake3 = {"job_id": "x_m0", "codec": "x", "stage": "m0", "status": "pass",
             "metrics": {"byte_exact": True, "files_decoded": 3, "test_cases": 3},
             "provenance": {"code_sha": "abc"}}
    ok3, _ = validate_result(fake3)
    check("gate rejects pass with too-small corpus", not ok3)


def test_real_roundtrip_passes():
    print("3. real round-trip -> legitimate pass")
    r = run_stage({"id": "identity_m0", "codec": "identity", "stage": "m0"})
    check("status is pass", r["status"] == "pass", r["status"])
    check("byte_exact measured true", r["metrics"].get("byte_exact") is True)
    ok, errs = validate_result(r)
    check("gate accepts evidence-backed pass", ok, str(errs))


def test_snappy_is_partial_with_real_metrics():
    print("4. snappy m0 -> partial, MEASURED evidence (real decoder)")
    r = run_stage({"id": "snappy_m0", "codec": "snappy", "stage": "m0"})
    check("status is partial", r["status"] == "partial", r["status"])
    m = r.get("metrics", {})
    check("byte_exact true (decoder bug fixed)", m.get("byte_exact") is True, str(m))
    check("files_decoded == test_cases", m.get("files_decoded") == m.get("test_cases"),
          f"{m.get('files_decoded')}/{m.get('test_cases')}")
    check("test_cases >= 8", int(m.get("test_cases", 0)) >= 8)
    check("decoder_lines measured (>0)", int(m.get("decoder_lines", 0)) > 0,
          str(m.get("decoder_lines")))
    ok, errs = validate_result(r)
    check("gate accepts honest partial", ok, str(errs))
    check("partial is not pass", r["status"] != "pass")


def test_failing_and_erroring_are_not_pass():
    print("5. failing check -> fail; raising verifier -> error (neither is pass)")
    rf = run_stage({"id": "brokencodec_m0", "codec": "brokencodec", "stage": "m0"})
    check("broken decoder -> fail", rf["status"] == "fail", rf["status"])
    check("fail is not pass", rf["status"] != "pass")
    re_ = run_stage({"id": "explodingcodec_m0", "codec": "explodingcodec", "stage": "m0"})
    check("raising verifier -> error", re_["status"] == "error", re_["status"])
    check("error is not pass", re_["status"] != "pass")
    check("error captured", "decoder blew up" in re_.get("error", ""))


def test_base64_is_real_pass():
    print("6. base64 m0 -> real FULL pass (trivial real codec, RFC 4648)")
    r = run_stage({"id": "base64_m0", "codec": "base64", "stage": "m0"})
    check("status is pass", r["status"] == "pass", r["status"])
    m = r.get("metrics", {})
    check("byte_exact true", m.get("byte_exact") is True, str(m))
    check("files_decoded == test_cases", m.get("files_decoded") == m.get("test_cases"),
          f"{m.get('files_decoded')}/{m.get('test_cases')}")
    check("decoder_lines measured (>0)", int(m.get("decoder_lines", 0)) > 0)
    ok, errs = validate_result(r)
    check("gate accepts evidence-backed pass", ok, str(errs))


def main():
    print("=" * 64)
    print("Legitimacy backbone tests")
    print("=" * 64)
    test_unimplemented_is_not_pass()
    test_gate_rejects_unbacked_pass()
    test_real_roundtrip_passes()
    test_snappy_is_partial_with_real_metrics()
    test_failing_and_erroring_are_not_pass()
    test_base64_is_real_pass()
    print("=" * 64)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
