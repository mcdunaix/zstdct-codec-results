"""A byte-exact, independent Base32 decoder (RFC 4648 §6).

Decodes standard base32 (uppercase A-Z then digits 2-7, '='-padded) to the
original bytes. Pure Python standard library -- no ``base64`` dependency.

Public API: :func:`decode` (``bytes -> bytes``), byte-exact inverse of
``base64.b32encode``.
"""

from __future__ import annotations

# RFC 4648 §6 alphabet
_ALPH = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

# Reverse lookup: character -> 5-bit value (-1 = invalid)
_REVERSE: list[int] = [-1] * 256
for _i, _ch in enumerate(_ALPH):
    _REVERSE[_ch] = _i
    # Also accept lowercase (ASCII bit 5 set)
    _REVERSE[_ch | 0x20] = _i


def decode(data: bytes) -> bytes:
    """Decode a complete base32 stream to the original uncompressed bytes.

    Accepts canonical uppercase A-Z/2-7 with ``=`` padding. Accepts
    lowercase input as well (case-insensitive). Byte-exact inverse of
    ``base64.b32encode``::

        >>> decode(b'======') == b''
        True
        >>> decode(base64.b32encode(b'hello'))  # independent impl
        b'hello'

    Raises :exc:`ValueError` on invalid characters or malformed padding.
    """
    if not data:
        return b""

    # --- strip padding -------------------------------------------------------
    pad = 0
    while pad < 6 and data[-(pad + 1)] == 61:  # ord('=') = 61
        pad += 1
    if pad:
        data = data[:-pad]

    # --- decode 5-bit groups into bytes -------------------------------------
    out = bytearray()
    buf = 0
    bits = 0

    for byte in data:
        val = _REVERSE[byte]
        if val < 0:
            raise ValueError(f"invalid base32 character: {byte:#04x}")
        buf = (buf << 5) | val
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((buf >> bits) & 0xFF)

    # Leftover bits should be zero (the encoder zero-pads, and padding was
    # removed above).  If there are leftover non-zero bits, it's malformed.
    if bits and (buf & ((1 << bits) - 1)):
        raise ValueError("non-zero padding bits in base32 input")

    return bytes(out)
