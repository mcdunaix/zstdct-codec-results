"""A probe that decomposes the bzip2 *decoder* black box.

The encoder teardown (``bzip2_anatomy``) measured compression *ratios* -- the symptom. This
module is the other side: a full, instrumented bzip2 **decoder** that reconstructs the
original from a ``.bz2`` stream stage by stage, surfacing what the decoder actually *sees and
does* at each step. It is a probe, not an optimizer -- there is no target metric.

Decode pipeline (the inverse of the encoder), each stage instrumented:

    .bz2 bits
      -> [container]      block header: CRC, origPtr (the BWT rotation index), symbol map
      -> [Huffman tables] 2-6 canonical trees (code-length shape = tree skewness)
      -> [Huffman decode] the coded symbol stream (RUNA/RUNB run-codes + MTF values + EOB)
      -> [inverse RLE2]   expand RUNA/RUNB -> the MTF index stream (zero-run lengths)
      -> [inverse MTF]    -> the BWT last column
      -> [inverse BWT]    using origPtr -> the RLE1 stream
      -> [inverse RLE1]   -> the original bytes

Rigor anchor (measured, not asserted): :func:`decode_bz2` must equal ``bz2.decompress``
byte-for-byte. The inverse transforms (:func:`bwt_decode`, :func:`mtf_decode`,
:func:`rle1_decode`) and the bit reader are reused from :mod:`zstdct.bzip2_anatomy`.

No zstd, no I/O, deterministic.
"""

from __future__ import annotations

import bz2
from dataclasses import dataclass

from zstdct.bzip2_anatomy import (
    HUFFMAN_GROUP_SIZE,
    RUNA,
    RUNB,
    _BitReader,
    bwt_decode,
    mtf_decode,
    rle1_decode,
    shannon_entropy_bits,
)

_BLOCK_MAGIC = 0x314159265359
_EOS_MAGIC = 0x177245385090


# --- canonical Huffman ------------------------------------------------------
@dataclass
class _HuffTable:
    """Canonical Huffman decode table built from per-symbol code lengths."""

    max_len: int
    first_code: list[int]
    first_sym: list[int]
    limit: list[int]
    syms: list[int]
    lengths: list[int]  # kept for instrumentation (tree shape)


def _build_table(lengths: list[int]) -> _HuffTable:
    max_len = max(lengths)
    by_len: list[list[int]] = [[] for _ in range(max_len + 1)]
    for sym, length in enumerate(lengths):
        if length > 0:
            by_len[length].append(sym)
    first_code = [0] * (max_len + 2)
    first_sym = [0] * (max_len + 2)
    limit = [-1] * (max_len + 2)
    syms: list[int] = []
    code = 0
    idx = 0
    for length in range(1, max_len + 1):
        first_code[length] = code
        first_sym[length] = idx
        cnt = len(by_len[length])
        syms.extend(by_len[length])
        code += cnt
        limit[length] = code - 1
        idx += cnt
        code <<= 1
    return _HuffTable(max_len, first_code, first_sym, limit, syms, lengths)


def _decode_symbol(br: _BitReader, t: _HuffTable) -> int:
    code = 0
    for length in range(1, t.max_len + 1):
        code = (code << 1) | br.read_bit()
        if t.limit[length] >= code >= t.first_code[length] and t.limit[length] >= 0:
            return t.syms[t.first_sym[length] + (code - t.first_code[length])]
    raise ValueError("invalid Huffman code")


def _read_code_lengths(br: _BitReader, n_symbols: int) -> list[int]:
    """bzip2's delta-coded code lengths: 5-bit start, then +1/-1 deltas per symbol."""
    curr = br.read(5)
    lengths = []
    for _ in range(n_symbols):
        while br.read_bit() == 1:
            curr += -1 if br.read_bit() == 1 else 1
        lengths.append(curr)
    return lengths


# --- per-stage instrumentation ----------------------------------------------
@dataclass
class HuffTreeStat:
    n_symbols: int
    min_len: int
    max_len: int
    mean_len: float
    len_entropy_bits: float  # skewness proxy: entropy of the code-length distribution


@dataclass
class BlockDecode:
    """What the decoder saw and did for one block."""

    crc: int
    rotation_index: int           # origPtr -- the BWT index, one number that pins the permutation
    alphabet_size: int            # distinct post-RLE1 bytes (symbol map)
    n_huffman_groups: int
    n_selectors: int
    selector_usage: list[int]
    huffman_trees: list[HuffTreeStat]
    coded_symbols: int            # length of the Huffman-decoded symbol stream (excl. EOB)
    symbol_mix: dict[str, int]    # RUNA / RUNB / value / counts
    max_zero_run: int             # longest RLE2 zero-run the decoder expanded
    # stage output sizes (bytes), decode direction -- the expansion the decoder performs
    stage_bytes: dict[str, int]

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["huffman_trees"] = [t.__dict__ for t in self.huffman_trees]
        return d


def _read_symbol_map(br: _BitReader) -> list[int]:
    used16 = br.read(16)
    symbols: list[int] = []
    for i in range(16):
        if used16 & (1 << (15 - i)):
            bitmap = br.read(16)
            for j in range(16):
                if bitmap & (1 << (15 - j)):
                    symbols.append(i * 16 + j)
    return symbols


def _read_selectors(br: _BitReader, n_groups: int, n_selectors: int) -> list[int]:
    mtf = list(range(n_groups))
    out = []
    for _ in range(n_selectors):
        j = 0
        while br.read_bit() == 1:
            j += 1
        val = mtf.pop(j)
        mtf.insert(0, val)
        out.append(val)
    return out


def _decode_block(br: _BitReader) -> tuple[bytes, BlockDecode]:
    crc = br.read(32)
    br.read_bit()  # randomized (obsolete)
    rotation_index = br.read(24)
    symbols = _read_symbol_map(br)
    n_used = len(symbols)
    n_symbols = n_used + 2          # + RUNA, RUNB ... and EOB at the top
    eob = n_used + 1
    n_groups = br.read(3)
    n_selectors = br.read(15)
    selectors = _read_selectors(br, n_groups, n_selectors)
    tables = [_build_table(_read_code_lengths(br, n_symbols)) for _ in range(n_groups)]

    # Huffman-decode the coded symbol stream (table switches every 50 symbols).
    coded: list[int] = []
    decoded = 0
    group = 0
    table = tables[selectors[0]]
    while True:
        if decoded % HUFFMAN_GROUP_SIZE == 0:
            table = tables[selectors[group]]
            group += 1
        s = _decode_symbol(br, table)
        decoded += 1
        if s == eob:
            break
        coded.append(s)

    # inverse RLE2: RUNA/RUNB bijective zero-runs -> MTF index stream
    mtf_indices: list[int] = []
    n_runa = n_runb = n_value = 0
    max_zero_run = 0
    i = 0
    while i < len(coded):
        s = coded[i]
        if s == RUNA or s == RUNB:
            run = 0
            mult = 1
            while i < len(coded) and coded[i] in (RUNA, RUNB):
                if coded[i] == RUNA:
                    run += mult
                    n_runa += 1
                else:
                    run += 2 * mult
                    n_runb += 1
                mult <<= 1
                i += 1
            mtf_indices.extend([0] * run)
            max_zero_run = max(max_zero_run, run)
        else:
            mtf_indices.append(s - 1)  # encoder shifted nonzero MTF value v -> v+1
            n_value += 1
            i += 1

    last_column = mtf_decode(mtf_indices, symbols)   # inverse MTF -> BWT last column
    rle1_stream = bwt_decode(last_column, rotation_index)  # inverse BWT
    original = rle1_decode(rle1_stream)               # inverse RLE1

    trees = [HuffTreeStat(
        n_symbols=sum(1 for length in t.lengths if length > 0),
        min_len=min(length for length in t.lengths if length > 0),
        max_len=max(t.lengths),
        mean_len=round(sum(t.lengths) / len(t.lengths), 4),
        len_entropy_bits=round(shannon_entropy_bits(t.lengths), 4),
    ) for t in tables]

    trace = BlockDecode(
        crc=crc,
        rotation_index=rotation_index,
        alphabet_size=n_used,
        n_huffman_groups=n_groups,
        n_selectors=n_selectors,
        selector_usage=[selectors.count(g) for g in range(n_groups)],
        huffman_trees=trees,
        coded_symbols=len(coded),
        symbol_mix={"runa": n_runa, "runb": n_runb, "value": n_value},
        max_zero_run=max_zero_run,
        stage_bytes={
            "coded_symbols": len(coded),
            "mtf_stream": len(mtf_indices),
            "bwt_block": len(last_column),
            "rle1_stream": len(rle1_stream),
            "original": len(original),
        },
    )
    return original, trace


def decode_bz2_instrumented(compressed: bytes) -> tuple[bytes, list[BlockDecode]]:
    """Full instrumented decode. Returns (original bytes, per-block decode traces)."""
    if compressed[:3] != b"BZh" or not (0x31 <= compressed[3] <= 0x39):
        raise ValueError("not a bzip2 stream")
    br = _BitReader(compressed, start_byte=4)
    out = bytearray()
    traces: list[BlockDecode] = []
    while True:
        magic = (br.read(24) << 24) | br.read(24)
        if magic == _EOS_MAGIC:
            br.read(32)  # combined stream CRC
            break
        if magic != _BLOCK_MAGIC:
            raise ValueError(f"bad block magic {magic:#x}")
        block, trace = _decode_block(br)
        out.extend(block)
        traces.append(trace)
    return bytes(out), traces


def decode_bz2(compressed: bytes) -> bytes:
    """Decode only (no trace); the validation target is ``bz2.decompress``."""
    return decode_bz2_instrumented(compressed)[0]


if __name__ == "__main__":
    import json
    import numpy as np

    rng = np.random.default_rng(0)
    samples = {
        "all_zero": b"\x00" * 4000,
        "runs": (b"x" * 7 + b"y" * 3) * 400,
        "text": b"the quick brown fox " * 200,
        "low_alpha": bytes(rng.integers(0, 4, 4000, dtype=np.uint8)),
        "random": bytes(rng.integers(0, 256, 4000, dtype=np.uint8)),
        "ramp": bytes(range(256)) * 16,
    }
    print(f"{'shape':12s} {'ok':>4s} {'rot_idx':>8s} {'groups':>6s} {'coded':>7s} {'maxrun':>7s}")
    for name, data in samples.items():
        comp = bz2.compress(data, 9)
        out, traces = decode_bz2_instrumented(comp)
        ok = out == data == bz2.decompress(comp)
        t = traces[0]
        print(f"{name:12s} {str(ok):>4s} {t.rotation_index:8d} {t.n_huffman_groups:6d} "
              f"{t.coded_symbols:7d} {t.max_zero_run:7d}")
        if not ok:
            print("  MISMATCH", json.dumps(t.to_dict())[:200])
