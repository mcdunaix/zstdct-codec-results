"""Spoon-feeding LZMA's range coder -- the M1 kill-switch for substrate #3 (the N=3 CROSS-FAMILY test).

Substrates #1 (bzip2, :mod:`zstdct.spoonfeed`) and #2 (zstd, :mod:`zstdct.spoonfeed_fse`) both
transmit a **static model** (a Huffman tree / an FSE table) whose admissible set *is* a Mode-A
**counting** manifold (Kraft ``Sigma 2^-len = 1`` / ``Sigma f = 2^AL``), plus a Mode-B payload coded
against it. That gave N=2 -- but N=2 over *one family* (static-model, integer-table coders). LZMA is
the cross-family test: **LZ77 + an adaptive binary range coder with NO model on the wire** -- the
decoder maintains ~hundreds of adaptive bit-probabilities (``kBitModelTotal = 2048`` fixed-point)
regenerated from its own output. Ground truth is the byte-exact decoder :mod:`zstdct.lzma_decoder`
(validated == stdlib ``lzma`` in M0).

The two-mode picture: a stage either thins the reachable SET (**Mode A**: a manifold from a
*symmetry* or a *counting* constraint) or only grades COST (**Mode B**: a bijection, set full). M1
classifies LZMA's three candidate objects against the **pre-registered** predictions
(``findings/spoonfeed_lzma_milestones.md``, locked in git *before* this module):

  1. the range-coded payload -> **Mode B** (predicted to transfer; N=3),
  2. the final-interval slack -> **Mode-A-by-symmetry**, a relabeling gauge = a literal
     covert channel (predicted to transfer *and sharpen*; N=3),
  3. **Mode-A-by-counting** -> predicted **ABSENT** (no transmitted model => no conservation
     surface; the counting manifold is the static-model family's signature, not universal).

Each forward is tagged by how it is grounded -- the proven-vs-illustrated boundary the prior
substrates established:

**PROVEN (anchored to the exact decoder / real ``lzma``).**

- :func:`real_decode_bit` drives the REAL ``_RangeDecoder.decode_bit``; the returned bit is exactly
  "which sub-interval holds ``code``" -- so the interval split :func:`interval_split` /
  :func:`interval_partition_ok` *is* the decision the real coder makes (Object 1, the per-step
  bijection).
- :func:`range_cost` reads the achieved code length off the REAL instrumented decode of a REAL
  ``lzma.compress`` stream: ``cost_bits = -Sigma log2(interval fraction)``, which obeys the exact
  law ``8*n_renorm + (32 - log2 final_range)`` and lands within a few framing bytes of the
  compressed size (Object 1, Mode-B cost = adaptive entropy).
- :func:`range_decoder_totality` drives the REAL ``_RangeDecoder`` on arbitrary bytes: every
  ``decode_bit`` returns a bit and the range invariant holds -- the entropy engine is TOTAL (Mode B:
  the set stays full). :func:`lz_reference_constraint` shows the *only* partiality is the LZ77
  back-reference rule, a layer ABOVE the range coder (a separate constraint, not an entropy manifold).
- :func:`embed_in_gauge` / :func:`read_gauge` realize the covert channel against the REAL decoder:
  the embedded stream decodes byte-for-byte to the same output, yet carries a chosen payload in its
  trailing slack (Object 2, the gauge as capacity). :func:`trailing_gauge_bits` /
  :func:`gauge_block_invariant` *measure* that slack via the real decoder (the round-trip oracle).
- :func:`range_invariant_min` reads the smallest range entering a decision off the real trace
  (Object 3, the candidate counting "reappearance" -- measured, then correctly classified as a
  decoder-internal maintained inequality, not a transmitted conservation surface).

**STRUCTURAL (a fact about the format / the decoder source, measured not asserted).**

- :func:`stream_layout` -- the stream after the 13-byte header is *all* range-coded payload: **0
  model bytes** (contrast bzip2's transmitted trees / zstd's transmitted FSE tables + weights).
- :func:`initial_model` -- every adaptive probability starts at ``_PROB_INIT = 1024`` (= half of
  2048, max entropy); the model is *regenerated from output*, never read from the wire. There is no
  encoder-chosen object to place on a counting surface -> Mode-A-by-counting has no analog here.

The M2 additions (Direction-1 mapping: promote the per-bit bijection to a real *stream* via a
streaming range **encoder** -- the inverse of ``_RangeDecoder``; same writeup
``findings/spoonfeed_lzma_milestones.md``):

- :class:`RangeEncoder` -- the streaming range encoder. The carry-cache :meth:`RangeEncoder._shift_low`
  is the genuinely new mechanic (no bzip2/zstd analog: the FSE encoder had no carry). The "model" is
  not transmitted -- :meth:`RangeEncoder.encode_bit` co-regenerates the identical adaptive prob the
  decoder does, the cross-family essence made operational.
- :func:`range_encode_bits` / :func:`range_decode_bits` -- the per-bit Mode-B bijection promoted to a
  bitstream round-trip == identity against the REAL ``_RangeDecoder.decode_bit`` (the analog of FSE's
  ``fse_encode_stream`` / ``fse_decode_stream``, but the table is adaptive, not transmitted).
- :func:`reencode_lzma_alone` -- the strongest anchor: replays a *real* stream's decision tape and
  reproduces ``lzma.compress``'s payload **byte-for-byte** (the full LZMA range encoder, EOS marker
  and carry-cache included), so the per-bit bijection composes into the whole stream.
- :func:`final_interval` / :func:`encoder_gauge_bits` / :func:`reencode_with_offset` -- the gauge
  (Object 2) measured *from the encoder side*: the final interval ``[low, low + range)`` directly
  (``range`` == the decoder's recovered ``final_range``), and the representative-freedom demonstrated
  by construction (re-flush a different number in the interval -> same output, same length).

The M3 additions (Direction 2 = encoder / image+preimage: push real originals through
``lzma.compress`` and read the realized shapes off the validated decode trace; same writeup
``findings/spoonfeed_lzma_milestones.md``):

- :func:`encoder_trace_lzma` -- the Direction-2 instrument (the analog of zstd's
  ``encoder_trace_zstd`` / bzip2's ``encoder_trace``): compress an original with the REAL ``lzma``,
  decode it instrumented, and *assert byte-exact* vs both the input and ``lzma.decompress`` -- so
  every shape read off the returned ``LzmaTrace`` is one the real encoder produced.
- :func:`op_mix` -- the image, all stages off ONE trace: the realized LZ-decision shape
  (literals / matched-literals / matches / reps / short-reps + the literal/match fractions). Over a
  structure sweep the mix shifts monotonically (matches -> literals as structure dies) -- the bzip2
  D2 Finding-1 analog, on LZMA's decision stream.
- :func:`gauge_orbit` -- the **preimage** of an output under the Mode-A-by-symmetry gauge: ``m``
  distinct, equal-length streams (the encoder's free choice of representative number, via
  :func:`reencode_with_offset`) that ALL decode to the same output. Fed to the codec-agnostic
  :func:`zstdct.spoonfeed.coarsening_cell` it reads as a SYMMETRY ORBIT (fat: ``biggest_cell == m``;
  width 0: ``n_genuine_cells == 0``) -- the SAME code that read zstd's counting CELL (width > 0),
  opposite verdict. The M3 reading: the preimage mirrors the mode (Mode-B payload thin / bijection,
  Mode-A gauge fat) and its *shape* mirrors the cause -- here a symmetry **orbit**, not a counting
  cell, because LZMA transmits no fitted-model object for distinct inputs to coarsen onto
  (Object 3 absent, re-confirmed from the preimage side). The **adaptive-model caveat**: the
  payload bijection is "given the model trajectory", which is itself a deterministic function of the
  input -- so there is no input-independent model object whose preimage could be a counting cell.

No third-party dependency beyond the decoder (``lzma`` is stdlib; ``numpy`` only for the random
batteries). Deterministic (every draw is seeded; ``lzma.compress`` is deterministic).
"""

from __future__ import annotations

import math

import numpy as np

from zstdct.lzma_decoder import (
    _BIT_MODEL_TOTAL,
    _K_TOP,
    _MASK32,
    _MOVE_BITS,
    _PROB_INIT,
    _RangeDecoder,
    compress_alone,
    decode_lzma_alone,
    decode_lzma_alone_instrumented,
    decode_lzma_alone_taped,
)

_HEADER_BYTES = 13  # FORMAT_ALONE: props(1) + dict_size(4) + unpack_size(8)


def _safe_decode(comp: bytes) -> bytes | None:
    """Decode, treating any failure as "output changed". The range coder is total, but the LZ77
    layer can emit an out-of-range back-reference on a corrupted stream (an ``IndexError``) -- that
    is the LZ layer's referential constraint, not the entropy coder's (see
    :func:`lz_reference_constraint`)."""
    try:
        return decode_lzma_alone(comp)
    except (IndexError, ValueError):
        return None


# ============================================================================
# Object 1 -- the range-coded payload = Mode B (a bijection given the model; cost = entropy)
# ============================================================================


def interval_split(prob: int, range_: int) -> tuple[int, int]:
    """The two sub-interval widths the real ``decode_bit`` carves ``[0, range_)`` into:
    ``(bound, range_ - bound)`` with ``bound = (range_ >> 11) * prob`` -- bit 0 takes ``[0, bound)``,
    bit 1 takes ``[bound, range_)``. The exact constants of :func:`zstdct.lzma_decoder._RangeDecoder.decode_bit`."""
    bound = (range_ >> 11) * prob
    return bound, range_ - bound


def interval_partition_ok(prob: int, range_: int) -> bool:
    """True iff the two sub-intervals exactly TILE ``[0, range_)`` -- both non-empty, no gap, no
    overlap. This exactness (the last symbol gets the *remainder* ``range_ - bound``, not a second
    rounded share) is the per-bit bijection: ``code in [0, range_)`` <-> ``(bit, position within the
    chosen sub-interval)``, lossless. It is the range-coder analog of FSE's ``symbol_partition_ok``
    (whose cells tile ``[0, 2^AL)``). Holds for every ``prob in [1, 2047]`` over any in-range
    ``range_`` -- a structural fact about the real arithmetic, no rounding slack."""
    lo, hi = interval_split(prob, range_)
    return lo > 0 and hi > 0 and lo + hi == range_


def real_decode_bit(prob: int, range_: int, code: int) -> int:
    """Drive the REAL ``_RangeDecoder.decode_bit`` with a chosen ``(prob, range_, code)`` and return
    the decoded bit -- the anchor that grounds :func:`interval_partition_ok` on the real decoder. The
    decision is made *before* normalization, so the returned bit is exactly "which sub-interval holds
    ``code``": ``0`` iff ``code < bound``. (Requires ``range_ >= 2^24``; the crafted buffer feeds
    normalization zero bytes, which never affects the already-decided bit.)"""
    rc = _RangeDecoder(b"\x00" * 16, 0)  # init reads a 0 byte + 4 code bytes; we override below
    rc.range = range_
    rc.code = code
    return rc.decode_bit([prob], 0)


def range_cost(comp: bytes) -> dict:
    """The achieved code length, read off the REAL instrumented decode of ``comp``:
    ``cost_bits = -Sigma log2(actual interval fraction)`` accumulated over every coded decision.

    Returns ``cost_bits`` / ``n_renorm`` / ``stream_bytes`` / ``final_range`` / ``compressed_bits``
    (``= 8 * stream_bytes``) / ``law`` (``= 8*n_renorm + (32 - log2 final_range)``). The Mode-B
    reading: ``cost_bits == law`` (exact, telescoping ``range``), ``0 < cost_bits - 8*n_renorm <= 8``
    (the final-interval residue), and ``cost_bits`` is within a few framing bytes of
    ``compressed_bits`` -- i.e. the range coder spends exactly the bits the adaptive model says
    (near-optimal). Cost only; no membership constraint (the set stays full -- see
    :func:`range_decoder_totality`)."""
    _out, tr = decode_lzma_alone_instrumented(comp)
    law = 8 * tr.n_renorm + (32 - math.log2(tr.final_range))
    return {
        "cost_bits": tr.cost_bits,
        "n_renorm": tr.n_renorm,
        "stream_bytes": tr.stream_bytes,
        "final_range": tr.final_range,
        "compressed_bits": 8 * tr.stream_bytes,
        "law": law,
    }


def range_decoder_totality(seed: int, n_calls: int = 3000, n_contexts: int = 16) -> list[int]:
    """Drive the REAL ``_RangeDecoder`` on ARBITRARY bytes: every ``decode_bit`` returns a valid bit
    and the range invariant ``range >= 2^24`` holds after each step. The entropy engine is TOTAL --
    any bitstream decodes (Mode B: the reachable set is full, no manifold). Returns the decoded bits.

    (Asserts internally; raises ``AssertionError`` if totality or the invariant ever broke -- which
    it does not, by construction of the renormalizing range decoder.)"""
    rng = np.random.default_rng(seed)
    data = b"\x00" + bytes(rng.integers(0, 256, n_calls + 64, dtype=np.uint8))  # init byte must be 0
    rc = _RangeDecoder(data, 0)
    probs = [_PROB_INIT] * n_contexts
    bits: list[int] = []
    for i in range(n_calls):
        b = rc.decode_bit(probs, i % n_contexts)
        assert b in (0, 1)
        assert rc.range >= _K_TOP  # the renorm invariant is maintained for ANY input
        bits.append(b)
    return bits


def lz_reference_constraint(seed: int, trials: int = 300, body: int = 64, size: int = 32) -> tuple[int, int]:
    """Feed random FULL ``FORMAT_ALONE`` streams (valid 13-byte header, init byte 0, known ``size``)
    to the composite decoder and count ``(ok, fail)``. A fraction fail with an out-of-range
    back-reference: that is **LZ77's referential constraint** (a match distance must point within the
    output produced so far), a layer ABOVE the (total) range coder. So any thinning of the
    full-stream set belongs to the LZ layer, NOT the entropy coder -- Mode B holds for the range
    coder itself. Both ``ok`` and ``fail`` are nonzero (the range coder never errors; the LZ rule
    sometimes does)."""
    rng = np.random.default_rng(seed)
    props = bytes([93])  # lc=3 lp=0 pb=2
    head = props + (1 << 16).to_bytes(4, "little") + size.to_bytes(8, "little")
    ok = fail = 0
    for _ in range(trials):
        stream = b"\x00" + bytes(rng.integers(0, 256, body, dtype=np.uint8))
        if _safe_decode(head + stream) is None:
            fail += 1
        else:
            ok += 1
    return ok, fail


# ============================================================================
# Object 2 -- the final-interval slack = Mode-A-by-symmetry (a relabeling gauge = covert channel)
# ============================================================================


_GAUGE_WINDOW = 24  # the final-interval slack lives in the last ~3 bytes (the 5-byte flush region)


def _bit_get(buf: bytes, bit_from_end: int) -> int:
    byte_idx = len(buf) - 1 - (bit_from_end >> 3)
    return (buf[byte_idx] >> (bit_from_end & 7)) & 1


def _bit_set(buf: bytearray, bit_from_end: int, value: int) -> None:
    byte_idx = len(buf) - 1 - (bit_from_end >> 3)
    mask = 1 << (bit_from_end & 7)
    buf[byte_idx] = (buf[byte_idx] & ~mask) | ((value & 1) << (bit_from_end & 7))


def gauge_free_positions(comp: bytes, window: int = _GAUGE_WINDOW) -> list[int]:
    """The trailing stream-bit positions (from-end indices, ascending) that are output-invariant --
    flipping any one alone leaves the decode unchanged. Measured via the REAL decoder (the round-trip
    oracle: the manifold is not syntactic, like BWT). These are the bits of the final code value that
    fall below the interval resolution -- the encoder must emit some number in ``[low, low + range)``
    and picks one, so the sub-interval bits are free. (Byte-MSB-first packing scatters them through
    the last bytes rather than into a clean trailing run, but they are *jointly* free -- see
    :func:`gauge_block_invariant`; the encoder picks one representative of this orbit.) ``window``
    bounds the scan to the final bytes, so the count is a conservative lower bound on the full orbit."""
    base = decode_lzma_alone(comp)
    scan = min(window, (len(comp) - _HEADER_BYTES) * 8)
    free: list[int] = []
    for k in range(scan):
        flipped = bytearray(comp)
        _bit_set(flipped, k, _bit_get(comp, k) ^ 1)
        if _safe_decode(bytes(flipped)) == base:
            free.append(k)
    return free


def trailing_gauge_bits(comp: bytes, window: int = _GAUGE_WINDOW) -> int:
    """The gauge capacity: the number of (jointly) free trailing bits = ``len(gauge_free_positions)``
    -- the covert-channel capacity in bits, equivalently ``log2`` of the orbit of same-length streams
    that decode to this output. Tracks the final ``range`` (the interval freedom); a conservative
    lower bound (windowed to the last bytes)."""
    return len(gauge_free_positions(comp, window))


def gauge_block_invariant(comp: bytes, positions: list[int] | None = None, trials: int = 64) -> bool:
    """Set ALL the free positions to many RANDOM values at once; True iff every assignment decodes to
    the same output. Promotes the per-bit measurement to a genuine joint capacity (the slack carries
    ``2^len(positions)`` distinct streams, all one message) -- the relabeling-symmetry signature."""
    if positions is None:
        positions = gauge_free_positions(comp)
    base = decode_lzma_alone(comp)
    for t in range(trials):
        rng = np.random.default_rng(7919 + t)
        flipped = bytearray(comp)
        for k in positions:
            _bit_set(flipped, k, int(rng.integers(0, 2)))
        if _safe_decode(bytes(flipped)) != base:
            return False
    return True


def embed_in_gauge(comp: bytes, message_bits: list[int], positions: list[int] | None = None) -> bytes:
    """Write ``message_bits`` into the gauge's free positions and return the modified stream. It
    decodes byte-for-byte to the SAME output as ``comp`` (the cover), yet carries the message -- a
    literal covert channel the standard decoder ignores. Anchored by
    ``decode_lzma_alone(embed_in_gauge(c, m)) == decode_lzma_alone(c)`` and
    ``read_gauge(embed_in_gauge(c, m), pos) == m`` where ``pos = gauge_free_positions(c)`` (the
    position schedule is shared sender/receiver knowledge, derived from the cover -- it is the
    decode-invariant free set, not recoverable from the stego stream alone). Raises if the message
    exceeds capacity."""
    if positions is None:
        positions = gauge_free_positions(comp, window=max(_GAUGE_WINDOW, len(message_bits) + 8))
    if len(message_bits) > len(positions):
        raise ValueError(f"message ({len(message_bits)} bits) exceeds gauge capacity ({len(positions)} bits)")
    out = bytearray(comp)
    for bit, k in zip(message_bits, positions):
        _bit_set(out, k, bit)
    return bytes(out)


def read_gauge(stream: bytes, positions: list[int], k: int | None = None) -> list[int]:
    """Read the covert payload back from ``stream`` at the shared ``positions`` schedule (the
    cover's :func:`gauge_free_positions`) -- the bits :func:`embed_in_gauge` wrote. Reads ``k`` bits
    (default: all of ``positions``)."""
    if k is None:
        k = len(positions)
    return [_bit_get(stream, positions[i]) for i in range(k)]


# ============================================================================
# Object 3 -- Mode-A-by-counting: ABSENT (no transmitted model -> no conservation surface)
# ============================================================================


def stream_layout(comp: bytes) -> dict:
    """The byte budget of a ``FORMAT_ALONE`` stream: ``header_bytes`` (always 13: props + dict_size
    + unpack_size) and ``payload_bytes`` (the rest) -- with ``model_bytes = 0``.

    There is no *model section*: everything after the header is range-coded LZ77 tokens. The header's
    ``lc/lp/pb`` select the context *partitioning* (a hyperparameter, like a tree depth), not a
    fitted distribution; ``dict_size`` / ``unpack_size`` are scalars. So the encoder transmits **zero
    bytes of model** -- contrast bzip2 (per-block Huffman trees) and zstd (FSE tables + Huffman
    weights), whose transmitted model *is* the Mode-A counting manifold. With nothing on a
    conservation surface to send, Mode-A-by-counting has no analog (the pre-registered prediction)."""
    return {
        "total_bytes": len(comp),
        "header_bytes": _HEADER_BYTES,
        "payload_bytes": len(comp) - _HEADER_BYTES,
        "model_bytes": 0,
    }


def initial_model(lc: int = 3, lp: int = 0) -> dict:
    """The decoder's starting probability model for ``(lc, lp)``: ``n_probs`` adaptive
    bit-probabilities, ALL initialized to ``_PROB_INIT = 1024`` (= half of 2048 = maximum entropy,
    "no information"). ``distinct_init_values == 1``: the model carries zero information at the start
    and is *regenerated from the decoded output*, never read from the wire. Returns ``n_probs`` /
    ``init_value`` / ``distinct_init_values`` / ``bit_model_total``. (``pb`` only masks which
    pos-state contexts are used; it does not change the allocated probability count.)

    This is the structural reason Object 3 is absent: there is no encoder-chosen model object whose
    admissible set could be a counting surface -- the model is an emergent function of the data, not a
    transmitted parameter."""
    # the literal sub-model dominates the count; the rest are the LZ-decision contexts (fixed sizes)
    n_literal = 0x300 << (lc + lp)
    n_other = (
        (12 << 4)      # is_match
        + 12 * 4       # is_rep / is_rep_g0 / is_rep_g1 / is_rep_g2
        + (12 << 4)    # is_rep0_long
        + 4 * 64       # pos_slot
        + 115          # spec_pos
        + 16           # align
        + 2 * (2 + 16 + 16 + 256)  # len + rep_len coders (choice, choice2, low, mid, high)
    )
    return {
        "n_probs": n_literal + n_other,
        "init_value": _PROB_INIT,
        "distinct_init_values": 1,
        "bit_model_total": 1 << 11,
    }


def range_invariant_min(comp: bytes) -> int:
    """The smallest ``range`` seen entering a coded decision during the real decode of ``comp``. The
    renormalizer keeps ``range in [2^24, 2^32)``, so this is ``>= 2^24 = 16777216`` -- the candidate
    site where Mode-A-by-counting might "reappear" (the pre-reg flagged it).

    But it does NOT: this is a single inequality *maintained automatically by the decoder*, not an
    encoder-chosen object placed on a ``Sigma = const`` surface and transmitted. It is the range
    coder's working-precision band, the counterpart of ANS's state range -- decoder-internal, not on
    the wire. So the counting manifold stays the static-model family's signature (N=2, scoped), and
    LZMA is a third regime: cost (Mode B) + gauge (Mode-A-symmetry), no counting."""
    _out, tr = decode_lzma_alone_instrumented(comp)
    return tr.min_range


# ============================================================================
# M2 -- Direction 1: promote the per-bit bijection to a real range-coded STREAM
#   (a streaming range ENCODER, the exact inverse of _RangeDecoder; the carry-cache
#    shift_low is the genuinely new mechanic -- no bzip2/zstd analog. The "model" is
#    NOT on the wire: encoder and decoder co-regenerate the identical adaptive prob
#    from the identical bit sequence -- the cross-family essence, made operational.)
# ============================================================================


class RangeEncoder:
    """The streaming binary range **encoder** -- the exact inverse of
    :class:`zstdct.lzma_decoder._RangeDecoder`. M1 proved ``decode_bit`` is a per-step bijection (the
    interval split tiles ``[0, range)`` exactly); this promotes it to a real byte *stream*. The
    arithmetic mirrors the decoder's (``bound = (range >> 11) * prob``, the same normalize trigger),
    so a sequence of encodes round-trips through the real decoder. The genuinely new mechanic with no
    bzip2/zstd analog is the **carry-cache** :meth:`_shift_low`: the decoder's ``normalize`` reads one
    byte, but the encoder must emit lazily -- a later interval addition can ripple a carry up through
    already-pending bytes, so a pending byte (and a run of ``0xFF``) is held until the carry resolves.

    Anchored end to end by :func:`reencode_lzma_alone`, which replays a *real* stream's decision tape
    and reproduces ``lzma.compress``'s payload byte-for-byte."""

    def __init__(self) -> None:
        self.low = 0  # unbounded int, mirrors the reference UInt64 (a carry out of bit 31 is kept)
        self.range = _MASK32
        self.cache = 0
        self.cache_size = 1
        self.out = bytearray()

    def _shift_low(self) -> None:
        """Emit the top byte of ``low`` lazily, resolving carries. If the pending byte cannot carry
        (``low < 0xFF000000``) or a carry just occurred (``low >> 32``), flush the cache (plus any
        deferred ``0xFF`` run, each += the carry); otherwise hold it (``cache_size += 1``) because a
        future addition might still ripple through. Then drop the byte: ``low = (low << 8) mod 2^32``."""
        if (self.low >> 32) != 0 or self.low < 0xFF000000:
            carry = self.low >> 32
            temp = self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if self.cache_size == 0:
                    break
            self.cache = (self.low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (self.low << 8) & _MASK32

    def encode_bit_raw(self, prob: int, bit: int) -> None:
        """Encode one bit against an explicit ``prob`` (no model update) -- the inverse of
        ``_RangeDecoder.decode_bit``'s arithmetic. Same split ``bound = (range >> 11) * prob``: bit 0
        keeps ``[0, bound)``; bit 1 takes ``[bound, range)`` and advances ``low``. Then the same
        single normalize (``while`` here, but the invariant makes it run once, like the decoder's
        ``if``)."""
        bound = (self.range >> 11) * prob
        if bit == 0:
            self.range = bound
        else:
            self.low += bound
            self.range -= bound
        while self.range < _K_TOP:
            self.range = (self.range << 8) & _MASK32
            self._shift_low()

    def encode_bit(self, probs: list[int], idx: int, bit: int) -> None:
        """Encode one bit against the ADAPTIVE model at ``probs[idx]`` and update it EXACTLY as the
        decoder does (``prob += (2048 - prob) >> 5`` on 0, ``prob -= prob >> 5`` on 1). Encoder and
        decoder regenerate the identical model from the identical bit sequence -- the cross-family
        point: there is no transmitted table (Object 3), the model is co-derived on both sides."""
        prob = probs[idx]
        self.encode_bit_raw(prob, bit)
        if bit == 0:
            probs[idx] = prob + ((_BIT_MODEL_TOTAL - prob) >> _MOVE_BITS)
        else:
            probs[idx] = prob - (prob >> _MOVE_BITS)

    def encode_direct_bits(self, value: int, num: int) -> None:
        """Encode ``num`` equiprobable bits of ``value`` (MSB first) -- the inverse of
        ``_RangeDecoder.decode_direct_bits`` (the fixed ``p = 1/2`` split LZMA uses for the high
        distance bits). Each bit halves ``range`` and conditionally advances ``low``."""
        for i in range(num - 1, -1, -1):
            self.range >>= 1
            self.low += self.range & (0 - ((value >> i) & 1))
            while self.range < _K_TOP:
                self.range = (self.range << 8) & _MASK32
                self._shift_low()

    def replay(self, tape: list) -> "RangeEncoder":
        """Replay a decision tape (from :func:`zstdct.lzma_decoder.decode_lzma_alone_taped`):
        ``(prob, bit)`` -> :meth:`encode_bit_raw`, ``("d", value, num)`` -> :meth:`encode_direct_bits`.
        Returns ``self`` for chaining. (Replays with the *recorded* probs, so no parallel model is
        needed -- the decoder already did the adaptation.)"""
        for ev in tape:
            if len(ev) == 3:  # ("d", value, num)
                self.encode_direct_bits(ev[1], ev[2])
            else:  # (prob, bit)
                self.encode_bit_raw(ev[0], ev[1])
        return self

    def flush(self, offset: int = 0) -> None:
        """Emit the final 5 bytes. ``offset`` (in ``[0, range)``) chooses WHICH number in the final
        interval ``[low, low + range)`` to emit -- the gauge freedom (Object 2); ``offset = 0`` picks
        ``low`` itself, reproducing the reference encoder."""
        self.low += offset
        for _ in range(5):
            self._shift_low()


def range_encode_bits(bits: list[int], contexts: list[int] | None = None, n_contexts: int = 1) -> bytes:
    """Encode a bit sequence into a real range-coded byte stream against a shared ADAPTIVE model
    (``contexts[i]`` selects which of ``n_contexts`` probabilities codes bit ``i``; default: one
    context). Promotes M1's per-step bijection (:func:`interval_partition_ok`) to a STREAM -- the
    bits coded earlier shrink the interval the later bits live in, and the carry-cache flush
    serializes the final number. The inverse of :func:`range_decode_bits`; anchored by
    ``range_decode_bits(range_encode_bits(bits, ctx, n), ctx, len(bits), n) == bits`` against the
    REAL ``_RangeDecoder`` (the analog of FSE's ``fse_encode_stream`` / ``fse_decode_stream``, but
    the "table" here is adaptive, not transmitted)."""
    if contexts is None:
        contexts = [0] * len(bits)
    enc = RangeEncoder()
    probs = [_PROB_INIT] * n_contexts
    for bit, ctx in zip(bits, contexts):
        enc.encode_bit(probs, ctx, bit)
    enc.flush()
    return bytes(enc.out)


def range_decode_bits(stream: bytes, contexts: list[int], k: int, n_contexts: int = 1) -> list[int]:
    """Decode ``k`` bits from ``stream`` via the REAL ``_RangeDecoder.decode_bit`` (the anchor),
    regenerating the SAME adaptive model from the SAME ``contexts`` schedule. The known length ``k``
    isolates the range-coder bijection from LZMA's LZ-decision layer -- the analog of FSE's
    known-length ``fse_decode_stream`` (and bzip2's ``decode_payload`` running the real
    ``_decode_symbol`` ``n`` times)."""
    rc = _RangeDecoder(stream, 0)
    probs = [_PROB_INIT] * n_contexts
    return [rc.decode_bit(probs, contexts[i]) for i in range(k)]


def reencode_lzma_alone(comp: bytes) -> bytes:
    """Re-encode a real ``FORMAT_ALONE`` stream from its OWN decision tape and return the result.

    Decodes ``comp`` to capture every range-coder decision (:func:`decode_lzma_alone_taped`), replays
    that tape through :class:`RangeEncoder`, and re-attaches the 13-byte header. The result is
    ``== comp`` **byte-for-byte** across the battery -- literals, matched-literals, matches, reps,
    lengths, distances, the EOS marker, and the carry-cache flush all reproduced -- so
    :class:`RangeEncoder` is the EXACT inverse of the real range decoder at full LZMA fidelity, not
    merely on synthetic bits. This is the strongest Direction-1 anchor: the encoder reproduces
    ``lzma.compress``'s payload, proving the per-bit Mode-B bijection composes into the whole stream
    (cost only; no membership constraint)."""
    _out, tape = decode_lzma_alone_taped(comp)
    enc = RangeEncoder().replay(tape)
    enc.flush()
    return comp[:_HEADER_BYTES] + bytes(enc.out)


def final_interval(comp: bytes) -> tuple[int, int]:
    """The range coder's final interval ``(low, range)`` read off the ENCODER side: replay ``comp``'s
    tape and return ``(enc.low, enc.range)`` BEFORE the flush. The encoder must emit some number in
    ``[low, low + range)`` and picks one, so the interval width ``range`` IS the gauge (Object 2)
    measured *from the encoder* -- the final interval directly, not by decoder-side bit-flipping.
    Anchored: ``range`` equals the decoder's recovered ``LzmaTrace.final_range`` exactly (the same
    slack, both sides)."""
    _out, tape = decode_lzma_alone_taped(comp)
    enc = RangeEncoder().replay(tape)
    return enc.low, enc.range


def encoder_gauge_bits(comp: bytes) -> float:
    """The gauge capacity from the encoder side: ``log2`` of the final interval width
    (``log2(final_interval(comp)[1])``), in ``[24, 32)``. This is the FULL relabeling freedom -- the
    encoder's free choice of representative number -- of which M1's windowed, decoder-side
    :func:`trailing_gauge_bits` is a conservative lower bound. The same slack, two readings:
    interval-width here, individually-free trailing stream bits there."""
    _low, range_ = final_interval(comp)
    return math.log2(range_)


def reencode_with_offset(comp: bytes, offset: int) -> bytes:
    """Re-encode ``comp`` but emit the representative ``low + offset`` of the final interval (instead
    of the standard ``low``). For any ``offset`` in ``[0, final range)`` the result decodes to the
    SAME output as ``comp`` and is the SAME length, differing only in the trailing bytes -- the gauge
    freedom demonstrated by CONSTRUCTION from the encoder (the complement of M1's
    :func:`gauge_free_positions`, which had to search for it from the decoder side). ``offset = 0``
    returns exactly :func:`reencode_lzma_alone`."""
    _out, tape = decode_lzma_alone_taped(comp)
    enc = RangeEncoder().replay(tape)
    enc.flush(offset)
    return comp[:_HEADER_BYTES] + bytes(enc.out)


# ============================================================================
# M3 -- Direction 2 (encoder / image+preimage): push real originals through lzma.compress
#   and read the realized shapes off the VALIDATED decode trace. The sharp question:
#   does the preimage mirror the mode here -- and does its shape mirror the cause? With
#   NO transmitted model (Object 3 absent), there is no counting object whose preimage is a
#   coarsening CELL; the fat preimage is the Mode-A-by-symmetry gauge ORBIT instead.
# ============================================================================


def encoder_trace_lzma(data: bytes, preset: int = 6, *, lc: int = 3, lp: int = 0, pb: int = 2):
    """Push an original through the REAL ``lzma`` encoder and read every stage's realized shape off
    the validated decode trace -- the Direction-2 instrument, the analog of zstd's
    :func:`zstdct.spoonfeed_fse.encoder_trace_zstd` and bzip2's :func:`zstdct.spoonfeed.encoder_trace`.
    Faithfulness is *inherited*: it asserts the instrumented decode equals BOTH ``data`` and the
    stdlib ``lzma.decompress`` (the M0 anchor), so every shape read off the returned :class:`LzmaTrace`
    is a shape the real encoder actually produced. Returns ``(compressed, LzmaTrace)``."""
    import lzma  # local: the reference encoder/decoder (stdlib), like zstd's local ``zstandard``

    comp = compress_alone(data, preset=preset, lc=lc, lp=lp, pb=pb)
    out, tr = decode_lzma_alone_instrumented(comp)
    assert out == data == lzma.decompress(comp, format=lzma.FORMAT_ALONE), (
        "encoder_trace_lzma: instrumented decode is not byte-exact"
    )
    return comp, tr


def op_mix(tr) -> dict:
    """The image, all stages off ONE trace: the realized LZ-decision shape of a :class:`LzmaTrace`.

    Returns the per-kind counts (``n_literals`` / ``n_matched_literals`` / ``n_matches`` / ``n_reps``
    / ``n_short_reps``), the total ``n_tokens``, and the ``literal_frac`` / ``match_frac`` (a
    matched-literal counts as a literal; a short-rep as a match). Over a structure sweep
    (:func:`zstdct.spoonfeed.structured_input`, ``f: 0 -> 1``) the mix shifts monotonically --
    match-dominated when periodic (LZ77 finds long repeats) to literal-dominated when i.i.d. (nothing
    to match) -- the bzip2/zstd D2 Finding-1 analog (one structure knob, every stage's shape read off
    one trace). The range-coder stage is read separately by :func:`range_cost` / :func:`encoder_gauge_bits`."""
    lit = tr.n_literals + tr.n_matched_literals
    mat = tr.n_matches + tr.n_reps + tr.n_short_reps
    n = lit + mat
    return {
        "n_literals": tr.n_literals,
        "n_matched_literals": tr.n_matched_literals,
        "n_matches": tr.n_matches,
        "n_reps": tr.n_reps,
        "n_short_reps": tr.n_short_reps,
        "n_tokens": n,
        "literal_frac": lit / n if n else 0.0,
        "match_frac": mat / n if n else 0.0,
    }


def gauge_orbit(comp: bytes, m: int = 8) -> list[bytes]:
    """The **preimage** of ``comp``'s output under the Mode-A-by-symmetry gauge: ``m`` distinct,
    equal-length streams that ALL decode to the same output. The encoder must emit some number in the
    final interval ``[low, low + range)`` and picks one (``offset = 0``); each of ``m`` offsets spread
    across ``[0, range)`` emits a different representative (via :func:`reencode_with_offset`), so the
    orbit is the encoder's relabeling freedom realized by construction. ``orbit[0] == comp``.

    This is the symmetry-ORBIT constructor for the M3 preimage read: fed to the codec-agnostic
    :func:`zstdct.spoonfeed.coarsening_cell` (as ``shapes = contents = the decoded output``) it reads
    as an orbit -- ``biggest_cell == m`` (FAT preimage) and ``n_genuine_cells == 0`` / width 0 (one
    output, no distinct-content collapse) -- the SAME statistic that read zstd's counting CELL
    (width > 0), opposite verdict. Contrast a counting cell (distinct distributions rounding to one
    transmitted model object): LZMA has none (Object 3 absent), so the fat preimage is this orbit."""
    _low, range_ = final_interval(comp)
    offsets = [i * range_ // m for i in range(m)]  # spread across [0, range); offset 0 == comp
    return [reencode_with_offset(comp, off) for off in offsets]


# convenience re-exports for the toy / tests (the anchor encoder + decoder)
__all__ = [
    "compress_alone",
    "decode_lzma_alone",
    "decode_lzma_alone_taped",
    "interval_split",
    "interval_partition_ok",
    "real_decode_bit",
    "range_cost",
    "range_decoder_totality",
    "lz_reference_constraint",
    "gauge_free_positions",
    "trailing_gauge_bits",
    "gauge_block_invariant",
    "embed_in_gauge",
    "read_gauge",
    "stream_layout",
    "initial_model",
    "range_invariant_min",
    "RangeEncoder",
    "range_encode_bits",
    "range_decode_bits",
    "reencode_lzma_alone",
    "final_interval",
    "encoder_gauge_bits",
    "reencode_with_offset",
    "encoder_trace_lzma",
    "op_mix",
    "gauge_orbit",
]
