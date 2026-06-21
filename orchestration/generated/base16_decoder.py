"""A byte-exact base16 decoder — ground truth for the hex-layer substrate.

Base16 (RFC 4648 §8) maps each input byte to two uppercase hex digits, high
nibble first, using the alphabet ``0123456789ABCDEF``. The encoded stream has
even length; the decoder maps each pair back to one byte.

This implementation is **independent**: it does not import ``base64`` (or any
submodule of it). The nibble math is done by hand: two static lookup tables
for the low and high nibble values of each ASCII hex digit, covering both
``A-F`` and ``a-f`` for lenient input.

Public API: :func:`decode(data: bytes) -> bytes`

Deterministic; no third-party dependency.
"""

from __future__ import annotations

# --- nibble decode tables ---------------------------------------------------
# For each ASCII code, high_nibble[code] gives the 4-bit value of the first
# nibble of a two-hex-digit pair (shifted left by 4), and low_nibble[code]
# gives the second nibble (low 4 bits). Invalid characters decode to 0;
# the caller is expected to supply well-formed input.
#
# The table approach is faster than a conditional chain and makes the inner
# loop branch-predictor friendly.

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


# --- public API -------------------------------------------------------------

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
