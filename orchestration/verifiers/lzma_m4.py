#!/usr/bin/env python3
"""
Real M4 verifier for LZMA: synthesis — integrate the findings into the taxonomy.

Re-derives the N=3 cross-family LZMA picture from the validated trace and classifies
it as the THIRD regime (bzip2 = both, zstd = cell-only, LZMA = orbit-only):

  1. the range-coded payload is a Mode-B BIJECTION — it spends exactly the law bits
     (cost == 8*n_renorm + (32 - log2 final_range)), cost only, set stays full;
  2. the Mode-A-by-symmetry gauge is realized as a fat symmetry ORBIT — m equal-length
     streams all decoding to one output, read by the SAME coarsening_cell that saw
     gzip's counting CELL (biggest_cell == m, width 0);
  3. Mode-A-by-counting is ABSENT — zero transmitted-model bytes and a single-valued
     initial model, so there is no conservation surface and hence no counting cell;
  4. the gauge is present and UNPINNED — the full final-interval width (24..32 bits)
     is free, unlike gzip's present-but-pinned gauge;
  5. the LZ op-mix is readable off a byte-exact-asserted encoder trace.

All sub-analyses run and agree -> synthesis_complete -> a legitimate pass. Orbit-only;
no counting cell (no transmitted model) — the distinguishing N=3 result.
"""
from __future__ import annotations

from verify import Evidence, register
from zstdct.spoonfeed import coarsening_cell
from zstdct.spoonfeed_lzma import (
    compress_alone, decode_lzma_alone, range_cost, gauge_orbit,
    encoder_gauge_bits, stream_layout, initial_model,
    encoder_trace_lzma, op_mix,
)

_DATA = b"the quick brown fox jumps over the lazy dog " * 200
_M = 8


@register("lzma", "m4")
def verify_lzma_m4(job) -> Evidence:
    comp = compress_alone(_DATA)

    # 1. Payload = Mode-B bijection: spends exactly the law bits, residue in the final
    #    interval only (0 < residue <= 8). Cost only; the set stays full.
    rc = range_cost(comp)
    residue = rc["cost_bits"] - 8 * rc["n_renorm"]
    payload_mode_b = abs(rc["cost_bits"] - rc["law"]) < 1e-6 and 0 < residue <= 8

    # 2. Gauge = a fat symmetry ORBIT: m equal-length streams, all decode to one output,
    #    read by the codec-agnostic coarsening_cell as fat (biggest_cell == m) + width 0.
    orbit = gauge_orbit(comp, m=_M)
    outs = [decode_lzma_alone(s) for s in orbit]
    base = decode_lzma_alone(comp)
    cc = coarsening_cell([tuple(o) for o in outs], [tuple(o) for o in outs])
    gauge_is_orbit = (all(o == base for o in outs)
                      and all(len(s) == len(comp) for s in orbit)
                      and cc["biggest_cell"] == _M
                      and cc["best_genuine_width"] == 0)

    # 3. Counting ABSENT: zero transmitted-model bytes + a single-valued initial model
    #    => no conservation surface => the orbit is NOT a counting cell (width 0 confirms).
    layout = stream_layout(comp)
    model = initial_model()
    counting_absent = (layout["model_bytes"] == 0
                       and model["distinct_init_values"] == 1
                       and cc["n_genuine_cells"] == 0)

    # 4. Gauge present and UNPINNED: the whole final-interval width is free (24..32 bits),
    #    unlike gzip's pinned gauge.
    gbits = encoder_gauge_bits(comp)
    gauge_unpinned = 24.0 <= gbits < 32.0

    # 5. The LZ op-mix is readable off a byte-exact-asserted trace (encoder_trace_lzma
    #    asserts the instrumented decode == data == lzma.decompress before we read it).
    _comp2, tr = encoder_trace_lzma(_DATA)
    mix = op_mix(tr)
    mix_ok = mix["n_tokens"] > 0

    checks = {
        "payload_is_mode_b_bijection": bool(payload_mode_b),
        "gauge_is_symmetry_orbit": bool(gauge_is_orbit),
        "counting_absent": bool(counting_absent),
        "gauge_present_unpinned": bool(gauge_unpinned),
        "op_mix_readable": bool(mix_ok),
    }
    synth = all(checks.values())

    return Evidence(
        checks=checks,
        metrics={
            "synthesis_complete": synth,
            "mode_a_flavor": "orbit",
            "orbit_count": _M,
            "cell_count": 0,
            "gauge_bits": round(float(gbits), 2),
            "cost_residue_bits": round(float(residue), 3),
            "literal_frac": round(float(mix["literal_frac"]), 4),
            "match_frac": round(float(mix["match_frac"]), 4),
            "model_bytes": int(layout["model_bytes"]),
        },
        scope="full",
        detail=("LZMA M4 synthesis: Mode-B payload bijection (spends exactly the law bits) + "
                "Mode-A-by-symmetry gauge realized as a fat ORBIT (width 0, no counting cell) + "
                "counting ABSENT (zero transmitted model) + gauge present-but-UNPINNED. "
                "Orbit-only — the third regime (bzip2=both, zstd=cell-only, LZMA=orbit-only); N=3 cross-family."),
        tags=["lzma", "range_coder", "lz77", "adaptive", "mode_b", "gauge_orbit",
              "orbit_only", "no_counting", "no_transmitted_model", "third_regime",
              "synthesis_n3"],
    )
