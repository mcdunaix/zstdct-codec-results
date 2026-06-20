"""A byte-exact LZMA decoder -- ground truth for spoon-feed substrate #3 (the range coder).

Substrates #1 (bzip2, :mod:`zstdct.bzip2_decoder`) and #2 (zstd, :mod:`zstdct.zstd_decoder`) both
transmit a **static model** (a Huffman tree / an FSE table) that *is* a Mode-A counting manifold,
plus a Mode-B payload. LZMA is the **cross-family** test: an LZ77 front end + an **adaptive binary
range coder** with **no model on the wire** -- the decoder maintains ~hundreds of adaptive
bit-probabilities (``kBitModelTotal = 2048`` fixed-point) regenerated from its own output. This
module is the M0 gate: a faithful port of the LZMA reference decoder (Igor Pavlov's ``LzmaSpec.cpp``)
for the legacy ``FORMAT_ALONE`` container, validated byte-exact against the stdlib ``lzma`` (liblzma).

``FORMAT_ALONE`` (.lzma) layout: a 13-byte header -- ``props`` (``= (pb*5 + lp)*9 + lc``), a
little-endian ``uint32`` dict size, a little-endian ``uint64`` uncompressed size
(``0xFFFF...FF`` = unknown ⇒ the stream ends with the end-of-stream marker) -- then the raw LZMA
stream. Reads past the logical end normalize as zero bytes (the standard convention), so the EOS
marker, not the buffer length, terminates an unknown-size stream.

Public API: :func:`decode_lzma_alone` (bytes -> bytes), :func:`decode_lzma_alone_instrumented`
(-> ``(bytes, LzmaTrace)``; the trace exposes the literal/match/rep decision stream + range-coder
renormalization count -- the raw material for M1), and :func:`decode_lzma_alone_taped`
(-> ``(bytes, tape)``; the per-bit decision tape the M2 range *encoder* replays). :func:`compress_alone`
wraps the reference encoder for convenience. Deterministic; no third-party dependency (``lzma`` is stdlib).
"""

from __future__ import annotations

import lzma
import math
from dataclasses import dataclass, field

_K_TOP = 1 << 24
_BIT_MODEL_TOTAL = 1 << 11  # 2048
_MOVE_BITS = 5
_PROB_INIT = _BIT_MODEL_TOTAL // 2  # 1024
_NUM_POS_BITS_MAX = 4
_MATCH_MIN_LEN = 2
_END_POS_MODEL_INDEX = 14
_NUM_ALIGN_BITS = 4
_NUM_LEN_TO_POS = 4
_MASK32 = 0xFFFFFFFF


@dataclass
class LzmaTrace:
    """What the decoder saw and did -- the Direction-1 raw material for M1."""

    lc: int
    lp: int
    pb: int
    dict_size: int
    unpack_size: int  # the header field (0xFFFFFFFFFFFFFFFF == unknown -> EOS marker)
    n_literals: int = 0
    n_matched_literals: int = 0  # literals decoded in the state>=7 match-byte context
    n_matches: int = 0           # new (non-rep) matches
    n_reps: int = 0              # rep matches (rep0long / rep1 / rep2 / rep3), len >= 2
    n_short_reps: int = 0        # len-1 rep0 matches
    n_renorm: int = 0            # range-decoder normalize() calls == stream bytes pulled in
    stream_bytes: int = 0        # compressed stream bytes consumed (after the 13-byte header)
    ended_by_marker: bool = False
    cost_bits: float = 0.0       # M1: accumulated -log2(interval fraction) per coded decision
    final_range: int = 0         # M1: the range coder's range at termination (the gauge slack)
    min_range: int = _K_TOP      # M1: smallest range seen entering a coded decision (renorm invariant)
    ops: list = field(default_factory=list)  # [(kind, length, dist)]; kind in lit/mlit/match/rep/shortrep


class _RangeDecoder:
    """The adaptive binary range decoder -- the substrate's entropy engine (no bzip2/zstd analog:
    the model is *not* on the wire, it is the per-``decode_bit`` adaptive probability)."""

    def __init__(self, data: bytes, pos: int, track_cost: bool = False, tape: list | None = None):
        self.data = data
        self.pos = pos
        self.range = _MASK32
        self.init_byte = data[pos]  # must be 0 in a well-formed stream
        code = 0
        for _ in range(4):
            code = ((code << 8) | data[pos + 1]) & _MASK32
            pos += 1
        self.code = code
        self.pos = pos + 1
        self.renorm = 0
        # M1 (pure observation; off by default so decode_lzma_alone is byte-for-byte unchanged).
        self.track_cost = track_cost
        self.cost_bits = 0.0  # accumulated -log2(actual interval fraction) = the achieved code length
        self.min_range = _MASK32  # smallest range entering a coded decision (the renorm invariant)
        # M2 (pure observation; off by default): the decision tape -- one entry per coded primitive,
        # ``(prob, bit)`` per decode_bit and ``("d", value, num)`` per decode_direct_bits, in stream
        # order. Replaying it through the M2 range *encoder* reproduces the payload byte-for-byte
        # (see :func:`zstdct.spoonfeed_lzma.reencode_lzma_alone`).
        self.tape = tape

    def _byte(self) -> int:
        # reads past the logical end normalize as 0 (the standard LZMA convention)
        return self.data[self.pos] if self.pos < len(self.data) else 0

    def normalize(self) -> None:
        if self.range < _K_TOP:
            self.range = (self.range << 8) & _MASK32
            self.code = ((self.code << 8) | self._byte()) & _MASK32
            self.pos += 1
            self.renorm += 1

    def decode_bit(self, probs: list[int], idx: int) -> int:
        prob = probs[idx]
        rng = self.range
        if rng < self.min_range:
            self.min_range = rng
        bound = (rng >> 11) * prob
        if self.code < bound:
            self.range = bound
            probs[idx] = prob + ((_BIT_MODEL_TOTAL - prob) >> _MOVE_BITS)
            if self.track_cost:
                self.cost_bits += -math.log2(bound / rng)
            if self.tape is not None:
                self.tape.append((prob, 0))
            self.normalize()
            return 0
        self.range -= bound
        self.code -= bound
        probs[idx] = prob - (prob >> _MOVE_BITS)
        if self.track_cost:
            self.cost_bits += -math.log2((rng - bound) / rng)
        if self.tape is not None:
            self.tape.append((prob, 1))
        self.normalize()
        return 1

    def decode_direct_bits(self, num: int) -> int:
        res = 0
        for _ in range(num):
            self.range >>= 1
            self.code = (self.code - self.range) & _MASK32
            t = (0 - (self.code >> 31)) & _MASK32
            self.code = (self.code + (self.range & t)) & _MASK32
            self.normalize()
            res = ((res << 1) + (t + 1)) & _MASK32
        if self.track_cost:
            self.cost_bits += num  # each direct bit splits the range in half: exactly 1 bit, p=1/2
        if self.tape is not None:
            self.tape.append(("d", res, num))
        return res

    def bit_tree(self, probs: list[int], base: int, num_bits: int) -> int:
        m = 1
        for _ in range(num_bits):
            m = (m << 1) + self.decode_bit(probs, base + m)
        return m - (1 << num_bits)

    def bit_tree_reverse(self, probs: list[int], base: int, num_bits: int) -> int:
        m = 1
        sym = 0
        for i in range(num_bits):
            bit = self.decode_bit(probs, base + m)
            m = (m << 1) + bit
            sym |= bit << i
        return sym


def _make_len_coder() -> dict:
    return {
        "choice": [_PROB_INIT],
        "choice2": [_PROB_INIT],
        "low": [[_PROB_INIT] * 8 for _ in range(1 << _NUM_POS_BITS_MAX)],
        "mid": [[_PROB_INIT] * 8 for _ in range(1 << _NUM_POS_BITS_MAX)],
        "high": [_PROB_INIT] * 256,
    }


def _decode(comp: bytes, collect: bool, tape: list | None = None) -> tuple[bytes, LzmaTrace | None]:
    props = comp[0]
    lc = props % 9
    lp = (props // 9) % 5
    pb = (props // 9) // 5
    dict_size = int.from_bytes(comp[1:5], "little")
    unpack_size = int.from_bytes(comp[5:13], "little")
    size_known = unpack_size != 0xFFFFFFFFFFFFFFFF

    rc = _RangeDecoder(comp, 13, track_cost=collect, tape=tape)
    lit_probs = [_PROB_INIT] * (0x300 << (lc + lp))
    is_match = [_PROB_INIT] * (12 << _NUM_POS_BITS_MAX)
    is_rep = [_PROB_INIT] * 12
    is_rep_g0 = [_PROB_INIT] * 12
    is_rep_g1 = [_PROB_INIT] * 12
    is_rep_g2 = [_PROB_INIT] * 12
    is_rep0_long = [_PROB_INIT] * (12 << _NUM_POS_BITS_MAX)
    pos_slot = [[_PROB_INIT] * 64 for _ in range(_NUM_LEN_TO_POS)]
    spec_pos = [_PROB_INIT] * 115
    align_probs = [_PROB_INIT] * 16
    len_coder = _make_len_coder()
    rep_len_coder = _make_len_coder()

    out = bytearray()
    rep0 = rep1 = rep2 = rep3 = 0
    state = 0
    tr = LzmaTrace(lc, lp, pb, dict_size, unpack_size) if collect else None
    pb_mask = (1 << pb) - 1
    lp_mask = (1 << lp) - 1

    def decode_len(coder: dict, pos_state: int) -> int:
        if rc.decode_bit(coder["choice"], 0) == 0:
            return rc.bit_tree(coder["low"][pos_state], 0, 3)
        if rc.decode_bit(coder["choice2"], 0) == 0:
            return 8 + rc.bit_tree(coder["mid"][pos_state], 0, 3)
        return 16 + rc.bit_tree(coder["high"], 0, 8)

    while not (size_known and len(out) >= unpack_size):
        pos_state = len(out) & pb_mask
        if rc.decode_bit(is_match, (state << _NUM_POS_BITS_MAX) + pos_state) == 0:
            # literal
            prev = out[-1] if out else 0
            lit_state = ((len(out) & lp_mask) << lc) + (prev >> (8 - lc) if lc else 0)
            base = 0x300 * lit_state
            if state >= 7:
                match_byte = out[-(rep0 + 1)]
                sym = 1
                while sym < 0x100:
                    match_bit = (match_byte >> 7) & 1
                    match_byte = (match_byte << 1) & 0xFF
                    bit = rc.decode_bit(lit_probs, base + ((1 + match_bit) << 8) + sym)
                    sym = (sym << 1) | bit
                    if match_bit != bit:
                        while sym < 0x100:
                            sym = (sym << 1) | rc.decode_bit(lit_probs, base + sym)
                        break
                if tr is not None:
                    tr.n_matched_literals += 1
                    tr.ops.append(("mlit", 1, 0))
            else:
                sym = 1
                while sym < 0x100:
                    sym = (sym << 1) | rc.decode_bit(lit_probs, base + sym)
                if tr is not None:
                    tr.n_literals += 1
                    tr.ops.append(("lit", 1, 0))
            out.append(sym & 0xFF)
            state = 0 if state < 4 else (state - 3 if state < 10 else state - 6)
            continue

        # match (is_match bit was 1)
        if rc.decode_bit(is_rep, state) != 0:
            # rep match
            if rc.decode_bit(is_rep_g0, state) == 0:
                if rc.decode_bit(is_rep0_long, (state << _NUM_POS_BITS_MAX) + pos_state) == 0:
                    state = 9 if state < 7 else 11
                    out.append(out[-(rep0 + 1)])
                    if tr is not None:
                        tr.n_short_reps += 1
                        tr.ops.append(("shortrep", 1, rep0))
                    continue
            else:
                if rc.decode_bit(is_rep_g1, state) == 0:
                    dist = rep1
                else:
                    if rc.decode_bit(is_rep_g2, state) == 0:
                        dist = rep2
                    else:
                        dist = rep3
                        rep3 = rep2
                    rep2 = rep1
                rep1 = rep0
                rep0 = dist
            length = decode_len(rep_len_coder, pos_state)
            state = 8 if state < 7 else 11
            kind = "rep"
        else:
            # new match
            rep3, rep2, rep1 = rep2, rep1, rep0
            length = decode_len(len_coder, pos_state)
            state = 7 if state < 7 else 10
            len_to_pos = length if length < _NUM_LEN_TO_POS else _NUM_LEN_TO_POS - 1
            slot = rc.bit_tree(pos_slot[len_to_pos], 0, 6)
            if slot < 4:
                rep0 = slot
            else:
                num_direct = (slot >> 1) - 1
                rep0 = (2 | (slot & 1)) << num_direct
                if slot < _END_POS_MODEL_INDEX:
                    rep0 += rc.bit_tree_reverse(spec_pos, rep0 - slot, num_direct)
                else:
                    rep0 += rc.decode_direct_bits(num_direct - _NUM_ALIGN_BITS) << _NUM_ALIGN_BITS
                    rep0 += rc.bit_tree_reverse(align_probs, 0, _NUM_ALIGN_BITS)
            if rep0 == _MASK32:  # end-of-stream marker
                if tr is not None:
                    tr.ended_by_marker = True
                break
            kind = "match"

        length += _MATCH_MIN_LEN
        if tr is not None:
            if kind == "rep":
                tr.n_reps += 1
            else:
                tr.n_matches += 1
            tr.ops.append((kind, length, rep0))
        for _ in range(length):
            out.append(out[-(rep0 + 1)])

    if tr is not None:
        tr.n_renorm = rc.renorm
        tr.stream_bytes = rc.pos - 13
        tr.cost_bits = rc.cost_bits
        tr.final_range = rc.range
        tr.min_range = rc.min_range
    return bytes(out), tr


def decode_lzma_alone(comp: bytes) -> bytes:
    """Decode a legacy ``FORMAT_ALONE`` LZMA stream to the original bytes (byte-exact vs
    ``lzma.decompress(comp, format=lzma.FORMAT_ALONE)``)."""
    return _decode(comp, collect=False)[0]


def decode_lzma_alone_instrumented(comp: bytes) -> tuple[bytes, LzmaTrace]:
    """Decode + return an :class:`LzmaTrace` of the literal/match/rep decision stream and the
    range-decoder renormalization count -- the raw material for the M1 kill-switch."""
    out, tr = _decode(comp, collect=True)
    assert tr is not None
    return out, tr


def decode_lzma_alone_taped(comp: bytes) -> tuple[bytes, list]:
    """Decode + return the range-coder **decision tape** -- one entry per coded primitive in stream
    order: ``(prob, bit)`` for every ``decode_bit`` and ``("d", value, num)`` for every
    ``decode_direct_bits``. This is the exact sequence of decisions the adaptive model drove; the M2
    range *encoder* replays it to reproduce the payload byte-for-byte (the Direction-1 anchor at full
    LZMA fidelity -- see :func:`zstdct.spoonfeed_lzma.reencode_lzma_alone`). Pure observation: the
    output is byte-identical to :func:`decode_lzma_alone`."""
    tape: list = []
    out, _ = _decode(comp, collect=False, tape=tape)
    return out, tape


def compress_alone(data: bytes, preset: int = 6, *, lc: int = 3, lp: int = 0, pb: int = 2) -> bytes:
    """Reference encoder wrapper: ``lzma.compress`` in ``FORMAT_ALONE`` with explicit ``lc/lp/pb``
    (the legacy container the decoder reads). The anchor for every spoon-feed forward."""
    filt = [{"id": lzma.FILTER_LZMA1, "preset": preset, "lc": lc, "lp": lp, "pb": pb}]
    return lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filt)
