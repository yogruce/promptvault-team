"""Low-level, dependency-free PNG text chunk reader/writer.

Reads ``tEXt``, ``zTXt`` and ``iTXt`` chunks (where generators such as
Automatic1111, ComfyUI, NovelAI, InvokeAI, Fooocus ... store their data)
without decoding pixel data, so scanning large folders is fast.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}
MAX_TEXT_CHUNK = 64 * 1024 * 1024  # safety limit for a single text chunk


class PNGError(ValueError):
    """Raised when a file is not a valid PNG."""


@dataclass
class PNGInfo:
    width: int = 0
    height: int = 0
    text: dict[str, str] = field(default_factory=dict)


def _decode_text(data: bytes) -> str:
    # The spec says tEXt is latin-1, but many tools write UTF-8 there.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _parse_text_chunk(ctype: bytes, data: bytes) -> tuple[str, str] | None:
    try:
        if ctype == b"tEXt":
            key, _, value = data.partition(b"\x00")
            return key.decode("latin-1"), _decode_text(value)
        if ctype == b"zTXt":
            key, _, rest = data.partition(b"\x00")
            # rest[0] is compression method (always 0 = zlib)
            return key.decode("latin-1"), _decode_text(zlib.decompress(rest[1:]))
        if ctype == b"iTXt":
            key, _, rest = data.partition(b"\x00")
            comp_flag, _method = rest[0], rest[1]
            rest = rest[2:]
            _lang, _, rest = rest.partition(b"\x00")
            _tkey, _, text = rest.partition(b"\x00")
            if comp_flag:
                text = zlib.decompress(text)
            return key.decode("latin-1"), text.decode("utf-8", errors="replace")
    except (IndexError, zlib.error):
        return None
    return None


def iter_chunks(f: BinaryIO, wanted: set[bytes] | None = None) -> Iterator[tuple[bytes, bytes]]:
    """Yield ``(chunk_type, data)``; chunks not in *wanted* are skipped with seek."""
    if f.read(8) != PNG_SIGNATURE:
        raise PNGError("PNG imzası bulunamadı")
    while True:
        head = f.read(8)
        if len(head) < 8:
            return
        length, ctype = struct.unpack(">I4s", head)
        if wanted is not None and ctype not in wanted and ctype != b"IEND":
            f.seek(length + 4, 1)
            continue
        if length > MAX_TEXT_CHUNK:
            f.seek(length + 4, 1)
            continue
        data = f.read(length)
        f.read(4)  # CRC
        if len(data) < length:
            return
        yield ctype, data
        if ctype == b"IEND":
            return


def read_png_info(source: str | Path | BinaryIO) -> PNGInfo:
    """Return image size and all text metadata of a PNG file."""
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return read_png_info(f)
    info = PNGInfo()
    for ctype, data in iter_chunks(source, wanted=TEXT_CHUNKS | {b"IHDR"}):
        if ctype == b"IHDR" and len(data) >= 8:
            info.width, info.height = struct.unpack(">II", data[:8])
        elif ctype in TEXT_CHUNKS:
            parsed = _parse_text_chunk(ctype, data)
            if parsed:
                key, value = parsed
                # keep the first occurrence, append duplicates with a suffix
                if key in info.text:
                    n = 2
                    while f"{key}#{n}" in info.text:
                        n += 1
                    key = f"{key}#{n}"
                info.text[key] = value
    return info


def read_png_text(source: str | Path | BinaryIO) -> dict[str, str]:
    return read_png_info(source).text


# ---------------------------------------------------------------- writing

def _chunk(ctype: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)


def make_text_chunk(key: str, value: str, compress: bool = False) -> bytes:
    """Build a tEXt chunk when possible, otherwise an iTXt (UTF-8) chunk."""
    k = key.encode("latin-1")[:79]
    try:
        raw = value.encode("latin-1")
        if compress:
            return _chunk(b"zTXt", k + b"\x00\x00" + zlib.compress(raw))
        return _chunk(b"tEXt", k + b"\x00" + raw)
    except UnicodeEncodeError:
        payload = value.encode("utf-8")
        flag = b"\x00"
        if compress:
            payload, flag = zlib.compress(payload), b"\x01"
        return _chunk(b"iTXt", k + b"\x00" + flag + b"\x00" + b"\x00" + b"\x00" + payload)


def write_png_text(png: bytes, items: Mapping[str, str], replace: bool = True,
                   compress: bool = False) -> bytes:
    """Return a copy of *png* bytes with the given text chunks inserted after IHDR."""
    if not png.startswith(PNG_SIGNATURE):
        raise PNGError("PNG imzası bulunamadı")
    out = [PNG_SIGNATURE]
    pos = 8
    inserted = False
    while pos < len(png):
        length, ctype = struct.unpack(">I4s", png[pos:pos + 8])
        end = pos + 12 + length
        chunk = png[pos:end]
        drop = False
        if replace and ctype in TEXT_CHUNKS:
            parsed = _parse_text_chunk(ctype, png[pos + 8:pos + 8 + length])
            drop = bool(parsed and parsed[0] in items)
        if not drop:
            out.append(chunk)
        if ctype == b"IHDR" and not inserted:
            out.extend(make_text_chunk(k, v, compress) for k, v in items.items())
            inserted = True
        pos = end
    return b"".join(out)


def make_blank_png(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (40, 40, 60)) -> bytes:
    """Create a tiny solid-colour RGB PNG (used by tests and demos)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
