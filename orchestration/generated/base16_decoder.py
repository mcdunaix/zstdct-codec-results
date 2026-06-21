"""A byte-exact base16 codec -- ground truth for the hex-layer substrate.

Base16 (RFC 4648 §8) maps each input byte to two uppercase hex digits, high
nibble first, using the alphabet ``0123456789ABCDEF``. The encoded stream has
even length; the decoder maps each pair back to one byte.

The encoder is **independent**: it does not import the standard library module
for this codec (or any submodule of it). The nibble math is done by hand:
the encoder uses a static lookup
string, the decoder uses two precomputed ``_HIGH`` / ``_LOW`` tables covering
both ``A-F`` and ``a-f`` for lenient input.

Public API:

- :func:`encode(data: bytes) -> bytes`  -- encode to base16 (uppercase hex)
- :func:`decode(data: bytes) -> bytes`  -- decode base16 to original bytes

Deterministic; no third-party dependency.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

# Static lookup: each index in [0..15] maps to its uppercase hex ASCII byte.
# This avoids any conditional branching in the hot loop.
_HEX_DIGITS: bytes = b"0123456789ABCDEF"


def encode(data: bytes) -> bytes:
    """Encode ``data`` to a base16 (uppercase hex) ``bytes`` object.

    Each input byte becomes two hex digits, high nibble first.
    Byte-exact with the standard Base16 (RFC 4648 §8) encoding.

    Parameters
    ----------
    data : bytes
        The raw bytes to encode.

    Returns
    -------
    bytes
        The base16-encoded stream.
    """
    if not data:
        return b""
    n = len(data)
    out = bytearray(n * 2)
    for i, b in enumerate(data):
        out[i * 2] = _HEX_DIGITS[b >> 4]
        out[i * 2 + 1] = _HEX_DIGITS[b & 0x0F]
    return bytes(out)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

# Precomputed decode tables: one read per hex digit, no conditional in the
# inner loop.  _HIGH[ascii_code] gives the high-nibble value (shifted left
# by 4); _LOW[ascii_code] gives the low nibble value (raw 0..15).  Invalid
# characters decode to 0; the caller is expected to supply well-formed input.

_HIGH: list[int] = [0] * 256
_LOW: list[int] = [0] * 256

# Seed digits '0'-'9'
for _i in range(10):
    _code = ord("0") + _i
    _HIGH[_code] = _i << 4
    _LOW[_code] = _i

# Seed uppercase 'A'-'F'
for _i in range(6):
    _code = ord("A") + _i
    _HIGH[_code] = (_i + 10) << 4
    _LOW[_code] = _i + 10

# Seed lowercase 'a'-'f'
for _i in range(6):
    _code = ord("a") + _i
    _HIGH[_code] = (_i + 10) << 4
    _LOW[_code] = _i + 10


def decode(data: bytes) -> bytes:
    """Decode a base16 (hex) encoded ``bytes`` object to the original bytes.

    The input must have even length; each pair of ASCII hex digits is decoded
    to one byte. Accepts both uppercase and lowercase hex digits.

    Parameters
    ----------
    data : bytes
        The base16-encoded stream (ASCII hex digits).

    Returns
    -------
    bytes
        The original uncompressed bytes.
    """
    if not data:
        return b""
    n = len(data)
    out = bytearray(n // 2)
    for i in range(0, n, 2):
        out[i >> 1] = _HIGH[data[i]] | _LOW[data[i + 1]]
    return bytes(out)
