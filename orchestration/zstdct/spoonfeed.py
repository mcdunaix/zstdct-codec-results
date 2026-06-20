"""Spoon-feeding the compressor: an off-label instrument over bzip2's encode/decode paths.

This is the crystallized core of the ``toys/spoonfeed.py`` exploration (writeup:
``findings/spoonfeed.md``). The methodology is **general / substrate-agnostic** -- isolate
``E: original -> bits`` and ``D: bits -> original`` and spoon-feed each direction to map the
*reachable set* and the *cost gradient* the encoder works within. **bzip2 is substrate #1**
(its decoder is the one we have fully instrumented in :mod:`zstdct.bzip2_decoder`, byte-exact
vs ``bz2.decompress``).

This module holds the *forwards* and *helpers* the probes reuse. Each is tagged by how it is
grounded -- the proven-vs-illustrated boundary that the adversarial read (2026-06-18) made
explicit:

**PROVEN (anchored to exact bzip2).** These round-trip == identity against the *real* decoder
readers (:func:`zstdct.bzip2_decoder._build_table` / ``_decode_symbol`` / ``_read_code_lengths``
/ ``_read_selectors``), which are themselves byte-exact validated via
``decode_bz2 == bz2.decompress``:

- :func:`canonical_codes` / :func:`encode_payload` -- the exhaustive payload round-trip proves
  ``canonical_codes`` assigns *exactly* bzip2's codewords (not merely a mutual inverse).
- :func:`encode_code_lengths` -- the encoder's direct delta-walk; round-trips through the real
  ``_read_code_lengths``.
- :func:`encode_selectors` -- unary-coded MTF; round-trips through the real ``_read_selectors``.
- :func:`inverse_rle2` -- faithful standalone of the decoder's inverse-RLE2; anchored by
  ``inverse_rle2 . rle2_encode == identity`` against the real forward stage.
- :func:`real_tree_lengths` -- extracts bzip2's own per-group trees with the real readers.
- :func:`bij_encode_run` -- reuses the real ``rle2_encode``, so it *is* bzip2's run-coder.

**ILLUSTRATIVE (deliberately not bzip2).** Used only to demonstrate a mechanism; never the
ground for a "this is what bzip2 does" claim:

- :func:`ord_encode_run` / :func:`ord_decode_run` -- the *counterfactual* ordinary-base-2
  run-coder (digit values ``{0,1}`` instead of bijective ``{1,2}``); shows the zero-padding
  symmetry that bzip2's design removes. Reachable exactly 1/2 (the Mode-A signature).
- :func:`huffman_lengths` -- a *valid* (Kraft==1) Huffman length vector by the standard merge,
  **not bzip2's exact tie-break**. Only illustrates the payload cost gradient; real trees are
  read separately via :func:`real_tree_lengths`.

**FAITHFUL-BUT-NOT-BYTE-EXACT.** The BWT helpers operate on :func:`zstdct.bzip2_anatomy.bwt_encode`,
a true rotation BWT that round-trips but does *not* match bzip2's exact tie-break. The
structural claims they back (rotation-invariance -> a ~1/n manifold; primitive <=> reachable)
are *tie-break-independent* -- they hold for any rotation BWT, bzip2's included -- but the
specific permutation is not bzip2's.

**M4 cross-substrate synthesis (codec-agnostic).** :func:`length_multiset` / :func:`coarsening_cell`
are the shared coordinate + statistic that test the M3 "counting -> coarsening cell" claim on
*both* substrates (used by ``test_spoonfeed_synthesis.py`` over this module and
:mod:`zstdct.spoonfeed_fse`). :func:`real_coded_group_freqs` is the bzip2-side instrument they need
(PROVEN -- it replays the real decode loop). The synthesis finding: the cell transfers (N=2) only
on the **gauge-invariant** tree shape (the multiset), not the per-symbol length vector M3 used --
which entangles the counting shape with the canonical gauge and so does not coarsen where the gauge
is input-scrambled (bzip2's BWT). See ``findings/spoonfeed_synthesis.md``.

No zstd dependency. Deterministic (``bz2.compress`` is deterministic; every random draw is seeded).
"""

from __future__ import annotations

import bz2
import heapq
import itertools
import math
from collections import Counter
from collections.abc import Sequence
from fractions import Fraction

import numpy as np

from zstdct.bzip2_anatomy import (
    HUFFMAN_GROUP_SIZE,
    RUNA,
    RUNB,
    _BitReader,
    rle2_encode,
)
from zstdct.bzip2_decoder import (
    _build_table,
    _decode_symbol,
    _read_code_lengths,
    _read_selectors,
    _read_symbol_map,
    decode_bz2_instrumented,
)

# ============================================================================
# BWT oracle helpers (Direction 1, BWT stage)
# ============================================================================


def rotations(s: bytes) -> set[bytes]:
    """The set of all cyclic rotations of ``s``."""
    return {s[i:] + s[:i] for i in range(len(s))}


def orbit_len(lf: np.ndarray, start: int) -> int:
    """Length of the cycle containing ``start`` in the LF permutation.

    A real BWT last column has a *single* n-cycle LF (the output is primitive); this is the
    oracle for "is this shuffled last column encoder-reachable?".
    """
    c, row = 0, start
    while True:
        row = int(lf[row])
        c += 1
        if row == start:
            return c


def min_period(b: bytes) -> int:
    """Smallest ``p`` with ``b == b[:p] * (len(b)//p)`` (``len(b)`` if ``b`` is primitive)."""
    n = len(b)
    for p in range(1, n + 1):
        if n % p == 0 and b == b[:p] * (n // p):
            return p
    return n


# ============================================================================
# RLE2 stage: the run-coders sharing one {0,1} codeword space
# ============================================================================


def inverse_rle2(symbols: list[int], eob: int) -> list[int]:
    """Faithful standalone of the decoder's inverse-RLE2 (``bzip2_decoder._decode_block``) +
    the EOB stop: expand each maximal RUNA/RUNB block as a bijective base-2 zero-run, map a
    value symbol ``s -> s-1`` (the encoder shifted nonzero ``v -> v+1``), halt at the FIRST
    EOB. Anchored by ``inverse_rle2 . rle2_encode == identity``."""
    mtf: list[int] = []
    i = 0
    while i < len(symbols):
        s = symbols[i]
        if s == eob:
            break  # decoder halts at the first EOB
        if s == RUNA or s == RUNB:
            run, mult = 0, 1
            while i < len(symbols) and symbols[i] in (RUNA, RUNB):
                run += (1 if symbols[i] == RUNA else 2) * mult  # digit VALUES are {1,2}
                mult <<= 1
                i += 1
            mtf.extend([0] * run)
        else:
            mtf.append(s - 1)
            i += 1
    return mtf


def bij_encode_run(length: int) -> list[int]:
    """Bijective base-2 (bzip2's real coder): reuse ``rle2_encode`` on a pure zero-run, strip
    the trailing EOB. Digits are RUNA/RUNB == ``{0,1}``, VALUES ``{1,2}``."""
    return rle2_encode([0] * length, alphabet_size=2)[:-1]


def bij_decode_run(digits: list[int]) -> int:
    run, mult = 0, 1
    for d in digits:
        run += (1 if d == RUNA else 2) * mult  # value(RUNA)=1, value(RUNB)=2
        mult <<= 1
    return run


def ord_encode_run(length: int) -> list[int]:
    """ILLUSTRATIVE counterfactual: ordinary base-2, same ``{0,1}`` symbols, VALUES ``{0,1}``,
    LSB first. Canonical form drops non-significant high zeros (the loop stops at the MSB) --
    which is exactly the zero-padding symmetry that gives this coder a 1/2 manifold."""
    bits: list[int] = []
    while length > 0:
        bits.append(length & 1)
        length >>= 1
    return bits


def ord_decode_run(digits: list[int]) -> int:
    return sum(d << k for k, d in enumerate(digits))  # value(0)=0, value(1)=1


def run_coder_stats(encode_run, decode_run, kmax: int):
    """Over ALL non-empty ``{0,1}`` digit strings of length <= k: decoded-value coverage and
    the round-trip (encoder-reachable) fraction. Reachable < 1 == a manifold.

    Returns rows ``(k, n_codes, n_distinct, lo, hi, reachable_fraction)``.
    """
    rows = []
    for k in range(1, kmax + 1):
        strings = [list(c) for L in range(1, k + 1) for c in itertools.product((0, 1), repeat=L)]
        decoded = [decode_run(s) for s in strings]
        rt = sum(encode_run(decode_run(s)) == s for s in strings)
        rows.append((k, len(strings), len(set(decoded)), min(decoded), max(decoded), rt / len(strings)))
    return rows


# ============================================================================
# Huffman stage: canonical codes, payload, code-length delta-walk, selectors
# ============================================================================


def pack_bits(bits: list[int]) -> bytes:
    """MSB-first bit packing (pad the final byte with zeros) -> a ``_BitReader``-readable buffer."""
    out = bytearray()
    for i in range(0, len(bits), 8):
        chunk = bits[i:i + 8]
        byte = 0
        for b in chunk:
            byte = (byte << 1) | b
        byte <<= (8 - len(chunk))  # left-align the last partial byte (MSB-first)
        out.append(byte)
    return bytes(out)


def kraft_sum(lengths: Sequence[int]) -> Fraction:
    """Exact Kraft sum ``sum 2^(-len)`` over present (len>0) symbols. ==1 iff complete code."""
    return sum((Fraction(1, 1 << L) for L in lengths if L > 0), Fraction(0))


def canonical_codes(lengths: list[int]) -> dict[int, tuple[int, int]]:
    """``sym -> (code_value, length)``, assigned EXACTLY as ``bzip2_decoder._build_table`` does
    (sweep lengths low->high, symbols in index order; ``code += 1`` per symbol, ``<<= 1`` per
    length). ``code_value >= 2**length`` signals an overfull (Kraft>1) vector -- invalid."""
    max_len = max(lengths)
    by_len: list[list[int]] = [[] for _ in range(max_len + 1)]
    for sym, L in enumerate(lengths):
        if L > 0:
            by_len[L].append(sym)
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    for L in range(1, max_len + 1):
        for sym in by_len[L]:
            codes[sym] = (code, L)
            code += 1
        code <<= 1
    return codes


def encode_payload(symbols: list[int], lengths: list[int]) -> list[int]:
    """Forward of the Huffman payload: each symbol -> its canonical codeword bits (MSB-first).
    Anchored by ``decode_payload(encode_payload(.)) == identity`` against the real decoder."""
    codes = canonical_codes(lengths)
    bits: list[int] = []
    for s in symbols:
        cv, L = codes[s]
        bits.extend((cv >> (L - 1 - k)) & 1 for k in range(L))
    return bits


def decode_payload(bits: list[int], lengths: list[int], n_symbols: int) -> list[int]:
    """Decode ``n_symbols`` via the REAL ``_build_table`` + ``_decode_symbol`` (the anchor)."""
    table = _build_table(lengths)
    br = _BitReader(pack_bits(bits), start_byte=0)
    return [_decode_symbol(br, table) for _ in range(n_symbols)]


def huffman_lengths(freqs: list[int]) -> list[int]:
    """ILLUSTRATIVE: a valid (Kraft==1) canonical code-length vector for the given symbol
    counts via the standard Huffman merge -- **not** bzip2's exact tie-break, but a true
    complete tree (all that's needed to illustrate the bijection / cost gradient). Real trees
    are extracted separately by :func:`real_tree_lengths`."""
    m = len(freqs)
    if m == 1:
        return [1]
    lengths = [0] * m
    heap: list = [(f, i, (i,)) for i, f in enumerate(freqs)]
    heapq.heapify(heap)
    cnt = m
    while len(heap) > 1:
        f1, _, g1 = heapq.heappop(heap)
        f2, _, g2 = heapq.heappop(heap)
        for s in g1 + g2:
            lengths[s] += 1
        heapq.heappush(heap, (f1 + f2, cnt, g1 + g2))
        cnt += 1
    return lengths


def encode_code_lengths(lengths: list[int]) -> list[int]:
    """The encoder's DIRECT delta-walk (bzip2's emission shape): 5-bit seed = ``lengths[0]``,
    then per symbol emit ``|delta|`` monotone +-1 steps (each = continuation 1 + dir bit) + a 0
    stop bit. dir bit: 1 -> -1, 0 -> +1 (matching ``_read_code_lengths``). Anchored by
    round-trip through the real reader.

    NOTE: the decoder accepts ANY +-1 walk reaching each target (a ``+1,-1`` pair is a null
    move) -- this direct walk is the encoder's one representative of that walk-symmetry orbit.
    """
    bits: list[int] = []
    seed = lengths[0]
    bits.extend((seed >> k) & 1 for k in range(4, -1, -1))
    curr = seed
    for L in lengths:
        delta = L - curr
        dir_bit = 1 if delta < 0 else 0
        for _ in range(abs(delta)):
            bits.append(1)        # continuation
            bits.append(dir_bit)  # direction
        bits.append(0)            # stop
        curr = L
    return bits


def encode_selectors(table_ids: list[int], n_groups: int) -> list[int]:
    """Forward of the selector stream: unary-coded MTF index of each table id. Anchored by
    round-trip through the real ``_read_selectors``."""
    mtf = list(range(n_groups))
    bits: list[int] = []
    for t in table_ids:
        j = mtf.index(t)
        bits.extend([1] * j + [0])
        mtf.pop(j)
        mtf.insert(0, t)
    return bits


def real_tree_lengths(data: bytes) -> list[list[int]]:
    """Extract the per-group code-length vectors of block 0 of real ``bz2.compress(data)``,
    using the REAL decoder readers (grounds the Kraft / gauge claims on bzip2's own trees)."""
    comp = bz2.compress(data, 9)
    br = _BitReader(comp, start_byte=4)
    br.read(24); br.read(24)                 # block magic
    br.read(32); br.read_bit(); br.read(24)  # crc, randomized, origPtr
    symbols = _read_symbol_map(br)
    n_symbols = len(symbols) + 2
    n_groups = br.read(3)
    n_selectors = br.read(15)
    _read_selectors(br, n_groups, n_selectors)
    return [_read_code_lengths(br, n_symbols) for _ in range(n_groups)]


def real_coded_group_freqs(data: bytes) -> tuple[list[tuple[list[int], list[int]]], list[int]]:
    """For block 0 of ``bz2.compress(data, 9)``: per Huffman group, the ``(code-length vector,
    coded-symbol frequency vector)`` it actually coded -- by replaying ``_decode_block``'s Huffman
    loop with the REAL decoder readers (so it is anchored to the byte-exact decoder).

    The frequency vector is exactly what that group's tree codes -- the content coordinate for the
    Huffman counting-cell, the bzip2 analog of zstd's literal byte histogram. Unlike zstd (where
    i.i.d. literals ARE the input), bzip2's BWT/MTF/RLE2 sit between the original and what Huffman
    sees, so this reads the coordinate *at the stage* rather than at the input. Returns
    ``(groups, usage)`` with ``groups[g] = (lengths_g, freqs_g)`` and ``usage[g]`` the selector
    count for group g (so ``argmax(usage)`` is the most-used group)."""
    comp = bz2.compress(data, 9)
    br = _BitReader(comp, start_byte=4)
    br.read(24); br.read(24)                 # block magic
    br.read(32); br.read_bit(); br.read(24)  # crc, randomized, origPtr
    symbols = _read_symbol_map(br)
    n_symbols = len(symbols) + 2
    eob = len(symbols) + 1
    n_groups = br.read(3)
    n_selectors = br.read(15)
    selectors = _read_selectors(br, n_groups, n_selectors)
    lengths = [_read_code_lengths(br, n_symbols) for _ in range(n_groups)]
    tables = [_build_table(L) for L in lengths]
    freqs = [[0] * n_symbols for _ in range(n_groups)]
    decoded = group = 0
    cur_g = selectors[0]
    table = tables[cur_g]
    while True:
        if decoded % HUFFMAN_GROUP_SIZE == 0:  # table switches every 50 coded symbols
            cur_g = selectors[group]
            table = tables[cur_g]
            group += 1
        s = _decode_symbol(br, table)
        decoded += 1
        if s == eob:
            break
        freqs[cur_g][s] += 1
    usage = [selectors.count(g) for g in range(n_groups)]
    return [(lengths[g], freqs[g]) for g in range(n_groups)], usage


def gauge_size(lengths: Sequence[int]) -> tuple[int, dict[int, int]]:
    """The ``prod_L (n_L!)`` length-preserving codeword<->symbol relabelings a length vector
    admits -- the gauge the canonical rule fixes. Returns ``(|orbit|, {length: count})``."""
    by_len: dict[int, int] = {}
    for L in lengths:
        if L > 0:
            by_len[L] = by_len.get(L, 0) + 1
    g = 1
    for c in by_len.values():
        g *= math.factorial(c)
    return g, dict(sorted(by_len.items()))


# ============================================================================
# M4 -- cross-substrate synthesis: the counting-cell coordinate (codec-agnostic)
# ============================================================================


def length_multiset(lengths: Sequence[int]) -> tuple:
    """The gauge-invariant tree SHAPE: the sorted multiset of present (``len>0``) code lengths
    ``((length, count), ...)``.

    The per-symbol length vector entangles *two* Mode-A objects -- the **counting** shape (how many
    codewords at each length; the Kraft-constrained part, Finding 2) and the canonical **gauge**
    (which symbol gets which codeword; the ``prod(n_L!)`` relabeling, Finding 3 / :func:`gauge_size`).
    Quotient out the gauge and you have the pure counting coordinate, the right one for the
    coarsening cell: where the symbol<->length assignment is input-stable (zstd i.i.d., symbol rank
    fixed) the per-symbol vector coarsens too, but where it is scrambled (bzip2's BWT permutes which
    symbol is frequent) only the multiset does -- so the multiset is what transfers (the M4 read)."""
    return tuple(sorted(Counter(L for L in lengths if L > 0).items()))


def coarsening_cell(shapes: Sequence, contents: Sequence) -> dict:
    """Group content vectors by the Mode-A *shape* they collapse onto, and return the statistics
    that tell a COUNTING coarsening cell from a SYMMETRY orbit.

    ``shapes[i]`` is the realized Mode-A object (hashable; use the gauge-invariant
    :func:`length_multiset`) and ``contents[i]`` the content coordinate whose collapse is read (the
    frequency/histogram vector that shape codes). A **genuine cell** holds ``>= 2`` *distinct*
    contents -- distinct distributions rounding to one shape, nonzero width -- which is the counting
    signature (the precision is *absorbed*: the exact data rides the Mode-B payload, no re-injecting
    phase). A **symmetry orbit** instead holds one content/multiset repeated, width 0, its members
    separated only by a transmitted phase (e.g. bzip2's ``origPtr``). Returns ``n`` /
    ``distinct_shapes`` / ``biggest_cell`` / ``n_genuine_cells`` / ``best_genuine_cell`` /
    ``best_genuine_distinct`` / ``best_genuine_width`` (the last three over the largest genuine cell)."""
    cells: dict = {}
    for s, c in zip(shapes, contents):
        cells.setdefault(s, []).append(tuple(c))
    genuine = [v for v in cells.values() if len(set(v)) >= 2]
    best = max(genuine, key=len, default=[])
    width = max((abs(a - b) for c in best for a, b in zip(c, best[0])), default=0)
    return {
        "n": sum(len(v) for v in cells.values()),
        "distinct_shapes": len(cells),
        "biggest_cell": max((len(v) for v in cells.values()), default=0),
        "n_genuine_cells": len(genuine),
        "best_genuine_cell": len(best),
        "best_genuine_distinct": len(set(best)),
        "best_genuine_width": width,
    }


# ============================================================================
# Direction 2 helpers: push originals through the real encoder, read the trace
# ============================================================================


def encoder_trace(data: bytes):
    """Push an original through the REAL encoder; read the per-stage shape off the validated
    decode trace. Faithfulness inherited: asserts byte-exact vs ``bz2.decompress``. Returns
    ``(compressed, [BlockDecode])``; ~4 KB inputs are a single block."""
    comp = bz2.compress(data, 9)
    out, traces = decode_bz2_instrumented(comp)
    assert out == data == bz2.decompress(comp), "decode_bz2_instrumented not byte-exact"
    return comp, traces


def structured_input(corrupt_frac: float, *, n: int = 4000, seed: int = 0) -> bytes:
    """A single clean structure knob. Period-20 text over a fixed ~16-symbol alphabet; replace
    ``corrupt_frac`` of positions with uniform draws over the SAME alphabet. ``f=0`` ->
    perfectly periodic (max sequential structure); ``f=1`` -> i.i.d. uniform over the alphabet.
    The alphabet is ~fixed across ``f``, so only the STRUCTURE changes, not ``|A|`` --
    isolating structure from alphabet size."""
    base = b"the quick brown fox "  # 20 bytes, 16 distinct
    alpha = np.frombuffer(bytes(sorted(set(base))), np.uint8)
    rng = np.random.default_rng(seed)
    buf = np.frombuffer((base * (n // len(base) + 1))[:n], np.uint8).copy()
    mask = rng.random(n) < corrupt_frac
    buf[mask] = rng.choice(alpha, size=int(mask.sum()))
    return buf.tobytes()
