#!/usr/bin/env python3
"""
Real M3 verifier for LZMA: Direction 2 — the preimage is a symmetry ORBIT, not a
counting cell.

gauge_orbit realizes m equal-length streams that all decode to one output; the SAME
coarsening_cell that read gzip's counting CELL reads this as an orbit (biggest_cell
== m, best_genuine_width == 0). LZMA is orbit-only (no transmitted model). Full
scope -> a pass that reproduces the known N=3 orbit result.
"""
from __future__ import annotations

from verify import Evidence, register
from zstdct.spoonfeed import coarsening_cell
from zstdct.spoonfeed_lzma import (
    compress_alone, decode_lzma_alone, gauge_orbit, encoder_gauge_bits,
)

_DATA = b"the quick brown fox jumps over the lazy dog " * 200
_M = 8


@register("lzma", "m3")
def verify_lzma_m3(job) -> Evidence:
    comp = compress_alone(_DATA)
    base = decode_lzma_alone(comp)
    orbit = gauge_orbit(comp, m=_M)
    outs = [decode_lzma_alone(s) for s in orbit]
    cc = coarsening_cell([tuple(o) for o in outs], [tuple(o) for o in outs])

    all_same = all(o == base for o in outs)
    equal_len = all(len(s) == len(comp) for s in orbit)
    orbit_fat = cc["biggest_cell"] == _M
    width_zero = cc["best_genuine_width"] == 0

    checks = {
        "orbit_all_decode_same": bool(all_same),
        "orbit_equal_length": bool(equal_len),
        "orbit_is_fat": bool(orbit_fat),
        "orbit_width_zero": bool(width_zero),
    }

    return Evidence(
        checks=checks,
        metrics={"mode_a_flavor": "orbit", "orbit_count": _M, "cell_count": 0,
                 "gauge_bits": round(float(encoder_gauge_bits(comp)), 2)},
        scope="full",
        detail=(f"LZMA M3 (Direction 2): Mode-A preimage is a symmetry ORBIT — {_M} equal-length "
                f"streams all decoding to one output (biggest_cell {cc['biggest_cell']}, width 0). "
                "Orbit-only; no counting cell (no transmitted model)."),
        tags=["gauge_orbit", "orbit_only", "symmetry", "direction2", "no_counting"],
    )
