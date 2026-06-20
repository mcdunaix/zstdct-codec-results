#!/usr/bin/env python3
"""
Real M2 verifier for gzip: Direction 1 — the streaming re-encoder reproduces real
output BYTE-FOR-BYTE.

Reuses the spoon-feed encoder (reencode_gzip / reencode_raw_deflate): re-emitting
a real gzip stream and a raw-DEFLATE stream reproduces them exactly across a varied
corpus. Full scope + byte-exact -> a legitimate pass.
"""
from __future__ import annotations

import gzip

from verify import Evidence, register
from zstdct.spoonfeed_gzip import reencode_gzip, reencode_raw_deflate, compress_raw_deflate


def _corpus():
    return [
        b"a",
        b"hello world",
        b"the quick brown fox jumps over the lazy dog " * 40,
        bytes((i * 73) % 256 for i in range(3000)),
        ("lorem ipsum dolor sit amet " * 60).encode(),
        b"\x00" * 4000,
        bytes(range(256)) * 8,
        b"\n".join(f"row {i},{i * i}".encode() for i in range(400)),
        b"abcabcabcabc" * 200,
    ]


@register("gzip", "m2")
def verify_gzip_m2(job) -> Evidence:
    corpus = _corpus()
    gzip_ok = 0
    raw_ok = 0
    fails = []
    for i, x in enumerate(corpus):
        comp = gzip.compress(x, mtime=0)
        try:
            if reencode_gzip(comp) == comp:
                gzip_ok += 1
            else:
                fails.append(f"case {i}: gzip re-emit differs")
            rawc = compress_raw_deflate(x, 9)
            if reencode_raw_deflate(rawc) == rawc:
                raw_ok += 1
            else:
                fails.append(f"case {i}: raw-deflate re-emit differs")
        except Exception as e:
            fails.append(f"case {i}: {type(e).__name__}: {e}")

    n = len(corpus)
    byte_exact = (gzip_ok == n) and (raw_ok == n)
    detail = (f"gzip M2 (Direction 1): re-encoder reproduces stdlib gzip AND raw-DEFLATE "
              f"byte-for-byte on {gzip_ok}/{n} gzip and {raw_ok}/{n} raw streams.")
    if fails:
        detail += " " + "; ".join(fails[:3])

    return Evidence(
        checks={"gzip_byte_exact": gzip_ok == n, "raw_deflate_byte_exact": raw_ok == n,
                "corpus_min": n >= 8},
        metrics={"byte_exact": byte_exact, "reencode_samples": n,
                 "gzip_reencoded": gzip_ok, "raw_reencoded": raw_ok},
        scope="full",
        detail=detail,
        tags=["deflate", "reencode", "direction1", "byte_exact"],
    )
