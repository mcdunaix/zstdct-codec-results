"""Spoon-feeding DEFLATE -- the M1 kill-switch for substrate #4 (gzip, the most-deployed codec).

Substrate #4 is the **prototype static-model codec**: LZ77 -> two per-block canonical Huffman trees
(one for literals+lengths, one for distances), both *transmitted inside the block* (RFC 1951). It is
the cleanest member of the static-model family whose signature -- proved family-specific at N=3
(`findings/spoonfeed_synthesis.md`) -- is **Mode-A-by-counting**. Ground truth is the byte-exact
decoder :mod:`zstdct.gzip_decoder` (validated == stdlib ``gzip`` / ``zlib`` in M0).

The two-mode picture: a stage either thins the reachable SET (**Mode A**: a manifold from a *symmetry*
or a *counting* constraint) or only grades COST (**Mode B**: a bijection, set full). M1 classifies
DEFLATE's candidate objects against the **pre-registered** predictions
(`findings/spoonfeed_gzip_milestones.md`, locked in git *before* this module, commit 45ed604):

  1. the Huffman token payload -> **Mode B** (a bijection given the trees; predicted N=4),
  2. the two main trees -> **Mode-A-by-counting** (Kraft ``Sigma 2^-len = 1``; predicted PRESENT ->
     the counting cell N=3 *within* the static family -- the kill-switch's positive, contrast LZMA),
  3. the code-length-code -> **recursive counting** (a counting manifold transmitting the model that
     is itself a counting manifold -- "Kraft inside Kraft", the genuinely new mechanic),
  4. the canonical gauge -> **Mode-A-by-symmetry, but PINNED** (``gauge_size`` ``prod n_L! > 1`` exists
     yet is fixed by the canonical rule from the lengths -> NO covert channel through this mechanism;
     the slack-map prediction, tested on the codec everyone uses),
  5. an incomplete tree -> the **partial-decoder off-manifold** (``Sigma 2^-len != 1`` dangles the
     decoder -- and the off-manifold measure is *exactly* the Kraft deficit),
  6. the three block types -> the "where is the model" axis completed to THREE points (dynamic =
     **shipped** / fixed = **conventional-constant** / stored = **none**).

Each forward is tagged by how it is grounded -- the proven-vs-illustrated boundary the prior
substrates established:

**PROVEN (anchored to the exact decoder / real ``gzip``/``zlib``).**

- :func:`token_payload_roundtrip` builds a real raw-DEFLATE *fixed-Huffman* block from a token list and
  decodes it with the REAL :func:`zstdct.gzip_decoder.inflate`; round-trip == identity proves the
  payload is a bijection given the (conventional) trees. The codeword assignment reuses the
  codec-agnostic :func:`zstdct.spoonfeed.canonical_codes` **unchanged** -- the same canonical
  construction as the decoder's ``_build_table`` (the same-code-over-substrates evidence; the
  exhaustive round-trip proves it assigns *exactly* DEFLATE's codewords).
- :func:`fixed_code_is_total` / :func:`lz_backreference_constraint` -- the complete prefix code is TOTAL
  over the code space (any bit window decodes to a symbol, Mode B: the set stays full); the ONLY
  partiality is LZ77's back-reference rule + the never-used distance symbols 30/31, layers ABOVE/beside
  the entropy coder (the DEFLATE analog of LZMA's ``lz_reference_constraint``).
- :func:`real_block_trees` extracts the transmitted code-length vectors of real ``gzip.compress`` blocks
  with the byte-exact decoder; :func:`zstdct.spoonfeed.kraft_sum` == 1 on every one -- Mode-A-by-counting
  PRESENT (the kill-switch positive). :func:`recursive_kraft` reads the counting manifold at BOTH levels
  (the two main trees AND the code-length-code that ships them).
- :func:`canonical_assignment_pinned` / :func:`noncanonical_breaks_output` -- the gauge is real
  (``gauge_size`` ``prod n_L! > 1``) but **pinned**: the codeword<->symbol assignment is a deterministic
  function of the lengths (``canonical_codes`` reproduces the real decoder's table), and using any other
  representative of the ``prod n_L!`` orbit makes the REAL decoder emit DIFFERENT output -- so the gauge
  cannot carry a covert payload through this mechanism (the OPPOSITE of LZMA's exploitable
  final-interval slack; the slack-map prediction confirmed).
- :func:`manifold_holes` -- the partial-decoder off-manifold measured exactly: an incomplete code's
  dangling bit-patterns number ``2^maxlen * (1 - kraft_sum)`` -- falling off the prefix-code manifold
  *is* the Kraft deficit (the static-family off-manifold artifact; LZMA, total, had none).

**STRUCTURAL (a fact about the format / the decoder, measured not asserted).**

- :func:`where_is_the_model` -- the three block types are three distinct answers to "where is the
  model": dynamic (10) ships the trees (the counting manifold on the wire), fixed (01) uses
  *conventional* spec-constant trees (0 model bytes shipped, yet not adaptive), stored (00) ships no
  model at all (pure passthrough, Mode-B identity). The "where's the model" axis, now THREE points.
- :func:`fixed_trees_are_conventional_counting` -- the fixed trees are the SAME counting manifold
  (Kraft == 1) as a shipped one, only amortized into the standard instead of the stream.

The M2 additions (Direction-1 mapping: promote the per-token bijection to a real DEFLATE *byte stream*
on the encoder side, and map the recursive code-length-code in Direction 1; same writeup
``findings/spoonfeed_gzip_milestones.md``). The decoder gained an **additive, gated capture**
(``inflate_captured`` / ``gzip_member_regions``; ``inflate`` / ``decode_gzip`` byte-identical -- the M0
gate re-verified, the same precedent as LZMA's gated ``tape``):

- :func:`reencode_raw_deflate` / :func:`reencode_gzip` -- the **strongest Direction-1 anchor**: replay a
  *real* stream's capture tape (``reencode_from_tape``) and reproduce ``zlib`` / ``gzip``'s bytes
  **BYTE-FOR-BYTE** across the battery (all three block types, the code-length-code, every token + extra
  bit, the final padding; the gzip header/trailer copied verbatim, the DEFLATE body re-emitted). The
  encoder is the exact inverse of the byte-exact decoder -- the DEFLATE analog of LZMA's
  ``reencode_lzma_alone``. Because every code is canonical, the per-token bijection (M1) composes into
  the whole stream.
- :func:`recursive_cl_manifold` -- the nested counting manifold (the tree-of-trees) operationalized in
  the encoder: the code-length-code is Kraft-constrained (``cl_kraft == 1``) and its canonical
  assignment re-emits the main-tree lengths, which are themselves Kraft-constrained (Kraft inside
  Kraft, now byte-exact in Direction 1).
- :func:`encoder_assignment_is_forced` -- the gauge PINNED from the **encoder** side: the re-encoder
  assigns every codeword by the canonical rule (derived from the captured lengths) with **no free
  parameter** -- there is no analog of LZMA's ``reencode_with_offset`` (the free choice of number in the
  final interval). The DEFLATE encoder cannot pick a different representative of the gauge orbit.
- :func:`encode_token_payload` / :func:`decode_token_payload` -- the payload bijection given the trees,
  isolated from the LZ layer by the **known length** (decode exactly ``n`` tokens via the REAL
  ``_decode_symbol``, no LZ execution) -- the analog of LZMA's ``range_decode_bits(k)`` and bzip2's
  ``decode_payload(n)``.

The M3 additions (Direction 2 = encoder / image+preimage: push real originals through ``gzip.compress``
and read the realized shapes off the validated decode trace; same writeup
``findings/spoonfeed_gzip_milestones.md``):

- :func:`encoder_trace_gzip` -- the D2 instrument (analog of zstd's ``encoder_trace_zstd`` / LZMA's
  ``encoder_trace_lzma``): compress with REAL ``gzip``, decode instrumented, *assert byte-exact* vs the
  input and ``gzip.decompress``, so every shape read off the trace is one the real encoder produced.
- :func:`op_mix_gzip` -- the image: the realized LZ-decision mix; over a structure sweep it shifts
  monotonically match -> literal as structure dies (the D2 Finding-1 analog).
- :func:`litlen_counting_cells` -- the HEART: distinct i.i.d. draws round onto shared lit/len tree
  shapes -- a **counting CELL** (``best_genuine_width > 0``) -- but only on the gauge-invariant
  :func:`zstdct.spoonfeed.length_multiset`, gauge-scrambled on the per-symbol vector, **exactly as
  bzip2** (DEFLATE's LZ77 + literal/length interleaving permutes which symbol is frequent). Counting
  cell, N=3 within the static family -- the same ``coarsening_cell`` that read zstd's cell and LZMA's
  orbit.
- :func:`fixed_block_degenerate_cell` -- the pre-registered new measurement: the FIXED (conventional)
  model's preimage is a **degenerate counting cell** (``distinct_shapes == 1``, the manifold collapsed
  to one point, distinct contents, width > 0) -- the limiting case of the shipped model, NOT a symmetry
  orbit. :func:`gauge_has_no_orbit` -- the pinned gauge has no enumerable orbit (no offset freedom,
  unlike LZMA), so gzip is **cell-only** (with zstd).

No third-party dependency beyond the decoder (``gzip`` / ``zlib`` are stdlib; ``numpy`` only for the
random batteries; ``geometric_iid`` from :mod:`zstdct.spoonfeed_fse` for the i.i.d. cell battery, in
tests). Deterministic (every draw is seeded; ``gzip.compress`` is deterministic)."""

from __future__ import annotations

import gzip

import numpy as np

from zstdct.gzip_decoder import (
    _BitReader,
    _CL_ORDER,
    _DIST_BASE,
    _DIST_EXTRA,
    _FIXED_DIST_LENGTHS,
    _FIXED_LITLEN_LENGTHS,
    _FIXED_LITLEN_TABLE,
    _LENGTH_BASE,
    _LENGTH_EXTRA,
    _build_table,
    _decode_symbol,
    compress_gzip,
    compress_raw_deflate,
    decode_gzip_instrumented,
    gzip_member_regions,
    inflate,
    inflate_captured,
    inflate_instrumented,
)
from zstdct.spoonfeed import (
    canonical_codes,
    coarsening_cell,
    gauge_size,
    kraft_sum,
    length_multiset,
)

# ============================================================================
# Bit emission helpers (DEFLATE order: stream LSB-first, but Huffman codes MSB-first)
# ============================================================================


def pack_bits_lsb(bits: list[int]) -> bytes:
    """Pack a consumption-order bit list **LSB-first** into bytes -- the DEFLATE bit order, the exact
    inverse of :class:`zstdct.gzip_decoder._BitReader` (contrast bzip2's MSB-first
    :func:`zstdct.spoonfeed.pack_bits`). ``bits[0]`` becomes the LSB of byte 0."""
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j, b in enumerate(bits[i:i + 8]):
            byte |= b << j
        out.append(byte)
    return bytes(out)


def _emit_huff(bits: list[int], code_value: int, code_len: int) -> None:
    """Append a canonical Huffman codeword **MSB-first** (the order the decoder accumulates it:
    ``code = (code << 1) | bit``)."""
    for k in range(code_len - 1, -1, -1):
        bits.append((code_value >> k) & 1)


def _emit_lsb(bits: list[int], value: int, nbits: int) -> None:
    """Append an ``nbits`` field **LSB-first** (extra bits, header fields -- everything that is not a
    Huffman code)."""
    for k in range(nbits):
        bits.append((value >> k) & 1)


def length_to_symbol(length: int) -> tuple[int, int, int]:
    """A match length (3..258) -> ``(litlen_symbol, extra_value, n_extra_bits)`` (RFC 1951 §3.2.5).
    Length 258 -> symbol 285 with 0 extra bits (not 284 -- the classic boundary)."""
    for i in range(28, -1, -1):
        if length >= _LENGTH_BASE[i]:
            return 257 + i, length - _LENGTH_BASE[i], _LENGTH_EXTRA[i]
    raise ValueError(f"length {length} out of range [3, 258]")


def distance_to_symbol(distance: int) -> tuple[int, int, int]:
    """A match distance (1..32768) -> ``(distance_symbol, extra_value, n_extra_bits)`` (RFC 1951 §3.2.5)."""
    for i in range(29, -1, -1):
        if distance >= _DIST_BASE[i]:
            return i, distance - _DIST_BASE[i], _DIST_EXTRA[i]
    raise ValueError(f"distance {distance} out of range [1, 32768]")


# ============================================================================
# Object 1 -- the Huffman token payload = Mode B (a bijection given the trees; cost only)
# ============================================================================


def execute_tokens(tokens: list[tuple]) -> bytes:
    """The output a DEFLATE token sequence denotes: ``('lit', byte)`` emits a byte, ``('match', length,
    distance)`` replays an LZ77 back-reference (overlap-safe). The ground-truth the payload round-trip
    must reproduce."""
    out = bytearray()
    for tok in tokens:
        if tok[0] == "lit":
            out.append(tok[1])
        else:
            length, distance = tok[1], tok[2]
            start = len(out) - distance
            for i in range(length):
                out.append(out[start + i])
    return bytes(out)


def build_fixed_deflate(tokens: list[tuple], final: bool = True) -> bytes:
    """Build a complete raw-DEFLATE **fixed-Huffman** block (BTYPE 01) from a token list: BFINAL +
    BTYPE, then each token's canonical code (lit/len) + extra bits + distance code + extra bits, then
    the end-of-block symbol 256. The codeword assignment is the codec-agnostic
    :func:`zstdct.spoonfeed.canonical_codes` over the *conventional* fixed lengths -- decoded by the
    REAL :func:`zstdct.gzip_decoder.inflate`."""
    litlen_codes = canonical_codes(_FIXED_LITLEN_LENGTHS)
    dist_codes = canonical_codes(_FIXED_DIST_LENGTHS)
    bits: list[int] = [1 if final else 0]   # BFINAL
    _emit_lsb(bits, 1, 2)                    # BTYPE = 01 (fixed Huffman), LSB-first
    for tok in tokens:
        if tok[0] == "lit":
            _emit_huff(bits, *litlen_codes[tok[1]])
        else:
            length, distance = tok[1], tok[2]
            sym, extra, nextra = length_to_symbol(length)
            _emit_huff(bits, *litlen_codes[sym])
            _emit_lsb(bits, extra, nextra)
            dsym, dextra, dnextra = distance_to_symbol(distance)
            _emit_huff(bits, *dist_codes[dsym])
            _emit_lsb(bits, dextra, dnextra)
    _emit_huff(bits, *litlen_codes[256])     # end of block
    return pack_bits_lsb(bits)


def token_payload_roundtrip(tokens: list[tuple]) -> bool:
    """PROVEN: ``inflate(build_fixed_deflate(tokens)) == execute_tokens(tokens)`` -- the Huffman token
    payload is a bijection given the trees, anchored end-to-end by the byte-exact decoder. (Reuses
    :func:`zstdct.spoonfeed.canonical_codes` unchanged; the exhaustive round-trip proves it assigns
    *exactly* DEFLATE's fixed codewords, not merely a mutual inverse.)"""
    return inflate(build_fixed_deflate(tokens)) == execute_tokens(tokens)


def fixed_code_is_total(seed: int, trials: int = 4000) -> dict:
    """The complete fixed lit/len prefix code is TOTAL over the code space: feed random 9-bit windows
    to the REAL ``_decode_symbol`` -- every one decodes to a valid symbol, none dangles (Mode B: the
    entropy stage's reachable set is full). Returns ``{decoded, dangled}`` with ``dangled == 0``.

    (9 bits = the fixed code's max length, so a full window always contains a complete codeword; this
    is the entropy-layer totality, the analog of LZMA's ``range_decoder_totality``.)"""
    rng = np.random.default_rng(seed)
    decoded = dangled = 0
    for _ in range(trials):
        window = int(rng.integers(0, 512))            # 9 random bits
        br = _BitReader(pack_bits_lsb([(window >> k) & 1 for k in range(9)]), 0)
        try:
            _decode_symbol(br, _FIXED_LITLEN_TABLE)
            decoded += 1
        except (ValueError, IndexError):
            dangled += 1
    return {"decoded": decoded, "dangled": dangled}


def lz_backreference_constraint() -> dict:
    """The ONLY partiality in full-block decoding lives ABOVE/beside the entropy coder, as in LZMA:
    (a) a back-reference whose distance exceeds the output so far is invalid (LZ77's referential rule),
    and (b) distance symbols 30/31 never occur (an invalid distance). Both make the REAL decoder raise,
    while the prefix code itself is total. Returns ``{valid_ok, backref_fail, baseline}``: a valid match
    decodes, an out-of-range back-reference fails -- the thinning belongs to the LZ layer, not Mode B."""
    valid = [("lit", 65), ("lit", 66), ("match", 4, 2)]        # dist 2 <= 2 bytes out: OK
    bad = [("lit", 65), ("match", 4, 50)]                       # dist 50 > 1 byte out: invalid back-ref
    valid_ok = inflate(build_fixed_deflate(valid)) == execute_tokens(valid)
    try:
        inflate(build_fixed_deflate(bad))
        backref_fail = False
    except (ValueError, IndexError):
        backref_fail = True
    return {"valid_ok": valid_ok, "backref_fail": backref_fail, "baseline": execute_tokens(valid)}


def payload_cost(litlen_lengths: list[int], tokens: list[tuple]) -> dict:
    """The Mode-B cost: a token coded against a complete prefix code spends exactly its code length in
    bits (``-log2(2^-len) = len``) + the length/distance extra bits. Returns the literal/length code
    bits, the extra bits, and the total -- a graded COST, no membership constraint. (Distances would
    add the dist-tree bits; the lit/len side suffices to show cost = ``Sigma`` code lengths.)"""
    litlen_codes = canonical_codes(litlen_lengths)
    code_bits = extra_bits = 0
    for tok in tokens:
        if tok[0] == "lit":
            code_bits += litlen_codes[tok[1]][1]
        else:
            sym, _extra, nextra = length_to_symbol(tok[1])
            code_bits += litlen_codes[sym][1]
            extra_bits += nextra
    return {"code_bits": code_bits, "extra_bits": extra_bits, "total_bits": code_bits + extra_bits}


# ============================================================================
# Object 2 -- Mode-A-by-counting: PRESENT (the static-family signature; the kill-switch positive)
#   + Object 2b -- recursive counting (the code-length-code: Kraft inside Kraft)
# ============================================================================


def real_block_trees(data: bytes, level: int = 9) -> list[dict]:
    """Extract the transmitted code-length vectors of the DYNAMIC blocks of real ``gzip.compress(data,
    level)`` via the byte-exact instrumented decoder. Each entry: ``{litlen_lengths, dist_lengths,
    cl_lengths}`` -- the two main trees + the code-length-code that ships them. The transmitted code
    lengths *are* the Mode-A counting manifold (contrast LZMA, which transmits no model)."""
    _out, tr = decode_gzip_instrumented(compress_gzip(data, level))
    return [
        {"litlen_lengths": b.litlen_lengths, "dist_lengths": b.dist_lengths, "cl_lengths": b.cl_lengths}
        for b in tr.blocks
        if b.btype == 2
    ]


def recursive_kraft(data: bytes, level: int = 9) -> list[dict]:
    """The counting manifold read at BOTH levels of a real dynamic block, with the codec-agnostic
    :func:`zstdct.spoonfeed.kraft_sum` **unchanged**: the two main trees (``litlen``/``dist``, the
    shipped model) AND the **code-length-code** (``cl``, the manifold that ships them) each obey Kraft
    ``Sigma 2^-len == 1``. "Kraft inside Kraft" -- a counting manifold whose own description is a second
    counting manifold (bzip2 ships lengths via a delta walk, zstd FSE-codes them; DEFLATE Huffman-codes
    them, so the description is itself a counting object). Returns per-block
    ``{litlen_kraft, dist_kraft, cl_kraft}`` (each == 1)."""
    out = []
    for t in real_block_trees(data, level):
        out.append({
            "litlen_kraft": kraft_sum(t["litlen_lengths"]),
            "dist_kraft": kraft_sum(t["dist_lengths"]),
            "cl_kraft": kraft_sum(t["cl_lengths"]),
        })
    return out


# ============================================================================
# Object 3 -- Mode-A-by-symmetry: the canonical gauge is PRESENT but PINNED (no covert channel)
# ============================================================================


def canonical_assignment_pinned(lengths: list[int], symbols: list[int]) -> bool:
    """PROVEN: the codeword<->symbol assignment is a deterministic function of the lengths. Encode
    ``symbols`` with :func:`zstdct.spoonfeed.canonical_codes` (the codec-agnostic canonical rule) and
    decode with the REAL ``_build_table`` / ``_decode_symbol``; round-trip == identity for ANY length
    vector. So the assignment is *derived on both sides from the lengths alone* -- there is nothing on
    the wire to choose a different representative of the gauge orbit."""
    codes = canonical_codes(lengths)
    bits: list[int] = []
    for s in symbols:
        _emit_huff(bits, *codes[s])
    table = _build_table(lengths)
    br = _BitReader(pack_bits_lsb(bits), 0)
    return [_decode_symbol(br, table) for _ in range(len(symbols))] == symbols


def noncanonical_breaks_output(lengths: list[int], symbols: list[int]) -> dict:
    """The gauge is PINNED, not a channel: ``gauge_size`` says ``prod n_L! > 1`` relabelings exist, but
    using any OTHER representative (swap two equal-length codewords) makes the REAL decoder emit
    DIFFERENT output -- the decoder always applies the canonical rule, and the format transmits no
    assignment. So the relabeling freedom cannot carry a covert payload **through this mechanism** (the
    OPPOSITE of LZMA's final-interval slack, which preserves the output). Returns ``{gauge, canonical_ok,
    swapped_differs}``: ``gauge > 1`` (freedom exists), ``canonical_ok`` (canonical round-trips),
    ``swapped_differs`` (the swapped assignment decodes to something else).

    (Honest scope: DEFLATE *does* have a covert channel -- the LZ *parse* freedom, suboptimal
    match/literal choices -- but that is a different, higher layer; the canonical-Huffman gauge, the
    mechanism the slack-map prediction is about, is pinned.)"""
    g, by_len = gauge_size(lengths)
    canonical_ok = canonical_assignment_pinned(lengths, symbols)
    # find a length with >= 2 codewords and swap two of its symbols' codes (a non-identity gauge element)
    codes = dict(canonical_codes(lengths))
    swap_len = next((L for L, c in by_len.items() if c >= 2), None)
    swapped_differs = False
    if swap_len is not None:
        same_len_syms = sorted(s for s, (_cv, L) in codes.items() if L == swap_len)
        a, b = same_len_syms[0], same_len_syms[1]
        codes[a], codes[b] = codes[b], codes[a]                # a non-canonical (but length-preserving) assignment
        bits: list[int] = []
        for s in symbols:
            _emit_huff(bits, *codes[s])
        table = _build_table(lengths)                          # the REAL decoder still uses canonical
        br = _BitReader(pack_bits_lsb(bits), 0)
        decoded = [_decode_symbol(br, table) for _ in range(len(symbols))]
        swapped_differs = decoded != symbols
    return {"gauge": g, "canonical_ok": canonical_ok, "swapped_differs": swapped_differs}


# ============================================================================
# The partial-decoder off-manifold = an incomplete tree (and it is EXACTLY the Kraft deficit)
# ============================================================================


def manifold_holes(lengths: list[int]) -> dict:
    """The off-manifold measured exactly. Enumerate every ``2^maxlen`` bit-pattern and decode its
    prefix with the REAL canonical machinery; a pattern with no valid codeword prefix is a **hole**
    (where ``_decode_symbol`` dangles). For a complete code (``Sigma 2^-len == 1``) there are **0**
    holes -- it is total over the code space (Mode B). For an incomplete code the holes number
    **exactly** ``2^maxlen * (1 - kraft_sum)``: each codeword of length ``L`` covers ``2^(maxlen-L)``
    patterns, so coverage ``= 2^maxlen * kraft``. **Falling off the prefix-code manifold IS the Kraft
    deficit** -- the static-family off-manifold artifact, here pinned to the counting constraint with an
    exact identity (LZMA, being total, has none). Returns ``{kraft, max_len, patterns, holes,
    predicted_holes}``.

    **Scope: the identity holds for ``kraft <= 1`` only** (complete or incomplete codes). For an
    *overfull* code (``kraft > 1``) the codewords *collide* rather than dangle, ``holes`` can be 0 while
    ``predicted_holes`` goes negative -- ``canonical_codes`` would mis-assign such a vector, so it is
    outside the prefix-code regime. ``measured_holes`` (the brute count) is always valid; the formula is
    not."""
    table = _build_table(lengths)
    mx = table.max_len
    holes = 0
    for pattern in range(1 << mx):
        code = 0
        found = False
        for length in range(1, mx + 1):
            code = (code << 1) | ((pattern >> (mx - length)) & 1)
            if table.limit[length] >= 0 and table.first_code[length] <= code <= table.limit[length]:
                found = True
                break
        if not found:
            holes += 1
    kraft = kraft_sum(lengths)
    predicted = (1 << mx) * (1 - kraft)                        # exact: a Fraction equal to an integer
    return {
        "kraft": kraft,
        "max_len": mx,
        "patterns": 1 << mx,
        "holes": holes,
        "predicted_holes": predicted,
    }


# ============================================================================
# The "where is the model" axis, completed to THREE points (the genuinely new structural thing)
# ============================================================================


def where_is_the_model(data: bytes, level: int) -> list[dict]:
    """Classify each block of real ``gzip.compress(data, level)`` by WHERE its model lives -- the three
    DEFLATE block types are three distinct answers (STRUCTURAL, read off the byte-exact trace):

    - **stored (btype 0)** -> ``model='none'``: no entropy coding, pure passthrough (Mode-B identity,
      1 byte out per byte in + framing).
    - **fixed (btype 1)** -> ``model='conventional'``: 0 model bytes shipped, yet NOT adaptive -- the
      trees are spec constants (a new third point: shipped / adaptive-regenerated[LZMA] / conventional).
    - **dynamic (btype 2)** -> ``model='shipped'``: the trees are transmitted (the counting manifold on
      the wire, like bzip2 / zstd).

    Returns one dict per block: ``{btype, model, kraft_litlen}`` (``kraft_litlen`` is the lit/len tree's
    Kraft sum for fixed/dynamic -- == 1 either way; ``None`` for stored)."""
    _out, tr = decode_gzip_instrumented(compress_gzip(data, level))
    out = []
    for b in tr.blocks:
        model = {0: "none", 1: "conventional", 2: "shipped"}[b.btype]
        kraft_litlen = kraft_sum(b.litlen_lengths) if b.litlen_lengths is not None else None
        out.append({"btype": b.btype, "model": model, "kraft_litlen": kraft_litlen})
    return out


def fixed_trees_are_conventional_counting() -> dict:
    """The conventional (fixed) model is the SAME counting manifold as a shipped one -- just amortized
    into the standard instead of the stream. The fixed lit/len and distance trees (RFC 1951 §3.2.6
    constants) each obey Kraft ``Sigma 2^-len == 1``, with ``gauge_size > 1`` (the relabeling freedom is
    present but, again, pinned by convention). Returns ``{litlen_kraft, dist_kraft, litlen_gauge,
    model_bytes_shipped}`` -- counting present, 0 bytes shipped."""
    return {
        "litlen_kraft": kraft_sum(_FIXED_LITLEN_LENGTHS),
        "dist_kraft": kraft_sum(_FIXED_DIST_LENGTHS),
        "litlen_gauge": gauge_size(_FIXED_LITLEN_LENGTHS)[0],
        "model_bytes_shipped": 0,
    }


# ============================================================================
# M2 -- Direction 1: promote the payload bijection to a real DEFLATE byte STREAM (the encoder side),
#   and map the recursive code-length-code (the tree-of-trees) in Direction 1. The strongest anchor:
#   replay a real stream's capture tape and reproduce zlib/gzip's bytes BYTE-FOR-BYTE -- the DEFLATE
#   analog of LZMA's reencode_lzma_alone. Because every DEFLATE code is canonical (derived from the
#   lengths), the encoder has NO assignment freedom (contrast LZMA's free final-interval offset) -- the
#   gauge is pinned from the encoder side too.
# ============================================================================


def _emit_tokens(bits: list[int], tokens: list[tuple], litlen_codes: dict, dist_codes: dict) -> None:
    """Emit a captured token list (from :func:`zstdct.gzip_decoder.inflate_captured`) via the canonical
    codes: ``('lit', sym)`` -> the lit/len codeword; ``('match', litlen_sym, len_extra, len_nbits,
    dist_sym, dist_extra, dist_nbits)`` -> lit/len codeword + extra + distance codeword + extra."""
    for tok in tokens:
        if tok[0] == "lit":
            _emit_huff(bits, *litlen_codes[tok[1]])
        else:
            _, ls, le, ln, ds, de, dn = tok
            _emit_huff(bits, *litlen_codes[ls])
            _emit_lsb(bits, le, ln)
            _emit_huff(bits, *dist_codes[ds])
            _emit_lsb(bits, de, dn)


def _emit_block(bits: list[int], blk: dict) -> None:
    """Emit one captured block (the inverse of ``_inflate``'s one-block decode). Stored aligns + LEN/NLEN
    + raw bytes; fixed emits tokens via the conventional codes; dynamic emits HLIT/HDIST/HCLEN, the
    code-length-code lengths (in ``_CL_ORDER``), the captured run-length stream **via the cl canonical
    codes** (the recursive manifold, re-emitted), then the tokens, then the end-of-block symbol 256."""
    bits.append(blk["bfinal"])
    _emit_lsb(bits, blk["btype"], 2)
    if blk["btype"] == 0:
        while len(bits) % 8:                          # align to a byte boundary (stored bodies are byte-aligned)
            bits.append(0)
        stored = blk["stored"]
        _emit_lsb(bits, len(stored), 16)
        _emit_lsb(bits, (~len(stored)) & 0xFFFF, 16)
        for b in stored:
            _emit_lsb(bits, b, 8)
    elif blk["btype"] == 1:
        ll = canonical_codes(_FIXED_LITLEN_LENGTHS)
        dd = canonical_codes(_FIXED_DIST_LENGTHS)
        _emit_tokens(bits, blk["tokens"], ll, dd)
        _emit_huff(bits, *ll[256])
    else:                                             # dynamic
        _emit_lsb(bits, blk["hlit"] - 257, 5)
        _emit_lsb(bits, blk["hdist"] - 1, 5)
        _emit_lsb(bits, blk["hclen"] - 4, 4)
        cl = blk["cl_lengths"]
        for i in range(blk["hclen"]):
            _emit_lsb(bits, cl[_CL_ORDER[i]], 3)
        cl_codes = canonical_codes(cl)                # the code-length-code, canonical (Kraft-constrained)
        for sym, extra, nbits in blk["cl_events"]:    # re-emit the main-tree lengths through it (recursive)
            _emit_huff(bits, *cl_codes[sym])
            _emit_lsb(bits, extra, nbits)
        ll = canonical_codes(blk["litlen_lengths"])
        dd = canonical_codes(blk["dist_lengths"]) if max(blk["dist_lengths"]) > 0 else {}
        _emit_tokens(bits, blk["tokens"], ll, dd)
        _emit_huff(bits, *ll[256])


def reencode_from_tape(cap: list[dict]) -> bytes:
    """Re-emit a raw-DEFLATE byte stream from a capture tape (:func:`zstdct.gzip_decoder.inflate_captured`),
    padding the final byte with zeros to a boundary (as zlib does). Every codeword is the canonical code
    derived from the captured lengths, so this composes the per-token bijection (M1) into a whole stream."""
    bits: list[int] = []
    for blk in cap:
        _emit_block(bits, blk)
    while len(bits) % 8:                               # pad the final byte (zlib pads with 0 bits)
        bits.append(0)
    return pack_bits_lsb(bits)


def reencode_raw_deflate(raw: bytes) -> bytes:
    """PROVEN, the strongest Direction-1 anchor: decode ``raw`` to its capture tape and re-emit it ->
    ``== raw`` **byte-for-byte** across the battery. Reproduces ``zlib``'s actual DEFLATE bitstream
    (all three block types, the code-length-code, every token + extra bit, the final padding) from the
    decoder's own captured decisions -- so this encoder is the exact inverse of the byte-exact decoder,
    the DEFLATE analog of LZMA's ``reencode_lzma_alone``. (Maps the recursive code-length-code in
    Direction 1: the captured run-length stream is re-emitted through the cl canonical codes.)"""
    _out, cap = inflate_captured(raw)
    return reencode_from_tape(cap)


def reencode_gzip(comp: bytes) -> bytes:
    """PROVEN: reproduce a whole gzip stream **byte-for-byte**. The RFC 1952 header and the 8-byte
    CRC32+ISIZE trailer are raw bytes (not bitstream), copied verbatim; the DEFLATE body between them is
    re-emitted via :func:`reencode_raw_deflate`. Multi-member aware. ``== comp`` across the battery."""
    out = bytearray()
    prev = 0
    for header_end, deflate_end, member_end in gzip_member_regions(comp):
        out += comp[prev:header_end]                  # verbatim RFC 1952 header
        out += reencode_raw_deflate(comp[header_end:deflate_end])
        out += comp[deflate_end:member_end]           # verbatim CRC32 + ISIZE trailer
        prev = member_end
    return bytes(out)


def recursive_cl_manifold(raw: bytes) -> list[dict]:
    """The Direction-1 map of the nested counting manifold (the tree-of-trees): for each dynamic block,
    the code-length-code is a counting manifold (``kraft_sum(cl_lengths) == 1``) whose canonical
    assignment transmits the main-tree lengths -- and the re-emission through it is byte-exact. Returns
    per dynamic block ``{cl_kraft, litlen_kraft, dist_kraft, n_cl_events}``: Kraft == 1 at the cl level
    AND the main-tree level (Kraft inside Kraft, now operationalized in the encoder)."""
    _out, cap = inflate_captured(raw)
    out = []
    for blk in cap:
        if blk["btype"] == 2:
            out.append({
                "cl_kraft": kraft_sum(blk["cl_lengths"]),
                "litlen_kraft": kraft_sum(blk["litlen_lengths"]),
                "dist_kraft": kraft_sum(blk["dist_lengths"]),
                "n_cl_events": len(blk["cl_events"]),
            })
    return out


def encoder_assignment_is_forced(raw: bytes) -> dict:
    """The gauge PINNED, from the encoder side (Direction 1): the re-encoder assigns every codeword by
    the canonical rule (``canonical_codes`` from the captured lengths) with **no free parameter** -- it
    reproduces ``raw`` exactly and there is no analog of LZMA's ``reencode_with_offset`` (the free choice
    of number in the final interval). Returns ``{byte_exact, has_offset_parameter}`` =
    ``{True, False}``: the DEFLATE encoder cannot choose a different representative of the gauge orbit."""
    return {"byte_exact": reencode_raw_deflate(raw) == raw, "has_offset_parameter": False}


# --- the payload bijection given the trees, isolated from the LZ layer (known length) ---------------
def encode_token_payload(tokens: list[tuple], litlen_lengths: list[int], dist_lengths: list[int]) -> bytes:
    """Encode a token list as a raw Huffman payload (no block header, no end-of-block) against GIVEN
    trees -- the inverse of :func:`decode_token_payload`. ``tokens`` use decoded values: ``('lit',
    byte)`` / ``('match', length, distance)``."""
    ll = canonical_codes(litlen_lengths)
    dd = canonical_codes(dist_lengths)
    bits: list[int] = []
    for tok in tokens:
        if tok[0] == "lit":
            _emit_huff(bits, *ll[tok[1]])
        else:
            length, distance = tok[1], tok[2]
            sym, extra, nextra = length_to_symbol(length)
            _emit_huff(bits, *ll[sym])
            _emit_lsb(bits, extra, nextra)
            dsym, dextra, dnextra = distance_to_symbol(distance)
            _emit_huff(bits, *dd[dsym])
            _emit_lsb(bits, dextra, dnextra)
    return pack_bits_lsb(bits)


def decode_token_payload(stream: bytes, litlen_lengths: list[int], dist_lengths: list[int],
                         n_tokens: int) -> list[tuple]:
    """Decode EXACTLY ``n_tokens`` from ``stream`` via the REAL ``_decode_symbol`` against GIVEN trees,
    with NO LZ execution -- the prefix-code payload bijection isolated from the LZ back-reference layer
    (the known length is what isolates it, the analog of LZMA's ``range_decode_bits(k)`` and bzip2's
    ``decode_payload(n)``). Returns the tokens as decoded ``('lit', byte)`` / ``('match', length,
    distance)``; anchored by ``decode_token_payload(encode_token_payload(t, ..), .., len(t)) == t``."""
    ll = _build_table(litlen_lengths)
    dd = _build_table(dist_lengths)
    br = _BitReader(stream, 0)
    toks: list[tuple] = []
    for _ in range(n_tokens):
        sym = _decode_symbol(br, ll)
        if sym < 256:
            toks.append(("lit", sym))
        else:
            li = sym - 257
            length = _LENGTH_BASE[li] + br.read(_LENGTH_EXTRA[li])
            dsym = _decode_symbol(br, dd)
            distance = _DIST_BASE[dsym] + br.read(_DIST_EXTRA[dsym])
            toks.append(("match", length, distance))
    return toks


# ============================================================================
# M3 -- Direction 2 (encoder / image+preimage): push real originals through gzip.compress and read the
#   realized shapes off the VALIDATED decode trace. The sharp question (pre-registered): does the
#   counting CELL appear on the gauge-invariant multiset (the same coarsening_cell that read zstd/bzip2),
#   as a static-model member should? And the new measurement: is the FIXED (conventional) model's
#   preimage a degenerate counting cell? The gauge is PINNED (M1/M2), so there is NO enumerable gauge
#   orbit -- gzip's only Mode-A preimage is the cell (cell-only, like zstd).
# ============================================================================


def encoder_trace_gzip(data: bytes, level: int = 9) -> tuple[bytes, list]:
    """Push an original through the REAL ``gzip.compress`` and read every block's realized shape off the
    validated decode trace -- the Direction-2 instrument, the analog of zstd's ``encoder_trace_zstd`` /
    LZMA's ``encoder_trace_lzma``. Faithfulness is *inherited*: it asserts the instrumented decode equals
    BOTH ``data`` and stdlib ``gzip.decompress`` (the M0 anchor), so every shape read off the returned
    per-block traces is one the real encoder produced. Returns ``(compressed, blocks)``."""
    comp = compress_gzip(data, level)
    out, tr = decode_gzip_instrumented(comp)
    assert out == data == gzip.decompress(comp), "encoder_trace_gzip: instrumented decode is not byte-exact"
    return comp, tr.blocks


def real_litlen_lengths(data: bytes, level: int = 9) -> list[int] | None:
    """The lit/len code-length vector of the first DYNAMIC block of real ``zlib``-compressed ``data``
    (the content-stage tree, the analog of zstd's ``real_literal_lengths``). ``None`` if no dynamic
    block (a tiny/incompressible input used a fixed or stored block instead)."""
    _out, blocks = inflate_instrumented(compress_raw_deflate(data, level))
    for b in blocks:
        if b.btype == 2:
            return b.litlen_lengths
    return None


def op_mix_gzip(blocks: list) -> dict:
    """The image, off the validated trace: the realized LZ-decision shape (literals vs matches) of a
    DEFLATE stream. Returns ``n_literals`` / ``n_matches`` / ``n_tokens`` / ``literal_frac`` /
    ``match_frac``. Over a structure sweep (:func:`zstdct.spoonfeed.structured_input`, ``f: 0 -> 1``) the
    mix shifts monotonically match -> literal as structure dies -- the bzip2/zstd/LZMA D2 Finding-1
    analog, on DEFLATE's token stream."""
    lit = sum(b.n_literals for b in blocks)
    mat = sum(b.n_matches for b in blocks)
    n = lit + mat
    return {
        "n_literals": lit,
        "n_matches": mat,
        "n_tokens": n,
        "literal_frac": lit / n if n else 0.0,
        "match_frac": mat / n if n else 0.0,
    }


def litlen_counting_cells(datas: list[bytes], level: int = 9) -> tuple[dict, dict, int]:
    """The HEART of M3: distinct inputs -> their real dynamic-block lit/len trees + byte histograms ->
    the codec-agnostic :func:`zstdct.spoonfeed.coarsening_cell` on BOTH the per-symbol length vector AND
    the gauge-invariant :func:`zstdct.spoonfeed.length_multiset`. The counting CELL appears on the
    **multiset** (``best_genuine_width > 0`` -- distinct histograms rounding onto one tree SHAPE), but
    is gauge-scrambled on the per-symbol vector (~injective) -- **exactly as bzip2**: DEFLATE's LZ77 +
    literal/length interleaving permutes which symbol is frequent, like bzip2's BWT, so only the
    gauge-quotiented coordinate coarsens. Returns ``(vec_stats, multiset_stats, n_dynamic)``. The same
    ``coarsening_cell`` that read zstd's cell and LZMA's orbit -- counting cell, N=3 within the static
    family."""
    vec, ms, hists = [], [], []
    for data in datas:
        lengths = real_litlen_lengths(data, level)
        if lengths is None:
            continue
        assert kraft_sum(lengths) == 1                    # Mode-A counting object, on Kraft (reused helper)
        hist = tuple(int(c) for c in np.bincount(np.frombuffer(data, np.uint8), minlength=256))
        vec.append(tuple(lengths))
        ms.append(length_multiset(lengths))
        hists.append(hist)
    return coarsening_cell(vec, hists), coarsening_cell(ms, hists), len(ms)


def fixed_block_degenerate_cell(datas: list[bytes], level: int = 9) -> tuple[dict, int]:
    """The pre-registered new measurement: inputs that compress to a FIXED block ALL round onto the ONE
    conventional lit/len tree. ``coarsening_cell`` reports ``distinct_shapes == 1`` (the counting
    manifold collapsed to a single point) with distinct contents (``best_genuine_distinct >= 2``,
    ``width > 0``) -- a **DEGENERATE counting cell**: the limiting case of the shipped model (same
    counting manifold, restricted to one cell; the description amortized into the spec and the rounding
    made trivial -- everything maps to the one conventional tree). NOT a symmetry orbit (its contents are
    distinct, width > 0; an orbit has one content, width 0). Returns ``(stats, n_fixed)``."""
    ms, hists = [], []
    for data in datas:
        _out, blocks = inflate_instrumented(compress_raw_deflate(data, level))
        if blocks and all(b.btype == 1 for b in blocks):
            ms.append(length_multiset(_FIXED_LITLEN_LENGTHS))
            hists.append(tuple(int(c) for c in np.bincount(np.frombuffer(data, np.uint8), minlength=256)))
    return coarsening_cell(ms, hists), len(ms)


def gauge_has_no_orbit(data: bytes, level: int = 9) -> dict:
    """The Direction-2 reading of the pinned gauge: gzip has **no enumerable gauge orbit** -- unlike
    LZMA's ``gauge_orbit`` (m equal-length re-encodings of one output), the canonical rule fixes the
    single representative, so there is no free offset to spread (``encoder_assignment_is_forced`` ->
    ``has_offset_parameter == False``). DEFLATE's only Mode-A preimage is the counting CELL, never a
    symmetry orbit -- gzip is **cell-only** (with zstd). Returns ``{has_offset_parameter, byte_exact,
    note}``."""
    forced = encoder_assignment_is_forced(compress_raw_deflate(data, level))
    return {**forced, "note": "canonical gauge pinned -> no orbit; Mode-A preimage is the counting cell only"}


# convenience: the reference anchors (mirrors spoonfeed_lzma's re-exports)
__all__ = [
    "pack_bits_lsb",
    "length_to_symbol",
    "distance_to_symbol",
    "execute_tokens",
    "build_fixed_deflate",
    "token_payload_roundtrip",
    "fixed_code_is_total",
    "lz_backreference_constraint",
    "payload_cost",
    "real_block_trees",
    "recursive_kraft",
    "canonical_assignment_pinned",
    "noncanonical_breaks_output",
    "manifold_holes",
    "where_is_the_model",
    "fixed_trees_are_conventional_counting",
    # M2 (Direction 1)
    "reencode_from_tape",
    "reencode_raw_deflate",
    "reencode_gzip",
    "recursive_cl_manifold",
    "encoder_assignment_is_forced",
    "encode_token_payload",
    "decode_token_payload",
    # M3 (Direction 2)
    "encoder_trace_gzip",
    "real_litlen_lengths",
    "op_mix_gzip",
    "litlen_counting_cells",
    "fixed_block_degenerate_cell",
    "gauge_has_no_orbit",
    "compress_gzip",            # the reference encoder anchor
    "compress_raw_deflate",     # the raw-DEFLATE reference encoder anchor
    "inflate",                  # the reference decoder anchor
]
