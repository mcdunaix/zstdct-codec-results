"""bzip2 anatomy: a validated, stage-by-stage decomposition of the bzip2 pipeline.

The whole A->F arc lived in the **LZ/dictionary** family (zstd, gzip, lzma) and asked
where ZSTD's *resonance* comes from (dict-size = which offsets are cheap -> a peak). This
module turns the lens on **one** compressor, bzip2, **alone** -- no zstd, no cross-compressor
comparison. bzip2 is not one knob; it is a five-stage pipeline:

    RLE1  ->  BWT  ->  MTF  ->  RLE2  ->  Huffman
    (run    (block   (move-   (zero-    (2-6 tables,
     >=4)    sort)    to-      run        switched
                      front)   coding)    per 50 syms)

A block-size sweep (the toy probe) only ever touched the *outermost* knob and found it
monotonic. To ask "where does bzip2's compression actually come from, and does any
internal scale resonate?" we need to see *inside*. This module provides two windows:

1. **Faithful stage reimplementations** (:func:`rle1_encode`, :func:`bwt_encode`,
   :func:`mtf_encode`, :func:`rle2_encode`) so we can measure the size / order-0 entropy
   of the stream *after each stage* -- the "where do the bits go" attribution.
2. **A real ``.bz2`` container parser** (:func:`parse_bz2`) that reads bzip2's own adaptive
   decisions straight from the compressed bytes without decompressing: the symbol-map size
   (distinct bytes after RLE1), the number of Huffman groups it chose (2-6), and the
   selector count (~= coded-symbol count / 50). These are the hidden knobs.

**Validation (measured, not asserted -- arc discipline).** Every transform round-trips
(``decode(encode(x)) == x``). And the *cross-check that grounds the reimplementation
against the real encoder*: the alphabet of our RLE1 output must equal the symbol-map size
the real bzip2 wrote into the container (bzip2 applies RLE1 first, then BWT -- a
permutation -- so the post-RLE1 byte set is exactly bzip2's symbol map). We do **not**
claim byte-exact BWT (tie-breaking differs); we validate the invariants that must hold.

Determinism: every function here is deterministic; ``bz2.compress`` is deterministic. No
randomness, no I/O.
"""

from __future__ import annotations

import bz2
from dataclasses import dataclass, field

import numpy as np

# bzip2 RLE1 fires on runs of >= 4 identical bytes; the count byte carries 0..251 *extra*
# repeats, so a single coded run spans 4..255 bytes. This 4 is a hard, fixed scale.
RLE1_RUN_TRIGGER = 4
RLE1_MAX_EXTRA = 251  # count byte range 0..251 -> max single run 4 + 251 = 255
# bzip2 switches Huffman table once per group of 50 coded symbols.
HUFFMAN_GROUP_SIZE = 50
# Run symbols at the bottom of the post-MTF alphabet.
RUNA, RUNB = 0, 1

_BLOCK_MAGIC = 0x314159265359  # "pi"   -- a compressed data block
_EOS_MAGIC = 0x177245385090    # "sqrt pi" -- end of stream


# --- entropy ----------------------------------------------------------------
def shannon_entropy_bits(symbols) -> float:
    """Order-0 (memoryless) Shannon entropy in bits per symbol.

    The cost an *ideal* order-0 entropy coder (e.g. a single Huffman table) would pay per
    symbol. Accepts bytes or a sequence of ints. Empty input -> 0.0.
    """
    arr = np.frombuffer(symbols, dtype=np.uint8) if isinstance(symbols, (bytes, bytearray)) \
        else np.asarray(list(symbols), dtype=np.int64)
    if arr.size == 0:
        return 0.0
    _, counts = np.unique(arr, return_counts=True)
    p = counts / counts.sum()
    return float(-np.sum(p * np.log2(p)))


def ideal_order0_bits(symbols) -> float:
    """Total ideal order-0 cost of a stream: ``len * H0`` bits."""
    n = len(symbols)
    return n * shannon_entropy_bits(symbols) if n else 0.0


def ideal_per_group_bits(symbols, group_size: int = HUFFMAN_GROUP_SIZE) -> float:
    """Ideal cost if each ``group_size``-symbol group got its *own* order-0 table, overhead-free.

    bzip2's selector picks one of <=6 *shared* tables per group, so this per-group-table cost
    is a strict **lower bound** on what its multi-table scheme can achieve. The gap
    ``ideal_order0_bits - ideal_per_group_bits`` is therefore an **upper bound** on the pure
    adaptivity benefit of the 50-symbol selector (before the cost of storing the tables). If
    even this upper bound is small / non-resonant, bzip2's real selector is too.
    """
    total = 0.0
    for i in range(0, len(symbols), group_size):
        total += ideal_order0_bits(symbols[i:i + group_size])
    return total


# --- stage 1: RLE1 (initial run-length encoding) ----------------------------
def rle1_encode(data: bytes) -> bytes:
    """bzip2's first stage. A run of >= 4 identical bytes -> 4 copies + 1 count byte.

    A run of exactly 4 therefore costs **5** bytes (4 + a zero count) -- RLE1 can *expand*
    at the trigger. Runs > 255 split into multiple coded runs. Runs of 1-3 pass through
    untouched. This hard threshold at 4 is bzip2's only fixed structural scale.
    """
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        run = 1
        while i + run < n and data[i + run] == b and run < RLE1_RUN_TRIGGER + RLE1_MAX_EXTRA:
            run += 1
        if run >= RLE1_RUN_TRIGGER:
            out.extend(bytes([b]) * RLE1_RUN_TRIGGER)
            out.append(run - RLE1_RUN_TRIGGER)
        else:
            out.extend(bytes([b]) * run)
        i += run
    return bytes(out)


def rle1_decode(data: bytes) -> bytes:
    """Inverse of :func:`rle1_encode` (for round-trip validation)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        run = 1
        while i + run < n and data[i + run] == b and run < RLE1_RUN_TRIGGER:
            run += 1
        out.extend(bytes([b]) * run)
        i += run
        if run == RLE1_RUN_TRIGGER and i < n:
            extra = data[i]
            out.extend(bytes([b]) * extra)
            i += 1
    return bytes(out)


# --- stage 2: BWT (Burrows-Wheeler Transform, rotation-based) ----------------
def _rotation_suffix_array(s: bytes) -> np.ndarray:
    """Sorted order of the n cyclic rotations of ``s`` (prefix doubling, O(n log^2 n)).

    Returns the permutation ``sa`` where ``sa[r]`` is the start index of the r-th smallest
    rotation. Vectorised with numpy so blocks up to a few hundred KB are fast.
    """
    n = len(s)
    if n == 0:
        return np.empty(0, dtype=np.int64)
    arr = np.frombuffer(s, dtype=np.uint8).astype(np.int64)
    # dense initial ranks from byte values
    _, rank = np.unique(arr, return_inverse=True)
    rank = rank.astype(np.int64)
    idx = np.arange(n)
    k = 1
    while True:
        second = rank[(idx + k) % n]
        # composite sort key (rank, second); rank < n so this fits int64 for n < ~3e9
        key = rank * (n + 1) + second
        order = np.argsort(key, kind="stable")
        sk = key[order]
        new = np.empty(n, dtype=np.int64)
        new[order] = np.cumsum(np.concatenate(([0], (sk[1:] != sk[:-1]).astype(np.int64))))
        rank = new
        if int(rank.max()) == n - 1 or k >= n:
            return np.argsort(rank, kind="stable")
        k <<= 1


def bwt_encode(s: bytes) -> tuple[bytes, int]:
    """Rotation BWT. Returns (last column ``L``, primary index ``p``).

    ``L[r]`` is the last byte of the r-th smallest rotation; ``p`` is the rank of the
    rotation that starts at index 0. This is the same transform bzip2 applies (after RLE1);
    we do not match bzip2's exact tie-breaking, but ``L`` is a true BWT and round-trips.
    """
    n = len(s)
    if n == 0:
        return b"", 0
    sa = _rotation_suffix_array(s)
    arr = np.frombuffer(s, dtype=np.uint8)
    last = arr[(sa - 1) % n]
    primary = int(np.where(sa == 0)[0][0])
    return last.tobytes(), primary


def bwt_decode(last: bytes, primary: int) -> bytes:
    """Inverse rotation BWT via the LF-mapping (for round-trip validation)."""
    n = len(last)
    if n == 0:
        return b""
    arr = np.frombuffer(last, dtype=np.uint8).astype(np.int64)
    # stable sort of L gives F; LF[i] maps row i in L-order to its row in F-order
    lf = np.argsort(arr, kind="stable")
    out = np.empty(n, dtype=np.uint8)
    row = lf[primary]
    for k in range(n):
        out[k] = last[row]
        row = lf[row]
    return out.tobytes()


# --- stage 3: MTF (Move-To-Front) -------------------------------------------
def mtf_encode(data: bytes, alphabet: list[int] | None = None) -> list[int]:
    """Move-to-front over ``data``'s alphabet. Maps locally-repetitive bytes to small ints.

    Initialised with the sorted set of present byte values (bzip2's symbol-map order), so a
    BWT output with long same-byte runs becomes a stream dominated by zeros -- exactly the
    skew RLE2 + Huffman then cash in.
    """
    table = list(alphabet) if alphabet is not None else sorted(set(data))
    out: list[int] = []
    # list.index / pop / insert are all C-level; faster than a Python-side position map.
    for b in data:
        i = table.index(b)
        out.append(i)
        if i:
            del table[i]
            table.insert(0, b)
    return out


def mtf_decode(indices: list[int], alphabet: list[int]) -> bytes:
    """Inverse MTF (for round-trip validation)."""
    table = list(alphabet)
    out = bytearray()
    for i in indices:
        b = table[i]
        out.append(b)
        if i:
            del table[i]
            table.insert(0, b)
    return bytes(out)


# --- stage 4: RLE2 (zero-run coding, RUNA/RUNB) -----------------------------
def rle2_encode(mtf_indices: list[int], alphabet_size: int) -> list[int]:
    """bzip2's second RLE: collapse runs of MTF-zeros with bijective base-2 (RUNA/RUNB).

    Non-zero MTF value ``v`` becomes ``v + 1`` (shifted up to free 0,1 for the run symbols);
    a run of ``z`` zeros becomes its bijective-base-2 digits (RUNA=0, RUNB=1, LSB first);
    a final EOB symbol (= ``alphabet_size + 1``) terminates the block.
    """
    out: list[int] = []

    def _emit_zero_run(z: int) -> None:
        while z > 0:
            z -= 1
            out.append(RUNA if (z & 1) == 0 else RUNB)
            z >>= 1

    zeros = 0
    for v in mtf_indices:
        if v == 0:
            zeros += 1
        else:
            if zeros:
                _emit_zero_run(zeros)
                zeros = 0
            out.append(v + 1)
    if zeros:
        _emit_zero_run(zeros)
    out.append(alphabet_size + 1)  # EOB
    return out


# --- the full stage breakdown -----------------------------------------------
@dataclass
class StageBreakdown:
    """Where bzip2's bits go, per *input* byte, for one blob.

    Every ``*_bpb`` field is bits-per-input-byte so stages compose into a waterfall:
    ``raw`` -> ``order0_input`` (plain entropy coding) -> ``after_rle1`` -> ``after_bwt_mtf``
    (the context->skew magic) -> ``after_rle2`` -> ``bzip2_actual`` (the real coder).
    """

    input_bytes: int
    raw_bpb: float                  # 8.0 by definition
    order0_input_bpb: float         # ideal single-table Huffman on the raw input
    after_rle1_bpb: float           # ideal order-0 cost of the RLE1 stream
    after_bwt_mtf_bpb: float        # ideal order-0 cost of the MTF stream (BWT+MTF skew)
    after_rle2_bpb: float           # ideal order-0 cost of the RLE2 symbol stream
    bzip2_actual_bpb: float         # real bz2.compress, bits per input byte
    # stream shapes
    rle1_len: int
    mtf_len: int
    rle2_len: int
    alphabet_size: int              # distinct bytes after RLE1 (our reimpl)
    mtf_zero_fraction: float        # fraction of MTF symbols that are 0 (BWT coherence)
    # what each phase buys, in bits per input byte (>=0 means it helped)
    rle1_gain_bpb: float
    bwt_mtf_gain_bpb: float
    rle2_gain_bpb: float
    huffman_adaptivity_gain_bpb: float  # order0(rle2) - real: <0 if multi-table beats H0

    def to_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def stage_breakdown(data: bytes, *, compresslevel: int = 9) -> StageBreakdown:
    """Run all five stages on ``data`` and attribute the bits removed at each."""
    n = len(data)
    if n == 0:
        raise ValueError("stage_breakdown requires non-empty input")

    r1 = rle1_encode(data)
    last, _ = bwt_encode(r1)
    alphabet = sorted(set(r1))
    m = mtf_encode(last, alphabet)
    r2 = rle2_encode(m, len(alphabet))

    raw = 8.0
    order0_input = shannon_entropy_bits(data)
    after_rle1 = ideal_order0_bits(r1) / n
    after_bwt_mtf = ideal_order0_bits(m) / n
    after_rle2 = ideal_order0_bits(r2) / n
    actual = 8.0 * len(bz2.compress(data, compresslevel)) / n

    zero_frac = (sum(1 for v in m if v == 0) / len(m)) if m else 0.0

    return StageBreakdown(
        input_bytes=n,
        raw_bpb=raw,
        order0_input_bpb=order0_input,
        after_rle1_bpb=after_rle1,
        after_bwt_mtf_bpb=after_bwt_mtf,
        after_rle2_bpb=after_rle2,
        bzip2_actual_bpb=actual,
        rle1_len=len(r1),
        mtf_len=len(m),
        rle2_len=len(r2),
        alphabet_size=len(alphabet),
        mtf_zero_fraction=zero_frac,
        rle1_gain_bpb=order0_input - after_rle1,
        bwt_mtf_gain_bpb=after_rle1 - after_bwt_mtf,
        rle2_gain_bpb=after_bwt_mtf - after_rle2,
        huffman_adaptivity_gain_bpb=after_rle2 - actual,
    )


# --- the .bz2 container parser (reads bzip2's own decisions) ------------------
class _BitReader:
    """MSB-first bit reader over a byte string."""

    def __init__(self, data: bytes, start_byte: int = 0):
        self._data = data
        self._bitpos = start_byte * 8

    def read(self, nbits: int) -> int:
        v = 0
        for _ in range(nbits):
            byte = self._data[self._bitpos >> 3]
            bit = (byte >> (7 - (self._bitpos & 7))) & 1
            v = (v << 1) | bit
            self._bitpos += 1
        return v

    def read_bit(self) -> int:
        return self.read(1)

    @property
    def bitpos(self) -> int:
        return self._bitpos


@dataclass
class Bz2Block:
    """bzip2's per-block decisions, read straight from the compressed container."""

    crc: int
    orig_ptr: int               # BWT primary index bzip2 chose
    alphabet_size: int          # symbol-map popcount = distinct bytes after RLE1
    n_huffman_groups: int       # 2..6 -- how many tables bzip2 decided it needed
    n_selectors: int            # one per HUFFMAN_GROUP_SIZE coded symbols
    selector_usage: list[int]   # times each group/table was selected
    approx_coded_symbols: int   # n_selectors * 50 (upper bound on the coded stream length)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Bz2Container:
    level: int                  # 1..9 -> block size 100K..900K
    blocks: list[Bz2Block] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "blocks": [b.to_dict() for b in self.blocks]}


def parse_bz2(compressed: bytes) -> Bz2Container:
    """Parse a ``.bz2`` stream up to (not including) each block's Huffman-coded payload.

    Extracts the structural decisions that are the hidden knobs of this probe -- the symbol
    map size, the number of Huffman groups (2-6), and the selector count -- without
    decoding the entropy-coded data. Raises ``ValueError`` on a malformed header.
    """
    if compressed[:3] != b"BZh" or not (0x31 <= compressed[3] <= 0x39):
        raise ValueError("not a bzip2 stream")
    level = compressed[3] - 0x30
    br = _BitReader(compressed, start_byte=4)
    blocks: list[Bz2Block] = []

    while True:
        magic = (br.read(24) << 24) | br.read(24)
        if magic == _EOS_MAGIC:
            break
        if magic != _BLOCK_MAGIC:
            raise ValueError(f"bad block magic {magic:#x}")

        crc = br.read(32)
        br.read_bit()  # randomized flag (obsolete in modern bzip2)
        orig_ptr = br.read(24)

        # symbol map: 16-bit "used ranges", then a 16-bit bitmap per used range
        used16 = br.read(16)
        alphabet_size = 0
        for r in range(16):
            if used16 & (1 << (15 - r)):
                bitmap = br.read(16)
                alphabet_size += bin(bitmap).count("1")

        n_groups = br.read(3)
        n_selectors = br.read(15)

        # selectors: each is a unary-coded MTF index into [0, n_groups); un-MTF them
        mtf = list(range(n_groups))
        usage = [0] * n_groups
        for _ in range(n_selectors):
            j = 0
            while br.read_bit() == 1:
                j += 1
            val = mtf.pop(j)
            mtf.insert(0, val)
            usage[val] += 1

        blocks.append(Bz2Block(
            crc=crc,
            orig_ptr=orig_ptr,
            alphabet_size=alphabet_size,
            n_huffman_groups=n_groups,
            n_selectors=n_selectors,
            selector_usage=usage,
            approx_coded_symbols=n_selectors * HUFFMAN_GROUP_SIZE,
        ))
        # We stop parsing this block here (before the Huffman tables + coded data). To reach
        # the next block we'd need to decode the payload; for single-block inputs (<=900K)
        # that's unnecessary -- bzip2 emits one data block + EOS. Bail after the first.
        break

    return Bz2Container(level=level, blocks=blocks)


# --- self-check: round-trips + cross-validation against the real encoder ------
def self_check(samples: list[bytes]) -> dict:
    """Validate the reimplementation: every stage round-trips, and our RLE1 alphabet equals
    the symbol-map size the real bzip2 wrote. Returns a report; raises on any failure."""
    report: dict = {"round_trips": 0, "alphabet_matches": 0, "samples": len(samples), "detail": []}
    for s in samples:
        if not s:
            continue
        # round-trips
        assert rle1_decode(rle1_encode(s)) == s, "rle1 round-trip failed"
        r1 = rle1_encode(s)
        last, p = bwt_encode(r1)
        assert bwt_decode(last, p) == r1, "bwt round-trip failed"
        alphabet = sorted(set(r1))
        m = mtf_encode(last, alphabet)
        assert mtf_decode(m, alphabet) == last, "mtf round-trip failed"
        report["round_trips"] += 1
        # cross-validation: our post-RLE1 alphabet == real bzip2 symbol map
        container = parse_bz2(bz2.compress(s, 9))
        real_alpha = container.blocks[0].alphabet_size if container.blocks else -1
        ok = real_alpha == len(alphabet)
        report["alphabet_matches"] += int(ok)
        report["detail"].append({
            "len": len(s), "our_alphabet": len(alphabet), "real_alphabet": real_alpha,
            "n_groups": container.blocks[0].n_huffman_groups if container.blocks else None,
            "match": ok,
        })
    return report


if __name__ == "__main__":
    import json

    rng = np.random.default_rng(0)
    samples = [
        b"A" * 1000,
        b"ABAB" * 500,
        bytes(range(256)) * 40,
        b"the quick brown fox " * 200,
        bytes(rng.integers(0, 4, size=4000, dtype=np.uint8)),
        bytes(rng.integers(0, 256, size=4000, dtype=np.uint8)),
        (b"x" * 7 + b"y" * 3) * 400,
    ]
    print(json.dumps(self_check(samples), indent=2))
