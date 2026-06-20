#!/usr/bin/env python3
"""
Real M0 verifier for LZMA (FORMAT_ALONE).

Our byte-exact range-coder decoder (decode_lzma_alone) reproduces the plaintext
from stdlib-compressed streams (compress_alone wraps stdlib lzma) across a varied
corpus. Full scope + all byte-exact -> a legitimate pass.
"""
from __future__ import annotations

from pathlib import Path

from verify import Evidence, register
from zstdct.spoonfeed_lzma import compress_alone, decode_lzma_alone

ORCH_DIR = Path(__file__).resolve().parent.parent
DECODER_SRC = ORCH_DIR / "zstdct" / "lzma_decoder.py"


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
        b"",
    ]


def _decoder_lines():
    try:
        return len(DECODER_SRC.read_text().splitlines())
    except Exception:
        return -1


@register("lzma", "m0")
def verify_lzma_m0(job) -> Evidence:
    corpus = _corpus()
    ok = 0
    fails = []
    for i, x in enumerate(corpus):
        try:
            if decode_lzma_alone(compress_alone(x)) == x:
                ok += 1
            else:
                fails.append(f"case {i}: mismatch")
        except Exception as e:
            fails.append(f"case {i}: {type(e).__name__}: {e}")

    n = len(corpus)
    byte_exact = ok == n
    detail = f"LZMA FORMAT_ALONE: decoder reproduces {ok}/{n} stdlib-compressed streams byte-exact."
    if fails:
        detail += " " + "; ".join(fails[:3])

    return Evidence(
        checks={"all_byte_exact": byte_exact, "corpus_min": n >= 8},
        metrics={"files_decoded": ok, "test_cases": n, "byte_exact": byte_exact,
                 "decoder_lines": _decoder_lines()},
        scope="full",
        detail=detail,
        tags=["lzma", "range_coder", "lz77", "adaptive"],
    )
