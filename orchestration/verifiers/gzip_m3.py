#!/usr/bin/env python3
"""
Real M3 verifier for gzip: Direction 2 — locate the structural thinning.

Reuses litlen_counting_cells + gauge_has_no_orbit to confirm gzip's Mode-A
preimage is a counting CELL on the gauge-invariant multiset (distinct byte
histograms rounding onto one lit/len tree SHAPE, best_genuine_width > 0), while
the canonical gauge is pinned with NO orbit. gzip is cell-only (with zstd).
Full scope -> a legitimate pass that reproduces the known N=4 result.
"""
from __future__ import annotations

import numpy as np

from verify import Evidence, register
from zstdct.spoonfeed_gzip import litlen_counting_cells, gauge_has_no_orbit

_TEXT = b"the of and to in is was he for it with as " * 300


def _geometric_iid(skew, n, alphabet, seed):
    """i.i.d. bytes with geometric weights — copied from spoonfeed_fse.geometric_iid
    so M3 doesn't pull in the whole FSE/zstd chain."""
    w = np.exp(-skew * np.arange(alphabet))
    w /= w.sum()
    rng = np.random.default_rng(seed)
    return bytes((rng.choice(alphabet, size=n, p=w).astype(np.uint8) + 32))


@register("gzip", "m3")
def verify_gzip_m3(job) -> Evidence:
    datas = [_geometric_iid(0.8, 8000, 48, i) for i in range(40)]
    vec, ms, n_dyn = litlen_counting_cells(datas)

    cell_on_multiset = ms["best_genuine_width"] > 0 and ms["best_genuine_distinct"] >= 2
    multiset_coarsens = ms["distinct_shapes"] < vec["distinct_shapes"]
    no_orbit = gauge_has_no_orbit(_TEXT)["has_offset_parameter"] is False

    checks = {
        "counting_cell_on_multiset": bool(cell_on_multiset),
        "multiset_coarsens_more": bool(multiset_coarsens),
        "gauge_has_no_orbit": bool(no_orbit),
    }

    return Evidence(
        checks=checks,
        metrics={
            "mode_a_flavor": "counting",
            "cell_count": int(ms["biggest_cell"]),
            "orbit_count": 0,
            "best_genuine_width": int(ms["best_genuine_width"]),
            "n_dynamic": int(n_dyn),
        },
        scope="full",
        detail=(f"gzip M3 (Direction 2): Mode-A preimage is a counting CELL on the gauge-invariant "
                f"multiset (width {ms['best_genuine_width']} > 0, biggest cell {ms['biggest_cell']}, "
                f"{n_dyn} dynamic blocks); canonical gauge pinned -> NO orbit. Cell-only."),
        tags=["counting_cell", "cell_only", "gauge_invariant_multiset", "direction2"],
    )
