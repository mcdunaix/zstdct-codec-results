#!/usr/bin/env python3
"""
Real M4 verifier for gzip: synthesis — integrate the findings into the taxonomy.

Re-derives the N=4 gzip picture from the validated trace: recursive Kraft-inside-
Kraft (new tag), the conventional model as a degenerate counting cell (new tag),
the three model locations (none/conventional/shipped), the LZ op-mix, and the
gauge present-but-pinned. All sub-analyses run and agree -> synthesis_complete ->
a legitimate pass.
"""
from __future__ import annotations

import numpy as np

from verify import Evidence, register
from zstdct.spoonfeed import gauge_size
from zstdct.spoonfeed_gzip import (
    recursive_kraft, fixed_block_degenerate_cell, where_is_the_model,
    op_mix_gzip, encoder_trace_gzip, real_block_trees,
)

_TEXT = b"the of and to in is was he for it with as " * 300


@register("gzip", "m4")
def verify_gzip_m4(job) -> Evidence:
    # New tag #1: recursive Kraft inside Kraft (cl-code and both main trees each Kraft == 1).
    manifolds = recursive_kraft(_TEXT)
    recursive_ok = bool(manifolds) and all(
        rk["litlen_kraft"] == 1 and rk["dist_kraft"] == 1 and rk["cl_kraft"] == 1
        for rk in manifolds)

    # New tag #2: conventional (fixed) model == a degenerate counting cell.
    rng = np.random.default_rng(0)
    small = [bytes(rng.integers(0, 64, 20, dtype=np.uint8)) for _ in range(40)]
    deg, n_fixed = fixed_block_degenerate_cell(small)
    degenerate_cell = (deg["distinct_shapes"] == 1 and deg["best_genuine_width"] > 0
                       and n_fixed >= 30)

    # Three model locations; confirm the shipped (dynamic) counting model is present.
    models = {b["model"] for b in where_is_the_model(_TEXT, 9)}
    shipped_model = "shipped" in models

    # Mode-A-by-symmetry gauge present (prod n_L! > 1) on the real trees.
    trees = real_block_trees(_TEXT)
    gauge_present = bool(trees) and all(gauge_size(t["litlen_lengths"])[0] > 1 for t in trees)

    # The LZ op-mix is readable off the validated trace.
    _comp, blocks = encoder_trace_gzip(_TEXT, 9)
    mix = op_mix_gzip(blocks)
    mix_ok = mix["n_tokens"] > 0

    checks = {
        "recursive_kraft_inside_kraft": bool(recursive_ok),
        "conventional_is_degenerate_cell": bool(degenerate_cell),
        "shipped_counting_model": bool(shipped_model),
        "gauge_present": bool(gauge_present),
        "op_mix_readable": bool(mix_ok),
    }
    synth = all(checks.values())

    return Evidence(
        checks=checks,
        metrics={
            "synthesis_complete": synth,
            "n_dynamic_blocks": len(manifolds),
            "literal_frac": round(float(mix["literal_frac"]), 4),
            "match_frac": round(float(mix["match_frac"]), 4),
            "n_fixed_degenerate": int(n_fixed),
        },
        scope="full",
        detail=("gzip M4 synthesis: counting cell + recursive Kraft-inside-Kraft (new) + conventional "
                "model = degenerate cell (new) + shipped model present + gauge present-but-pinned. "
                "Cell-only; N=4 in the static-model family."),
        tags=["deflate", "static_model", "counting_cell", "recursive_kraft",
              "conventional_model", "gauge_pinned", "cell_only", "synthesis_n4"],
    )
