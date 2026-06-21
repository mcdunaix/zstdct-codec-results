#!/usr/bin/env python3
"""
oracle.py — the trusted reference + byte-exact validator for generated decoders.

This is the legitimacy anchor of the decomposer-inside-the-loop. A machine
(Codewhale / DeepSeek, via `codewhale exec`) writes the decoder; NOTHING the
machine writes decides pass/fail. Pass/fail is decided HERE, by trusted code,
measured against the real codec:

  - the corpus is OURS,
  - the compression is done by the REAL stdlib codec (the oracle),
  - the comparison is byte-exact and counted by US,
  - the Evidence handed to the gate is built from those measured counts,
    never from anything the generated module asserts about itself.

Anti-delegation: a "decoder" that just calls `bz2.decompress` would round-trip
perfectly while having decoded nothing. So a generated decoder is FORBIDDEN from
importing the reference codec module at all (static guard below), and is run
with that module poisoned at runtime (see run_decoder_isolated). The generated
code must be an independent implementation.

Dependency-free (stdlib only) so it runs on Termux/Android, same as verify.py.
"""
from __future__ import annotations

import ast
import base64
import bz2
import gzip
import lzma
import zlib

# Reference codecs we can byte-validate on-device with zero install. The
# compress side uses FIXED, deterministic settings so M2 byte-exact re-encode
# is well defined. START HERE (stdlib); pip C-ext codecs can be added later.
#
# Each entry:
#   module   : the import name a generated decoder must NOT touch (anti-delegation)
#   compress : data -> bytes   (canonical, deterministic reference encoder)
#   decompress: bytes -> bytes (reference decoder; the M0 plaintext oracle)
#   tags     : structural hints recorded with results
REFERENCE = {
    "base64": {
        # Not compression — an encoding — but a perfectly byte-exact reference
        # codec and the simplest decoder to generate (RFC 4648).
        "module": "base64",
        "compress": base64.b64encode,
        "decompress": base64.b64decode,
        "tags": ["base64", "encoding", "rfc4648"],
    },
    "base32": {
        # RFC 4648 base32 — no built-in verifier, must be reimplemented (no
        # bytes-method shortcut), reliably generatable. Good autonomy proof.
        "module": "base64",
        "compress": base64.b32encode,
        "decompress": base64.b32decode,
        "tags": ["base32", "encoding", "rfc4648"],
    },
    "base16": {
        # RFC 4648 base16 (hex): each byte -> two uppercase hex chars. Trivial to
        # reimplement but NOT via bytes.fromhex/binascii (forbidden in the prompt),
        # so it stays a genuine reimplementation. Reliable autonomy proof.
        "module": "base64",
        "compress": base64.b16encode,
        "decompress": base64.b16decode,
        "tags": ["base16", "hex", "encoding", "rfc4648"],
    },
    "gzip": {
        "module": "gzip",
        "compress": lambda d: gzip.compress(d, compresslevel=9, mtime=0),
        "decompress": gzip.decompress,
        "tags": ["deflate", "huffman", "lz77", "static_model", "rfc1952"],
    },
    "zlib": {
        "module": "zlib",
        "compress": lambda d: zlib.compress(d, 9),
        "decompress": zlib.decompress,
        "tags": ["deflate", "huffman", "lz77", "static_model", "rfc1950"],
    },
    "bz2": {
        "module": "bz2",
        "compress": lambda d: bz2.compress(d, 9),
        "decompress": bz2.decompress,
        "tags": ["bwt", "mtf", "rle2", "huffman", "static_model"],
    },
    "lzma": {
        "module": "lzma",
        "compress": lambda d: lzma.compress(d, preset=6),
        "decompress": lzma.decompress,
        "tags": ["range_coder", "lz77", "adaptive"],
    },
}

# Codec-name aliases a job might use -> the REFERENCE key.
ALIASES = {
    "bzip2": "bz2",
    "deflate": "zlib",
    "xz": "lzma",
}

# A real M0 must clear at least this many corpus files byte-exact. Mirrors
# verify.M0_MIN_FILES — the gate enforces the same floor.
MIN_FILES = 8


class OracleError(Exception):
    """Raised when a codec has no installable reference oracle on this device."""


def resolve(codec: str) -> str:
    """Map a job's codec name to a REFERENCE key, or raise OracleError."""
    key = ALIASES.get(codec, codec)
    if key not in REFERENCE:
        raise OracleError(
            f"no reference oracle for codec {codec!r} "
            f"(have: {', '.join(sorted(REFERENCE))})"
        )
    return key


def has_oracle(codec: str) -> bool:
    try:
        resolve(codec)
        return True
    except OracleError:
        return False


def forbidden_module(codec: str) -> str:
    """The stdlib module a generated decoder for this codec must not import."""
    return REFERENCE[resolve(codec)]["module"]


def corpus() -> list[bytes]:
    """A varied corpus (>= MIN_FILES): empty, tiny, text, binary, repetitive,
    runs, all byte values, structured. Stresses every layer a decoder must
    reconstruct. Deterministic — no RNG — so results are reproducible."""
    return [
        b"",                                               # empty
        b"a",                                              # single byte
        b"hello world",
        b"the quick brown fox jumps over the lazy dog " * 50,   # redundancy -> LZ
        ("lorem ipsum dolor sit amet " * 40).encode(),
        bytes(range(256)),                                 # all byte values
        bytes((i * 73) % 256 for i in range(2000)),        # low-redundancy pseudo-random
        b"\x00" * 5000,                                     # long run -> RLE-like
        b"abcabcabcabc" * 200,                              # periodic
        b"\n".join(f"row {i},{i * i}".encode() for i in range(300)),  # structured
    ]


# ---------------------------------------------------------------------------
# Anti-delegation static guard.
# ---------------------------------------------------------------------------

def static_guard(source: str, codec: str) -> tuple[bool, str]:
    """Reject generated decoder source that delegates to the reference codec.

    A genuine decoder reimplements the format; it must not import the codec's
    own module (which would let it cheat via the real `decompress`). We parse
    the AST and reject any `import <mod>` / `from <mod> import ...` where <mod>
    is the forbidden module (or a submodule of it). Returns (ok, reason).
    """
    mod = forbidden_module(codec)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"generated decoder is not valid Python: {e}"

    def _root(name: str) -> str:
        return (name or "").split(".", 1)[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) == mod:
                    return False, f"forbidden import of reference codec {mod!r}"
        elif isinstance(node, ast.ImportFrom):
            # level>0 is a relative import; module may be None for `from . import x`
            if node.level == 0 and _root(node.module or "") == mod:
                return False, f"forbidden `from {mod} import ...`"
    return True, ""


# ---------------------------------------------------------------------------
# Isolated execution of a generated decoder.
# ---------------------------------------------------------------------------

def run_decoder_isolated(decode_fn, compressed: bytes, codec: str) -> bytes:
    """Run a generated decode() with the reference module POISONED, so even if
    the static guard were bypassed (e.g. a deferred/dynamic import), a call into
    the real `decompress` raises instead of silently delegating.

    Runs in-process but swaps `sys.modules[mod]` for a poison stub for the
    duration of the call, then restores it. This is belt-and-suspenders on top
    of static_guard; the guard is the primary defense.
    """
    import sys
    from types import ModuleType

    mod = forbidden_module(codec)
    real = sys.modules.get(mod)

    class _Poison(ModuleType):
        def __getattr__(self, name):
            raise RuntimeError(
                f"generated decoder tried to use reference codec {mod}.{name} "
                f"(delegation is forbidden)"
            )

    sys.modules[mod] = _Poison(mod)
    try:
        return decode_fn(compressed)
    finally:
        if real is not None:
            sys.modules[mod] = real
        else:
            sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# The trusted probes. These produce the MEASURED metrics the gate reads.
# A generated module never writes these numbers — we do, from real runs.
# ---------------------------------------------------------------------------

def validate_m0(codec: str, decode_fn) -> dict:
    """M0 oracle: does the generated decoder reproduce the real codec's input
    byte-for-byte across the corpus?

    For each corpus file x: c = reference.compress(x); assert decode(c) == x.
    Returns measured metrics + a sample of failures. byte_exact is True only if
    EVERY file matched (and the corpus met the floor).
    """
    key = resolve(codec)
    comp = REFERENCE[key]["compress"]

    files = corpus()
    n = len(files)
    ok = 0
    fails: list[str] = []
    for i, x in enumerate(files):
        try:
            c = comp(x)
            out = run_decoder_isolated(decode_fn, c, codec)
        except Exception as e:  # decoder raised / tried to delegate
            fails.append(f"case {i} ({len(x)}B): {type(e).__name__}: {e}")
            continue
        if out == x:
            ok += 1
        else:
            got = len(out) if isinstance(out, (bytes, bytearray)) else f"{type(out).__name__}"
            fails.append(f"case {i}: got {got}, want {len(x)}B")

    byte_exact = (ok == n) and (n >= MIN_FILES)
    return {
        "byte_exact": bool(byte_exact),
        "files_decoded": ok,
        "test_cases": n,
        "fails": fails[:5],
    }


def validate_m2(codec: str, encode_fn) -> dict:
    """M2 oracle: does the generated re-encoder reproduce the real codec's
    canonical output byte-for-byte?

    For each corpus file x: assert encode(x) == reference.compress(x). This is
    HARD — it requires matching the reference encoder's exact bitstream — and is
    the byte-exact oracle for Direction-1. Empty corpus members are skipped only
    if the reference itself can't round-trip them (it always can here).
    """
    key = resolve(codec)
    comp = REFERENCE[key]["compress"]

    files = corpus()
    n = len(files)
    ok = 0
    fails: list[str] = []
    for i, x in enumerate(files):
        try:
            want = comp(x)
            got = encode_fn(x)
        except Exception as e:
            fails.append(f"case {i}: {type(e).__name__}: {e}")
            continue
        if got == want:
            ok += 1
        else:
            glen = len(got) if isinstance(got, (bytes, bytearray)) else type(got).__name__
            fails.append(f"case {i}: re-encode {glen} != reference {len(want)}B")

    byte_exact = (ok == n) and (n >= 1)
    return {
        "byte_exact": bool(byte_exact),
        "reencode_samples": ok,
        "test_cases": n,
        "fails": fails[:5],
    }


if __name__ == "__main__":
    # Self-check: every reference oracle round-trips its own corpus, and the
    # static guard catches a delegating decoder. No generated code involved.
    print("oracle self-check")
    for name in sorted(REFERENCE):
        comp = REFERENCE[name]["compress"]
        deco = REFERENCE[name]["decompress"]
        good = all(deco(comp(x)) == x for x in corpus())
        print(f"  {name:6s} reference round-trip: {'ok' if good else 'FAIL'}  "
              f"(forbidden import: {REFERENCE[name]['module']})")

    cheat = "import bz2\ndef decode(data):\n    return bz2.decompress(data)\n"
    ok, reason = static_guard(cheat, "bz2")
    print(f"  static_guard rejects delegating bz2 decoder: {not ok}  ({reason})")

    honest = "def decode(data):\n    return b''  # stub\n"
    ok, _ = static_guard(honest, "bz2")
    print(f"  static_guard allows non-delegating source: {ok}")
