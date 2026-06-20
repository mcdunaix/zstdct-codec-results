#!/usr/bin/env python3
"""
From-scratch RFC 4648 base64 decoder (standard alphabet).

The simplest real codec: a complete, byte-exact, FULL-scope decoder used to
exercise the legitimacy pipeline end to end (M0 -> a genuine pass).

Deliberately does NOT call the stdlib base64 module. The verifier decodes with
THIS code and checks it against stdlib's *encoder* as the reference, so a pass
means our decoder is correct — not that stdlib agrees with itself.
"""
from __future__ import annotations

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_REV = {c: i for i, c in enumerate(_ALPHABET)}


def b64_decode(data: bytes) -> bytes:
    """Decode standard base64 (RFC 4648) bytes -> original bytes."""
    out = bytearray()
    acc = 0
    nbits = 0
    seen_pad = False
    for byte in data:
        ch = chr(byte)
        if ch == "=":
            seen_pad = True
            continue
        if ch in ("\n", "\r"):
            continue
        if seen_pad:
            raise ValueError("base64 data after padding")
        val = _REV.get(ch)
        if val is None:
            raise ValueError(f"invalid base64 character: {ch!r}")
        acc = (acc << 6) | val
        nbits += 6
        if nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    return bytes(out)
