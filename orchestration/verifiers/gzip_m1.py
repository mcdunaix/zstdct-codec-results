#!/usr/bin/env python3
"""
Real M1 verifier for gzip: the kill-switch — "the preimage mirrors the algorithm".

Reuses the spoon-feed gzip analysis to confirm the six claims that make gzip's
preimage structure legible: payload bijection, entropy-code totality (Mode B),
LZ-layer thinning, a counting model present, the canonical gauge PINNED (no covert
channel), and the off-manifold being EXACTLY the Kraft deficit. All hold ->
kill_switch_pass -> a legitimate full pass.
"""
from __future__ import annotations

from verify import Evidence, register
from zstdct.spoonfeed_gzip import (
    token_payload_roundtrip, fixed_code_is_total, lz_backreference_constraint,
    noncanonical_breaks_output, manifold_holes, fixed_trees_are_conventional_counting,
)

# Valid DEFLATE token sequences (literals + an in-range back-reference).
_TOKENS = [
    [("lit", 72), ("lit", 105), ("match", 4, 2)],
    [("lit", 65), ("lit", 66), ("lit", 67), ("match", 5, 3)],
    [("lit", 0), ("lit", 255), ("lit", 128), ("lit", 7)],
]


@register("gzip", "m1")
def verify_gzip_m1(job) -> Evidence:
    payload_bijection = all(token_payload_roundtrip(t) for t in _TOKENS)

    total = fixed_code_is_total(0)
    entropy_total = total["dangled"] == 0

    lz = lz_backreference_constraint()
    lz_thins = bool(lz["valid_ok"]) and bool(lz["backref_fail"])

    conv = fixed_trees_are_conventional_counting()
    counting_present = (conv["litlen_kraft"] == 1) and (conv["model_bytes_shipped"] == 0)

    nb = noncanonical_breaks_output([2, 2, 2, 2], [0, 1, 2, 3, 3, 2, 1, 0])
    gauge_pinned = (nb["gauge"] > 1) and bool(nb["canonical_ok"]) and bool(nb["swapped_differs"])

    mh = manifold_holes([1, 2, 3])  # incomplete code -> Kraft deficit
    off_manifold_kraft = mh["holes"] == mh["predicted_holes"]

    checks = {
        "payload_bijection": bool(payload_bijection),
        "entropy_code_total": bool(entropy_total),
        "lz_layer_thins": bool(lz_thins),
        "counting_model_present": bool(counting_present),
        "canonical_gauge_pinned": bool(gauge_pinned),
        "off_manifold_is_kraft_deficit": bool(off_manifold_kraft),
    }
    kill = all(checks.values())

    return Evidence(
        checks=checks,
        metrics={
            "kill_switch_pass": kill,
            "entropy_dangled": int(total["dangled"]),
            "gauge_size": int(nb["gauge"]),
            "manifold_holes": int(mh["holes"]),
        },
        scope="full",
        detail=("gzip M1 kill-switch: payload bijection + entropy code total (Mode B) + LZ-layer "
                "thinning + counting model present + canonical gauge PINNED + off-manifold == Kraft "
                "deficit. Preimage mirrors the algorithm."),
        tags=["kill_switch", "counting", "gauge_pinned", "mode_b", "static_model"],
    )
