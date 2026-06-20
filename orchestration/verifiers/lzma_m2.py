#!/usr/bin/env python3
"""
Real M2 verifier for LZMA: Direction 1 — the range ENCODER reproduces the real
FORMAT_ALONE payload BYTE-FOR-BYTE from its own decode tape (reencode_lzma_alone),
and offset 0 is the standard representative. Full scope + byte-exact -> a pass.
"""
from __future__ import annotations

from verify import Evidence, register
from zstdct.spoonfeed_lzma import compress_alone, reencode_lzma_alone, reencode_with_offset


def _corpus():
    return [
        b"hello world",
        b"the quick brown fox " * 50,
        bytes((i * 73) % 256 for i in range(3000)),
        ("lorem ipsum " * 80).encode(),
        b"\x00" * 4000,
        bytes(range(256)) * 8,
        b"abcabc" * 400,
        b"\n".join(f"line {i}".encode() for i in range(300)),
    ]


@register("lzma", "m2")
def verify_lzma_m2(job) -> Evidence:
    corpus = _corpus()
    ok = 0
    offset0 = 0
    fails = []
    for i, x in enumerate(corpus):
        comp = compress_alone(x)
        try:
            if reencode_lzma_alone(comp) == comp:
                ok += 1
            else:
                fails.append(f"case {i}: re-emit differs")
            if reencode_with_offset(comp, 0) == reencode_lzma_alone(comp):
                offset0 += 1
        except Exception as e:
            fails.append(f"case {i}: {type(e).__name__}: {e}")

    n = len(corpus)
    byte_exact = ok == n
    detail = (f"LZMA M2 (Direction 1): range encoder reproduces the FORMAT_ALONE payload "
              f"byte-for-byte on {ok}/{n} streams (offset 0 == standard on {offset0}/{n}).")
    if fails:
        detail += " " + "; ".join(fails[:3])

    return Evidence(
        checks={"reencode_byte_exact": ok == n, "offset0_is_standard": offset0 == n,
                "corpus_min": n >= 8},
        metrics={"byte_exact": byte_exact, "reencode_samples": n, "reencoded": ok},
        scope="full",
        detail=detail,
        tags=["lzma", "reencode", "direction1", "byte_exact", "range_encoder"],
    )
