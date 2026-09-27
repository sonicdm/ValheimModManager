from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import dnfile


@dataclass
class PluginMetadata:
    guid: str | None
    name: str | None
    version: str | None


_META_CACHE: dict[str, tuple[int, int, PluginMetadata]] = {}


def read_bepinex_plugin_metadata(dll_path: str) -> PluginMetadata:
    """Extract BepInPlugin attribute from a .NET assembly without loading it."""
    path = Path(dll_path)
    try:
        stat = path.stat()
        key = str(path)
        cached = _META_CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
    except OSError:
        return PluginMetadata(None, None, None)

    guid = name = version = None
    try:
        pe = dnfile.dnPE(dll_path)
    except Exception:
        return PluginMetadata(None, None, None)

    try:
        if pe.net is None or pe.net.mdtables is None:
            meta = PluginMetadata(None, None, None)
            _META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, meta)
            return meta

        custom_attrs = getattr(pe.net.mdtables, "CustomAttribute", None)
        if custom_attrs is None:
            meta = PluginMetadata(None, None, None)
            _META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, meta)
            return meta

        for ca in custom_attrs:
            type_name = _attr_type_name(ca)
            if not type_name or "BepInPlugin" not in type_name:
                continue
            values = _decode_custom_attr_strings(ca)
            if len(values) >= 3:
                guid, name, version = values[0], values[1], values[2]
            elif len(values) == 2:
                guid, name = values[0], values[1]
            elif len(values) == 1:
                guid = values[0]
            break
    except Exception:
        return PluginMetadata(None, None, None)
    finally:
        try:
            pe.close()
        except Exception:
            pass

    meta = PluginMetadata(guid=guid, name=name, version=version)
    _META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, meta)
    return meta


def _as_str(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    if isinstance(value, str):
        return value
    for attr in ("value", "string", "data"):
        data = getattr(value, attr, None)
        if isinstance(data, str):
            return data
        if isinstance(data, (bytes, bytearray)):
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data.decode("latin-1", errors="replace")
    try:
        return str(value)
    except Exception:
        return None


def _attr_type_name(ca) -> str | None:  # noqa: ANN001
    try:
        ctor = ca.Type.row
        if ctor is None:
            return None
        klass = getattr(ctor, "Class", None)
        if klass is None:
            return _as_str(getattr(ctor, "Name", None))
        row = getattr(klass, "row", None)
        if row is None:
            return _as_str(klass)
        type_name = _as_str(getattr(row, "TypeName", None) or getattr(row, "Name", None))
        ns = _as_str(getattr(row, "TypeNamespace", None))
        if ns and type_name:
            return f"{ns}.{type_name}"
        return type_name
    except Exception:
        return None


def _blob_bytes(ca) -> bytes:
    blob = ca.Value
    if blob is None:
        return b""
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    for attr in ("value", "value_bytes", "raw_data"):
        data = getattr(blob, attr, None)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    try:
        return bytes(blob)
    except Exception:
        return b""


def _decode_custom_attr_strings(ca) -> list[str]:
    """Best-effort decode of .NET custom attribute blob string arguments."""
    data = _blob_bytes(ca)
    if len(data) < 4:
        return []

    offset = 0
    if data[0:2] == b"\x01\x00":
        offset = 2

    strings: list[str] = []
    while offset < len(data) and len(strings) < 8:
        length, size = _read_compressed_uint(data, offset)
        offset += size
        if length == 0xFF or length is None:
            strings.append("")
            continue
        if offset + length > len(data):
            break
        try:
            strings.append(data[offset : offset + length].decode("utf-8"))
        except UnicodeDecodeError:
            strings.append(data[offset : offset + length].decode("latin-1", errors="replace"))
        offset += length
    return strings


def _read_compressed_uint(data: bytes, offset: int) -> tuple[int | None, int]:
    if offset >= len(data):
        return None, 0
    b0 = data[offset]
    if b0 == 0xFF:
        return 0xFF, 1
    if (b0 & 0x80) == 0:
        return b0, 1
    if (b0 & 0xC0) == 0x80:
        if offset + 1 >= len(data):
            return None, 0
        return ((b0 & 0x3F) << 8) | data[offset + 1], 2
    if offset + 3 >= len(data):
        return None, 0
    return (
        ((b0 & 0x1F) << 24)
        | (data[offset + 1] << 16)
        | (data[offset + 2] << 8)
        | data[offset + 3],
        4,
    )
