"""A byte-exact DEFLATE + gzip decoder -- ground truth for spoon-feed substrate #4 (the most-deployed codec).

Substrates #1 (bzip2, :mod:`zstdct.bzip2_decoder`) and #2 (zstd, :mod:`zstdct.zstd_decoder`) transmit a
**static model** (a Huffman tree / an FSE table) = a Mode-A counting manifold; substrate #3 (LZMA,
:mod:`zstdct.lzma_decoder`) is the cross-family case (adaptive range coder, *no* model on the wire).
DEFLATE (RFC 1951) is the **prototype static-model codec**: LZ77 -> two per-block canonical Huffman
trees (one for literals+lengths, one for distances), both *transmitted inside the block* -- the cleanest
member of the static family, and the one in front of everyone (gzip / zlib / PNG / ZIP / HTTP). This
module is the M0 gate: a faithful RFC 1951 (DEFLATE) + RFC 1952 (gzip wrapper) decoder, validated
byte-exact against the stdlib ``gzip`` / ``zlib``.

Decode pipeline (the inverse of the encoder), each piece instrumented:

    gzip stream
      -> [RFC 1952 wrapper]   magic / FLG / MTIME / optional FEXTRA·FNAME·FCOMMENT·FHCRC; trailer CRC32+ISIZE
      -> [RFC 1951 blocks]    BFINAL + BTYPE: 00 stored (passthrough) / 01 fixed Huffman / 10 dynamic Huffman
      -> [dynamic trees]      the code-length-code (19 syms, the recursive "Kraft inside Kraft") -> the two main trees
      -> [Huffman decode]     lit/len + distance symbols (canonical codes, MSB-first accumulation)
      -> [LZ77 execute]       literals copied out; (length, distance) back-references replayed (overlap-safe)

**Bit order (the substrate's headline gotcha):** DEFLATE packs the bitstream **LSB-first** (bzip2 was
MSB-first); data elements (LEN, extra bits, HLIT...) are read LSB-first, **but Huffman code bits are
emitted MSB-first**, so a code is still accumulated ``code = (code << 1) | next_bit``. The canonical
construction (length-ordered, then symbol-ordered: RFC 1951 §3.2.2) is identical to bzip2's, so the same
decode logic inverts whatever ``zlib`` encodes.

Public API: :func:`inflate` (raw RFC 1951 -> bytes; == ``zlib.decompress(data, -15)``),
:func:`decode_gzip` (RFC 1952 wrapper, multi-member, CRC32/ISIZE checked; == ``gzip.decompress``), and
the ``*_instrumented`` variants returning a :class:`GzipTrace` -- the per-block transmitted code lengths
(the counting manifold), the code-length-code (the recursive manifold), and the literal/match counts =
the raw material for M1. Deterministic; no third-party dependency (``gzip`` / ``zlib`` are stdlib;
``zlib.crc32`` is used only as the trailer checksum)."""

from __future__ import annotations

import gzip
import zlib
from dataclasses import dataclass, field

# --- RFC 1951 §3.2.5 length/distance code tables (cross-checked vs the RFC + zlib lbase/lext/dbase/dext)
# Literal/length symbols 257..285 -> (base length, extra bits). Symbol 285 = length 258, 0 extra bits.
_LENGTH_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43,
                51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
_LENGTH_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3,
                 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
# Distance symbols 0..29 -> (base distance, extra bits). 30/31 exist in the 5-bit alphabet but never occur.
_DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385,
              513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
_DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7,
               8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]
# RFC 1951 §3.2.7: the permuted order in which the (up to 19) code-length-code lengths are read.
_CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
# RFC 1951 §3.2.6: the fixed (static) Huffman code lengths -- a *conventional* model (0 bytes shipped).
_FIXED_LITLEN_LENGTHS = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8  # 288 entries (0..287)
_FIXED_DIST_LENGTHS = [5] * 32  # all 32 distance codes are 5 bits (30/31 unused)


class _BitReader:
    """LSB-first bit reader over a byte buffer (the DEFLATE bit order; contrast bzip2's MSB-first).

    Bits are consumed from the least-significant end of the current byte upward. ``read`` returns a
    multi-bit field LSB-first (the first bit read is the field's least-significant bit). Huffman codes
    are *not* read via :meth:`read`; the decode loop pulls single bits and accumulates them MSB-first."""

    __slots__ = ("data", "bytepos", "bitpos", "nbits")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.bytepos = pos
        self.bitpos = 0       # 0..7, bits already consumed from the current byte (from the LSB)
        self.nbits = 0        # total bits consumed (instrumentation)

    def read_bit(self) -> int:
        bit = (self.data[self.bytepos] >> self.bitpos) & 1
        self.bitpos += 1
        if self.bitpos == 8:
            self.bitpos = 0
            self.bytepos += 1
        self.nbits += 1
        return bit

    def read(self, n: int) -> int:
        """Read an ``n``-bit field, LSB-first (the value's low bit comes first off the wire)."""
        val = 0
        for i in range(n):
            val |= self.read_bit() << i
        return val

    def align_to_byte(self) -> None:
        """Discard the rest of the current byte (used before a stored block and before the gzip trailer)."""
        if self.bitpos != 0:
            self.bitpos = 0
            self.bytepos += 1

    def read_aligned_bytes(self, n: int) -> bytes:
        """Read ``n`` whole bytes; the reader must be byte-aligned (a stored-block body)."""
        assert self.bitpos == 0
        chunk = self.data[self.bytepos:self.bytepos + n]
        self.bytepos += n
        self.nbits += 8 * n
        return chunk


# --- canonical Huffman (length-ordered, then symbol-ordered: RFC 1951 §3.2.2 == bzip2's construction) ---
@dataclass
class _HuffTable:
    """A canonical Huffman decode table built from per-symbol code lengths (0 = symbol absent)."""

    max_len: int
    first_code: list[int]
    first_sym: list[int]
    limit: list[int]
    syms: list[int]
    lengths: list[int]  # kept for instrumentation (the transmitted counting manifold's shape)


def _build_table(lengths: list[int]) -> _HuffTable:
    """Assign canonical codes from code lengths exactly as RFC 1951 §3.2.2: shorter codes first, then
    increasing symbol value. Inverts whatever ``zlib`` emits (its encoder uses the same canonical rule)."""
    max_len = max(lengths) if lengths else 0
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
    """Decode one canonical Huffman symbol: pull bits and accumulate MSB-first until the code lands in
    the [first_code, limit] window for its length."""
    code = 0
    for length in range(1, t.max_len + 1):
        code = (code << 1) | br.read_bit()
        if t.limit[length] >= 0 and t.first_code[length] <= code <= t.limit[length]:
            return t.syms[t.first_sym[length] + (code - t.first_code[length])]
    raise ValueError("invalid Huffman code (fell off the prefix-code manifold)")


# The fixed Huffman tables are spec constants -- the *conventional* model (built once, shipped 0 bytes).
_FIXED_LITLEN_TABLE = _build_table(_FIXED_LITLEN_LENGTHS)
_FIXED_DIST_TABLE = _build_table(_FIXED_DIST_LENGTHS)


# --- instrumentation: what the decoder saw and did (the Direction-1 raw material for M1) ---------------
@dataclass
class DeflateBlockTrace:
    """One DEFLATE block. ``btype`` 0=stored, 1=fixed Huffman, 2=dynamic Huffman -- the three answers to
    'where is the model' (no model / conventional-constant / shipped). The ``*_lengths`` arrays are the
    transmitted code lengths = the counting manifold; ``cl_lengths`` (dynamic only) is the recursive
    code-length-code manifold (Kraft inside Kraft)."""

    btype: int
    bfinal: bool
    n_literals: int = 0
    n_matches: int = 0
    n_stored_bytes: int = 0          # stored blocks: raw bytes copied through
    out_bytes: int = 0               # bytes this block produced
    payload_bits: int = 0            # compressed bits this block consumed (the Mode-B payload size)
    # the transmitted / conventional trees (None for stored):
    litlen_lengths: list | None = None   # code length per lit/len symbol (0 = absent)
    dist_lengths: list | None = None     # code length per distance symbol
    cl_lengths: list | None = None       # the 19 code-length-code lengths (dynamic only) -- recursive manifold
    hlit: int = 0                    # dynamic: #lit/len codes (= HLIT field + 257)
    hdist: int = 0                   # dynamic: #distance codes (= HDIST field + 1)
    hclen: int = 0                   # dynamic: #code-length codes (= HCLEN field + 4)


@dataclass
class GzipTrace:
    """The whole stream: per-block traces + the gzip-wrapper fields the decoder read."""

    blocks: list = field(default_factory=list)   # DeflateBlockTrace, in stream order
    n_members: int = 0                            # gzip members (concatenated streams)
    fname: bytes | None = None
    mtime: int = 0
    isize: int = 0                                # the ISIZE trailer field of the last member
    crc32: int = 0                                # the CRC32 trailer field of the last member

    @property
    def btypes(self) -> list[int]:
        return [b.btype for b in self.blocks]


# --- DEFLATE (RFC 1951) -------------------------------------------------------------------------------
def _read_dynamic_tables(br: _BitReader, tr: DeflateBlockTrace | None,
                         cap_block: dict | None = None) -> tuple[_HuffTable, _HuffTable]:
    """RFC 1951 §3.2.7: read HLIT/HDIST/HCLEN, the code-length-code (19 syms in permuted order), then use
    it to decode the lit/len + distance code lengths (run-length ops 16/17/18, which may straddle the
    lit/len -> distance boundary). Build and return the two main canonical tables.

    ``cap_block`` (gated, off by default) is filled with the exact transmitted representation -- the
    code-length-code lengths AND the ``(cl_symbol, extra_value, extra_nbits)`` run-length stream that
    encodes the main-tree lengths -- so the Direction-1 re-encoder can reproduce the bytes."""
    hlit = br.read(5) + 257
    hdist = br.read(5) + 1
    hclen = br.read(4) + 4
    cl_lengths = [0] * 19
    for i in range(hclen):
        cl_lengths[_CL_ORDER[i]] = br.read(3)
    cl_table = _build_table(cl_lengths)
    cl_events: list | None = [] if cap_block is not None else None

    all_lengths: list[int] = []
    while len(all_lengths) < hlit + hdist:
        sym = _decode_symbol(br, cl_table)
        if sym < 16:
            all_lengths.append(sym)
            if cl_events is not None:
                cl_events.append((sym, 0, 0))
        elif sym == 16:                       # copy previous code length 3..6 times
            extra = br.read(2)
            all_lengths.extend([all_lengths[-1]] * (3 + extra))
            if cl_events is not None:
                cl_events.append((16, extra, 2))
        elif sym == 17:                       # repeat zero 3..10 times
            extra = br.read(3)
            all_lengths.extend([0] * (3 + extra))
            if cl_events is not None:
                cl_events.append((17, extra, 3))
        else:                                 # sym == 18: repeat zero 11..138 times
            extra = br.read(7)
            all_lengths.extend([0] * (11 + extra))
            if cl_events is not None:
                cl_events.append((18, extra, 7))
    if len(all_lengths) != hlit + hdist:
        raise ValueError("code-length run overflowed the lit/len+dist alphabet")

    litlen_lengths = all_lengths[:hlit]
    dist_lengths = all_lengths[hlit:hlit + hdist]
    if tr is not None:
        tr.hlit, tr.hdist, tr.hclen = hlit, hdist, hclen
        tr.cl_lengths = cl_lengths
        tr.litlen_lengths = litlen_lengths
        tr.dist_lengths = dist_lengths
    if cap_block is not None:
        cap_block.update(hlit=hlit, hdist=hdist, hclen=hclen, cl_lengths=cl_lengths,
                         cl_events=cl_events, litlen_lengths=litlen_lengths, dist_lengths=dist_lengths)
    return _build_table(litlen_lengths), _build_table(dist_lengths)


def _inflate_huffman_block(br: _BitReader, out: bytearray, litlen: _HuffTable, dist: _HuffTable,
                           tr: DeflateBlockTrace | None, tokens: list | None = None) -> None:
    """Decode one Huffman-coded block (fixed or dynamic) until the end-of-block symbol (256): literals
    go straight out; (length, distance) symbols replay an LZ77 back-reference (overlap-safe).

    ``tokens`` (gated, off by default) is filled with the exact per-token symbols + raw extra-bit
    values -- ``('lit', sym)`` or ``('match', litlen_sym, len_extra, len_nbits, dist_sym, dist_extra,
    dist_nbits)`` -- the Direction-1 re-encoder replays them via the canonical codes."""
    while True:
        sym = _decode_symbol(br, litlen)
        if sym < 256:
            out.append(sym)
            if tr is not None:
                tr.n_literals += 1
            if tokens is not None:
                tokens.append(("lit", sym))
        elif sym == 256:                       # end of block
            return
        else:                                  # length code 257..285
            li = sym - 257
            len_extra = br.read(_LENGTH_EXTRA[li])
            length = _LENGTH_BASE[li] + len_extra
            dsym = _decode_symbol(br, dist)
            dist_extra = br.read(_DIST_EXTRA[dsym])
            distance = _DIST_BASE[dsym] + dist_extra
            start = len(out) - distance
            if start < 0:
                raise ValueError("back-reference points before the start of the output")
            for i in range(length):            # byte-by-byte: correct even when distance < length (overlap)
                out.append(out[start + i])
            if tr is not None:
                tr.n_matches += 1
            if tokens is not None:
                tokens.append(("match", sym, len_extra, _LENGTH_EXTRA[li], dsym, dist_extra, _DIST_EXTRA[dsym]))


def _inflate(br: _BitReader, out: bytearray, trace: list | None, cap: list | None = None) -> None:
    """Decode RFC 1951 blocks from ``br`` into ``out`` until the BFINAL block is consumed.

    ``cap`` (gated, off by default) is appended one dict per block capturing the exact transmitted
    representation (block type, stored bytes, the dynamic header + code-length-code stream, the token
    symbols) -- enough for the Direction-1 re-encoder to reproduce the stream byte-for-byte."""
    while True:
        block_start_bits = br.nbits
        bfinal = br.read_bit()
        btype = br.read(2)
        tr = DeflateBlockTrace(btype=btype, bfinal=bool(bfinal)) if trace is not None else None
        cap_block: dict | None = {"bfinal": bfinal, "btype": btype} if cap is not None else None
        out_start = len(out)

        if btype == 0:                         # stored / no compression (RFC 1951 §3.2.4)
            br.align_to_byte()
            length = br.read(16)
            nlen = br.read(16)
            if nlen != (~length) & 0xFFFF:
                raise ValueError("invalid stored block lengths (LEN/NLEN mismatch)")
            stored = br.read_aligned_bytes(length)
            out.extend(stored)
            if tr is not None:
                tr.n_stored_bytes = length
            if cap_block is not None:
                cap_block["stored"] = stored
        elif btype == 1:                       # fixed Huffman (the conventional model -- §3.2.6)
            if tr is not None:
                tr.litlen_lengths = _FIXED_LITLEN_LENGTHS
                tr.dist_lengths = _FIXED_DIST_LENGTHS
            tokens: list | None = [] if cap_block is not None else None
            _inflate_huffman_block(br, out, _FIXED_LITLEN_TABLE, _FIXED_DIST_TABLE, tr, tokens)
            if cap_block is not None:
                cap_block["tokens"] = tokens
        elif btype == 2:                       # dynamic Huffman (the shipped model -- §3.2.7)
            litlen_table, dist_table = _read_dynamic_tables(br, tr, cap_block)
            tokens = [] if cap_block is not None else None
            _inflate_huffman_block(br, out, litlen_table, dist_table, tr, tokens)
            if cap_block is not None:
                cap_block["tokens"] = tokens
        else:
            raise ValueError("invalid BTYPE 3 (reserved)")

        if trace is not None:
            assert tr is not None
            tr.out_bytes = len(out) - out_start
            tr.payload_bits = br.nbits - block_start_bits
            trace.append(tr)
        if cap is not None:
            assert cap_block is not None
            cap.append(cap_block)
        if bfinal:
            return


# --- gzip wrapper (RFC 1952) --------------------------------------------------------------------------
def _parse_gzip_header(data: bytes, pos: int, tr: GzipTrace | None) -> int:
    """Parse one RFC 1952 member header starting at ``pos``; return the offset of the DEFLATE stream."""
    if data[pos] != 0x1F or data[pos + 1] != 0x8B:
        raise ValueError("not a gzip stream (bad magic)")
    if data[pos + 2] != 8:
        raise ValueError("unsupported gzip compression method (CM != 8)")
    flg = data[pos + 3]
    mtime = int.from_bytes(data[pos + 4:pos + 8], "little")
    # data[pos+8] = XFL, data[pos+9] = OS (both advisory; ignored)
    p = pos + 10
    if flg & 0x04:                             # FEXTRA
        xlen = int.from_bytes(data[p:p + 2], "little")
        p += 2 + xlen
    fname = None
    if flg & 0x08:                             # FNAME (zero-terminated)
        end = data.index(0, p)
        fname = data[p:end]
        p = end + 1
    if flg & 0x10:                             # FCOMMENT (zero-terminated)
        p = data.index(0, p) + 1
    if flg & 0x02:                             # FHCRC: low 2 bytes of crc32 over the header so far
        if (zlib.crc32(data[pos:p]) & 0xFFFF) != int.from_bytes(data[p:p + 2], "little"):
            raise ValueError("gzip header CRC16 mismatch")
        p += 2
    if tr is not None and tr.n_members == 0:   # record the first member's wrapper fields
        tr.fname = fname
        tr.mtime = mtime
    return p


def _decode_gzip(data: bytes, trace: GzipTrace | None) -> bytes:
    """Decode a gzip stream (one or more concatenated RFC 1952 members) to the original bytes, checking
    each member's CRC32 and ISIZE trailer."""
    out = bytearray()
    pos = 0
    n = len(data)
    while pos < n:
        pos = _parse_gzip_header(data, pos, trace)
        br = _BitReader(data, pos)
        member = bytearray()
        _inflate(br, member, trace.blocks if trace is not None else None)
        br.align_to_byte()
        pos = br.bytepos
        crc = int.from_bytes(data[pos:pos + 4], "little")
        isize = int.from_bytes(data[pos + 4:pos + 8], "little")
        pos += 8
        if zlib.crc32(member) != crc:
            raise ValueError("gzip CRC32 mismatch")
        if (len(member) & 0xFFFFFFFF) != isize:
            raise ValueError("gzip ISIZE mismatch")
        out.extend(member)
        if trace is not None:
            trace.n_members += 1
            trace.crc32 = crc
            trace.isize = isize
    return bytes(out)


# --- public API ---------------------------------------------------------------------------------------
def inflate(data: bytes, pos: int = 0) -> bytes:
    """Decode a raw DEFLATE stream (RFC 1951, no wrapper) to the original bytes.

    Byte-exact vs ``zlib.decompress(data, -15)``. This is the pure codec the spoon-feed instruments
    probe; :func:`decode_gzip` is the RFC 1952 container around it."""
    out = bytearray()
    _inflate(_BitReader(data, pos), out, None)
    return bytes(out)


def inflate_instrumented(data: bytes, pos: int = 0) -> tuple[bytes, list[DeflateBlockTrace]]:
    """Decode raw DEFLATE + return the per-block traces (block types, transmitted code lengths, the
    code-length-code, literal/match counts) -- the raw material for the M1 kill-switch."""
    out = bytearray()
    blocks: list[DeflateBlockTrace] = []
    _inflate(_BitReader(data, pos), out, blocks)
    return bytes(out), blocks


def inflate_captured(data: bytes, pos: int = 0) -> tuple[bytes, list[dict]]:
    """Decode raw DEFLATE + return the **capture tape**: one dict per block with the exact transmitted
    representation (block type, stored bytes, dynamic header + code-length-code stream, token symbols +
    raw extra bits). The Direction-1 (M2) re-encoder replays it to reproduce the stream byte-for-byte --
    the DEFLATE analog of LZMA's ``decode_lzma_alone_taped``. Pure observation: ``out`` is byte-identical
    to :func:`inflate`."""
    out = bytearray()
    cap: list[dict] = []
    _inflate(_BitReader(data, pos), out, None, cap)
    return bytes(out), cap


def gzip_member_regions(data: bytes) -> list[tuple[int, int, int]]:
    """For each gzip member, the byte offsets ``(header_end, deflate_end, member_end)`` -- so the
    Direction-1 re-encoder can splice a re-emitted DEFLATE body between the verbatim header and the
    8-byte CRC32+ISIZE trailer (which are raw bytes, not part of the bitstream). ``deflate_end`` is the
    byte boundary after the BFINAL block; ``member_end = deflate_end + 8``."""
    regions: list[tuple[int, int, int]] = []
    pos = 0
    n = len(data)
    while pos < n:
        header_end = _parse_gzip_header(data, pos, None)
        br = _BitReader(data, header_end)
        _inflate(br, bytearray(), None)
        br.align_to_byte()
        deflate_end = br.bytepos
        regions.append((header_end, deflate_end, deflate_end + 8))
        pos = deflate_end + 8
    return regions


def decode_gzip(data: bytes) -> bytes:
    """Decode a gzip stream (RFC 1952; multi-member aware) to the original bytes.

    Byte-exact vs ``gzip.decompress(data)``; CRC32 and ISIZE trailers are verified per member."""
    return _decode_gzip(data, None)


def decode_gzip_instrumented(data: bytes) -> tuple[bytes, GzipTrace]:
    """Decode a gzip stream + return a :class:`GzipTrace` (per-block + wrapper fields)."""
    trace = GzipTrace()
    out = _decode_gzip(data, trace)
    return out, trace


def compress_gzip(data: bytes, level: int = 6) -> bytes:
    """Reference encoder wrapper: ``gzip.compress`` with a fixed ``mtime=0`` (deterministic header).
    The anchor for every spoon-feed forward (round-trip == identity vs this)."""
    return gzip.compress(data, compresslevel=level, mtime=0)


def compress_raw_deflate(data: bytes, level: int = 6) -> bytes:
    """Reference encoder wrapper for **raw** DEFLATE (no zlib/gzip wrapper), via ``zlib`` wbits=-15.
    The anchor for :func:`inflate`."""
    co = zlib.compressobj(level, zlib.DEFLATED, -15)
    return co.compress(data) + co.flush()


if __name__ == "__main__":
    import numpy as np

    rng = np.random.default_rng(0)
    samples = {
        "empty": b"",
        "tiny": b"a",
        "text": b"the quick brown fox jumps over the lazy dog. " * 40,
        "runs": (b"x" * 7 + b"y" * 3) * 400,
        "random": bytes(rng.integers(0, 256, 4000, dtype=np.uint8)),
        "low_alpha": bytes(rng.integers(0, 4, 8000, dtype=np.uint8)),
    }
    print(f"{'shape':10s} {'lvl':>3s} {'ok':>4s} {'btypes':>14s} {'blocks':>6s} {'lit':>6s} {'match':>6s}")
    for name, data in samples.items():
        for lvl in (0, 6, 9):
            comp = compress_gzip(data, lvl)
            out, tr = decode_gzip_instrumented(comp)
            ok = out == data == gzip.decompress(comp)
            lit = sum(b.n_literals for b in tr.blocks)
            mat = sum(b.n_matches for b in tr.blocks)
            print(f"{name:10s} {lvl:3d} {str(ok):>4s} {str(tr.btypes):>14s} "
                  f"{len(tr.blocks):6d} {lit:6d} {mat:6d}")
            if not ok:
                print("  MISMATCH")
