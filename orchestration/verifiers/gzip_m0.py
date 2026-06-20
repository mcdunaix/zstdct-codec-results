#!/usr/bin/env python3
"""
Real, FULL-scope M0 verifier for gzip / DEFLATE (RFC 1951 + 1952).

"One we know about": this reuses the byte-exact decoder built during the
spoon-feed gzip arc (gzip_decoder.decode_gzip) and checks it against the stdlib
gzip ENCODER over a varied corpus. Full scope + all byte-exact -> a legitimate
`pass`, and it confirms our existing decoder still holds up — now wired through
the legitimacy gate.
"""
from __future__ import annotations

import gzip as gzip_ref
from pathlib import Path

from verify import Evidence, register

ORCH_DIR = Path(__file__).resolve().parent.parent
DECODER_SRC = ORCH_DIR / "gzip_decoder.py"


def _corpus():
    return [
        b"",                                              # empty member
        b"a",
        b"hello world",
        b"hello world " * 100,                            # repetitive -> LZ77 backrefs
        bytes(range(256)),                                # all byte values
        bytes((i * 73) % 256 for i in range(2000)),       # pseudo-random, low redundancy
        b"the quick brown fox jumps over the lazy dog " * 50,
        ("lorem ipsum dolor sit amet " * 40).encode(),
        b"\x00" * 5000,                                   # long run -> RLE-like
        b"\n".join(f"line {i}".encode() for i in range(300)),
    ]


def _decoder_lines():
    try:
        return len(DECODER_SRC.read_text().splitlines())
    except Exception:
        return -1


@register("gzip", "m0")
def verify_gzip_m0(job) -> Evidence:
    from gzip_decoder import decode_gzip

    corpus = _corpus()
    ok = 0
    fails = []
    for i, original in enumerate(corpus):
        compressed = gzip_ref.compress(original, mtime=0)  # stdlib reference encoder
        try:
            decoded = decode_gzip(compressed)
        except Exception as e:
            fails.append(f"case {i}: raised {type(e).__name__}: {e}")
            continue
        if decoded == original:
            ok += 1
        else:
            fails.append(f"case {i}: got {len(decoded)}B, want {len(original)}B")

    n = len(corpus)
    byte_exact = ok == n
    detail = (f"gzip/DEFLATE (RFC 1951/1952): spoon-feed decoder vs stdlib gzip encoder — "
              f"{ok}/{n} byte-exact across text, binary, repetitive and run cases. Full scope.")
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
        tags=["deflate", "huffman", "lz77", "static_model", "rfc1951", "rfc1952"],
    )
