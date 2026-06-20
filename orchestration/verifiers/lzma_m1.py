#!/usr/bin/env python3
"""
Real M1 verifier for LZMA: the kill-switch.

The range coder is TOTAL (Mode B — any bitstream decodes), it spends exactly the
bits the law predicts, the LZ layer (not the entropy coder) does the thinning, and
the final-interval slack is an OUTPUT-PRESERVING gauge (a covert channel). Counting
is ABSENT (no transmitted model). All hold -> kill_switch_pass -> a legitimate pass.
"""
from __future__ import annotations

from verify import Evidence, register
from zstdct.spoonfeed_lzma import (
    compress_alone, range_decoder_totality, range_cost,
    lz_reference_constraint, trailing_gauge_bits, gauge_block_invariant,
)

_COMP = compress_alone(b"the quick brown fox jumps over the lazy dog " * 60)


@register("lzma", "m1")
def verify_lzma_m1(job) -> Evidence:
    bits = range_decoder_totality(0)               # asserts internally; returns bits if total
    range_total = len(bits) == 3000

    rc = range_cost(_COMP)
    residue = rc["cost_bits"] - 8 * rc["n_renorm"]
    range_mode_b = (abs(rc["cost_bits"] - rc["law"]) < 1e-6) and (0 < residue <= 8)

    ok_n, fail_n = lz_reference_constraint(0)
    lz_thins = ok_n > 0 and fail_n > 0

    gbits = trailing_gauge_bits(_COMP)
    gauge_covert = gbits > 0 and gauge_block_invariant(_COMP) is True

    checks = {
        "range_coder_total_mode_b": bool(range_total),
        "spends_law_bits": bool(range_mode_b),
        "lz_layer_thins_not_entropy": bool(lz_thins),
        "gauge_is_output_preserving_channel": bool(gauge_covert),
    }
    kill = all(checks.values())

    return Evidence(
        checks=checks,
        metrics={"kill_switch_pass": kill, "gauge_bits": int(gbits),
                 "cost_residue_bits": round(float(residue), 3), "lz_ok": ok_n, "lz_fail": fail_n},
        scope="full",
        detail=("LZMA M1 kill-switch: range coder TOTAL (Mode B) + spends exactly law bits + LZ layer "
                "thins (not the entropy coder) + final-interval slack is an output-preserving gauge "
                "(covert channel). Counting ABSENT — no transmitted model."),
        tags=["kill_switch", "mode_b", "range_coder_total", "gauge_covert_channel", "no_counting"],
    )
