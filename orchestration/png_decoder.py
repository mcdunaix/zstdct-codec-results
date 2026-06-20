#!/usr/bin/env python3
"""
PNG decoder — from scratch (except zlib for DEFLATE).

Parses PNG bitstream, reverses filtering, returns raw pixel data.
No PIL, no external deps beyond stdlib.

Spec: ISO/IEC 15948:2004 (PNG Specification)
"""
from __future__ import annotations

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Filter types
FILTER_NONE = 0
FILTER_SUB = 1
FILTER_UP = 2
FILTER_AVERAGE = 3
FILTER_PAETH = 4

# Color types
COLOR_GRAYSCALE = 0
COLOR_RGB = 2
COLOR_INDEXED = 3
COLOR_GRAYSCALE_ALPHA = 4
COLOR_RGBA = 6


class PNGFatal(Exception):
    """Fatal parse error."""


def _bits_per_pixel(color_type: int, bit_depth: int) -> int:
    """Return bits per pixel for a given color type and bit depth."""
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if samples is None:
        raise PNGFatal(f"unknown color type: {color_type}")
    return samples * bit_depth


def _bytes_per_row(width: int, bpp: int) -> int:
    """Return bytes per scanline (filter byte NOT included)."""
    # Each scanline starts with a filter type byte
    raw = (width * bpp + 7) // 8  # ceil for <8 bit depths
    return raw


def _paeth(a: int, b: int, c: int) -> int:
    """Paeth predictor filter."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _reconstruct_row(filter_type: int, raw: bytearray, prev: bytearray | None, bpp: int):
    """Reverse the filter on one scanline (in-place on `raw`)."""
    if filter_type == FILTER_NONE:
        return
    if filter_type == FILTER_SUB:
        for i in range(bpp, len(raw)):
            raw[i] = (raw[i] + raw[i - bpp]) & 0xFF
    elif filter_type == FILTER_UP:
        if prev is None:
            return  # Up-filter on first row is a no-op
        for i in range(len(raw)):
            raw[i] = (raw[i] + prev[i]) & 0xFF
    elif filter_type == FILTER_AVERAGE:
        for i in range(len(raw)):
            left = raw[i - bpp] if i >= bpp else 0
            up = prev[i] if prev is not None else 0
            raw[i] = (raw[i] + (left + up) // 2) & 0xFF
    elif filter_type == FILTER_PAETH:
        for i in range(len(raw)):
            left = raw[i - bpp] if i >= bpp else 0
            up = prev[i] if prev is not None else 0
            up_left = prev[i - bpp] if prev is not None and i >= bpp else 0
            raw[i] = (raw[i] + _paeth(left, up, up_left)) & 0xFF
    else:
        raise PNGFatal(f"unknown filter type: {filter_type}")


def decode(data: bytes) -> tuple[int, int, int, int, bytes]:
    """
    Decode a PNG from raw bytes.

    Returns: (width, height, color_type, bit_depth, raw_pixels)
    - raw_pixels: bytearray of RGB(A) or grayscale pixel data,
      scanline-major (no filter byte), padded to byte boundaries.
    """
    if data[:8] != PNG_SIG:
        raise PNGFatal("not a valid PNG (bad signature)")

    offset = 8
    width = height = bit_depth = color_type = 0
    compressed_chunks: list[bytes] = []
    palette: list[tuple[int, int, int]] | None = None

    while offset < len(data):
        if offset + 8 > len(data):
            raise PNGFatal("truncated chunk header")
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        # Skip CRC validation (would need the type + data; CRC32 verified
        # implicitly by zlib if the data is compressed, but we trust the
        # stream structure for this M0)
        offset += 12 + length

        if chunk_type == b"IHDR":
            if len(chunk_data) < 13:
                raise PNGFatal("truncated IHDR")
            width = struct.unpack_from(">I", chunk_data, 0)[0]
            height = struct.unpack_from(">I", chunk_data, 4)[0]
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            # compression_method (10), filter_method (11), interlace (12)
            if chunk_data[12] != 0:
                raise PNGFatal("Adam7 interlaced PNG not supported (yet)")

        elif chunk_type == b"PLTE" and palette is None:
            if length % 3 != 0:
                raise PNGFatal("invalid PLTE length")
            palette = []
            for i in range(0, length, 3):
                palette.append((chunk_data[i], chunk_data[i + 1], chunk_data[i + 2]))

        elif chunk_type == b"IDAT":
            compressed_chunks.append(chunk_data)

        elif chunk_type == b"IEND":
            break

    if width == 0 or height == 0:
        raise PNGFatal("missing or empty IHDR")

    # Decompress IDAT data
    if not compressed_chunks:
        raise PNGFatal("no IDAT chunks")
    try:
        decompressed = zlib.decompress(b"".join(compressed_chunks))
    except zlib.error as e:
        raise PNGFatal(f"zlib decompression error: {e}") from e

    # Reconstruct scanlines
    bpp = _bits_per_pixel(color_type, bit_depth)
    row_bytes = _bytes_per_row(width, bpp)
    expected_per_row = 1 + row_bytes  # filter byte + pixel data
    expected_total = expected_per_row * height

    if len(decompressed) < expected_total:
        raise PNGFatal(
            f"decompressed data too short: {len(decompressed)} < {expected_total}"
        )

    raw_rows = bytearray()
    prev_row: bytearray | None = None

    for y in range(height):
        start = y * expected_per_row
        filter_type = decompressed[start]
        row_data = bytearray(decompressed[start + 1 : start + expected_per_row])
        _reconstruct_row(filter_type, row_data, prev_row, bpp // 8 if bpp > 8 else 1)
        raw_rows.extend(row_data)
        prev_row = row_data

    # Handle indexed color: expand palette
    if color_type == COLOR_INDEXED:
        if palette is None:
            raise PNGFatal("indexed PNG without PLTE")
        expanded = bytearray()
        for pixel in raw_rows:
            r, g, b = palette[pixel]
            expanded.extend((r, g, b))
        raw_rows = expanded

    return width, height, color_type, bit_depth, bytes(raw_rows)


# --- Reference encoder (for test corpus generation) ---
def _make_raw_row(width: int, bpp: int, pixels: bytes, y: int) -> tuple[int, bytearray]:
    """Create a scanline from pixel data with appropriate filter applied."""
    row_bytes = _bytes_per_row(width, bpp)
    start = y * row_bytes
    raw = bytearray(pixels[start : start + row_bytes])
    # For test corpus, use FILTER_NONE for simplicity
    return (FILTER_NONE, raw)


def encode(width: int, height: int, color_type: int, bit_depth: int,
           pixels: bytes) -> bytes:
    """
    Encode raw pixel data as a minimal PNG.

    Used as the reference encoder for testing the decoder.
    Uses FILTER_NONE and minimal chunk structure.
    """
    bpp = _bits_per_pixel(color_type, bit_depth)
    row_bytes = _bytes_per_row(width, bpp)

    # Build filtered scanlines
    raw_data = bytearray()
    for y in range(height):
        ftype, row = _make_raw_row(width, bpp, pixels, y)
        raw_data.append(ftype)
        raw_data.extend(row)

    compressed = zlib.compress(bytes(raw_data))

    out = bytearray(PNG_SIG)

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                            0, 0, 0)  # deflate + no filter + no interlace
    crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    out += struct.pack(">I", 13)
    out += b"IHDR"
    out += ihdr_data
    out += struct.pack(">I", crc)

    # IDAT
    idat_data = compressed
    crc = zlib.crc32(b"IDAT" + idat_data) & 0xFFFFFFFF
    out += struct.pack(">I", len(idat_data))
    out += b"IDAT"
    out += idat_data
    out += struct.pack(">I", crc)

    # IEND
    crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    out += struct.pack(">I", 0)
    out += b"IEND"
    out += struct.pack(">I", crc)

    return bytes(out)
