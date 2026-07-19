from __future__ import annotations

import struct
import threading
import zlib
from pathlib import Path


_APP_ICON_CACHE: dict[tuple[int, bool], bytes] = {}
_APP_ICON_LOCK = threading.Lock()


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _encode_png_rgba(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        start = y * width
        for r, g, b, a in pixels[start : start + width]:
            rows.extend((r, g, b, a))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + _png_chunk(b"IEND", b"")
    )


def _decode_png_rgba(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed.extend(body)
        elif kind == b"IEND":
            break
    if bit_depth != 8 or interlace != 0 or width is None or height is None or color_type not in {2, 6}:
        return None
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    rows: list[bytearray] = []
    offset = 0

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        prev = rows[y - 1] if y else bytearray(stride)
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            up = prev[x]
            up_left = prev[x - channels] if x >= channels else 0
            if filter_type == 1:
                row[x] = (row[x] + left) & 0xFF
            elif filter_type == 2:
                row[x] = (row[x] + up) & 0xFF
            elif filter_type == 3:
                row[x] = (row[x] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                row[x] = (row[x] + paeth(left, up, up_left)) & 0xFF
        rows.append(row)

    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for x in range(0, stride, channels):
            if channels == 4:
                pixels.append((row[x], row[x + 1], row[x + 2], row[x + 3]))
            else:
                pixels.append((row[x], row[x + 1], row[x + 2], 255))
    return width, height, pixels


def _resize_rgba(
    src_w: int,
    src_h: int,
    src: list[tuple[int, int, int, int]],
    size: int,
) -> list[tuple[int, int, int, int]]:
    if src_w == size and src_h == size:
        return src
    out: list[tuple[int, int, int, int]] = []
    for y in range(size):
        sy = min(src_h - 1, (y * src_h) // size)
        for x in range(size):
            sx = min(src_w - 1, (x * src_w) // size)
            out.append(src[sy * src_w + sx])
    return out


def _whiten_near_white(pixels: list[tuple[int, int, int, int]], threshold: int = 248) -> list[tuple[int, int, int, int]]:
    """Force near-white pixels to pure #fff so splash tiles don't show gray fringes."""
    out: list[tuple[int, int, int, int]] = []
    for r, g, b, a in pixels:
        if r >= threshold and g >= threshold and b >= threshold:
            out.append((255, 255, 255, 255))
        else:
            out.append((r, g, b, a))
    return out


def _knockout_white_to_transparent(
    pixels: list[tuple[int, int, int, int]],
    threshold: int = 248,
) -> list[tuple[int, int, int, int]]:
    """Make white background transparent so splash shows only the gray logo on white bg."""
    out: list[tuple[int, int, int, int]] = []
    for r, g, b, a in pixels:
        if r >= threshold and g >= threshold and b >= threshold:
            out.append((255, 255, 255, 0))
        else:
            # Soften residual light fringe against transparent bg
            luma = (r + g + b) / 3
            if luma > 200:
                alpha = max(0, min(255, int(255 * (1 - (luma - 200) / 55))))
                out.append((r, g, b, alpha))
            else:
                out.append((r, g, b, a))
    return out


def app_icon_png(logo_path: Path, fallback_logo_path: Path, size: int = 512, *, solid: bool = False) -> bytes:
    """Build a PWA icon from the configured logo path."""
    target = 512 if size >= 512 else 192
    cache_key = (target, solid)
    with _APP_ICON_LOCK:
        cached = _APP_ICON_CACHE.get(cache_key)
        if cached is not None:
            return cached
        source_bytes: bytes | None = None
        if logo_path.exists():
            source_bytes = logo_path.read_bytes()
        elif fallback_logo_path.exists():
            source_bytes = fallback_logo_path.read_bytes()
        if source_bytes is None:
            if solid:
                payload = _encode_png_rgba(target, target, [(255, 255, 255, 255)] * (target * target))
            else:
                payload = _encode_png_rgba(target, target, [(255, 255, 255, 0)] * (target * target))
            _APP_ICON_CACHE[cache_key] = payload
            return payload
        decoded = _decode_png_rgba(source_bytes)
        if decoded is None:
            payload = source_bytes
            _APP_ICON_CACHE[cache_key] = payload
            return payload
        src_w, src_h, src = decoded
        pixels = _resize_rgba(src_w, src_h, src, target)
        if solid:
            pixels = _whiten_near_white(pixels)
        else:
            pixels = _knockout_white_to_transparent(pixels)
        payload = _encode_png_rgba(target, target, pixels)
        _APP_ICON_CACHE[cache_key] = payload
        return payload
