#!/usr/bin/env python3
"""
Real, FULL-scope M0 verifier for base64 (RFC 4648).

The simplest real codec end to end: our from-scratch decoder
(codec_b64.b64_decode) is checked against the stdlib base64 ENCODER as the
authoritative reference, over a corpus that includes the 7 RFC 4648 test
vectors. Full scope + all byte-exact -> a legitimate `pass`.
"""
from __future__ import annotations

import base64
from pathlib import Path

from verify import Evidence, register

ORCH_DIR = Path(__file__).resolve().parent.parent
DECODER_SRC = ORCH_DIR / "codec_b64.py"


def _corpus():
    rfc = [b"", b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar"]  # RFC 4648 §10
    extra = [
        bytes(range(256)),                                   # all byte values
        b"\x00" * 100,
        b"\xff" * 65,
        b"the quick brown fox jumps over the lazy dog " * 5,
        bytes((i * 31) % 256 for i in range(513)),           # not a multiple of 3
        b"\n\r\t binary\x00mix \x80\x81",
    ]
    return rfc + extra


def _decoder_lines():
    try:
        return len(DECODER_SRC.read_text().splitlines())
    except Exception:
        return -1


@register("base64", "m0")
def verify_base64_m0(job) -> Evidence:
    from codec_b64 import b64_decode

    corpus = _corpus()
    ok = 0
    fails = []
    for i, original in enumerate(corpus):
        reference = base64.b64encode(original)  # authoritative reference encoder
        try:
            decoded = b64_decode(reference)
        except Exception as e:
            fails.append(f"case {i}: raised {type(e).__name__}: {e}")
            continue
        if decoded == original:
            ok += 1
        else:
            fails.append(f"case {i}: got {len(decoded)}B, want {len(original)}B")

    n = len(corpus)
    byte_exact = ok == n
    detail = (f"RFC 4648 base64: from-scratch decoder vs stdlib reference encoder — "
              f"{ok}/{n} byte-exact (incl. the 7 RFC test vectors). Full scope.")
    if fails:
        detail += " Failures: " + "; ".join(fails[:3])

    return Evidence(
        checks={"all_byte_exact": byte_exact, "corpus_min": n >= 8},
        metrics={
            "files_decoded": ok,
            "test_cases": n,
            "byte_exact": byte_exact,
            "decoder_lines": _decoder_lines(),  # MEASURED from source
        },
        scope="full",
        detail=detail,
        tags=["encoding", "base64", "rfc4648"],
    )
