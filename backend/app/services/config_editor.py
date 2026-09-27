from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings
from .paths import PathEscapeError, ensure_within, safe_join


@dataclass
class ConfigEntry:
    key: str
    value: str
    value_type: str  # bool|int|float|string|enum|unknown
    comment: str | None = None
    enum_values: list[str] = field(default_factory=list)


@dataclass
class ConfigSection:
    name: str
    comment: str | None = None
    entries: list[ConfigEntry] = field(default_factory=list)


_BOOL = re.compile(r"^(true|false)$", re.I)
_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+\.\d+$")
_ENUM_HINT = re.compile(r"#\s*Acceptable values:\s*(.+)$", re.I)
_TYPE_HINT = re.compile(r"#\s*Setting type:\s*(\w+)", re.I)


def list_config_files() -> list[dict[str, Any]]:
    settings = get_settings()
    root = settings.config_dir
    results: list[dict[str, Any]] = []
    if not root.is_dir():
        return results
    for path in sorted(root.glob("*.cfg")):
        if not path.is_file():
            continue
        try:
            ensure_within(root, path)
        except PathEscapeError:
            continue
        stat = path.stat()
        results.append(
            {
                "path": path.name,
                "name": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            }
        )
    return results


def resolve_config_path(relative: str) -> Path:
    settings = get_settings()
    # Only allow bare filenames under config dir (no subdirs for safety in v1)
    name = Path(relative).name
    if name != relative.replace("\\", "/").split("/")[-1] or "/" in relative or "\\" in relative:
        # allow only filename
        if Path(relative).name != relative and not relative.startswith("./"):
            raise PathEscapeError("Config path must be a filename in the BepInEx config directory")
    path = safe_join(settings.config_dir, name)
    if path.suffix.lower() != ".cfg":
        raise PathEscapeError("Only .cfg files are accessible")
    return path


def _infer_type(value: str, comments: list[str]) -> tuple[str, list[str]]:
    enum_values: list[str] = []
    declared = None
    for c in comments:
        m = _TYPE_HINT.search(c)
        if m:
            declared = m.group(1).lower()
        m = _ENUM_HINT.search(c)
        if m:
            enum_values = [v.strip() for v in m.group(1).split(",") if v.strip()]
    if enum_values:
        return "enum", enum_values
    if declared in {"boolean", "bool"}:
        return "bool", []
    if declared in {"int32", "int64", "int", "uint32"}:
        return "int", []
    if declared in {"single", "double", "float", "decimal"}:
        return "float", []
    if _BOOL.match(value):
        return "bool", []
    if _INT.match(value):
        return "int", []
    if _FLOAT.match(value):
        return "float", []
    return "string", []


def parse_bepinex_cfg(text: str) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_comments: list[str] = []
    section_comment: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            pending_comments.append(line)
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            pending_comments.append(line)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if current is not None:
                sections.append(current)
            name = stripped[1:-1]
            comment = "\n".join(pending_comments).strip() or None
            current = {"name": name, "comment": comment, "entries": []}
            pending_comments = []
            section_comment = []
            continue
        if "=" in stripped and current is not None:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            vtype, enums = _infer_type(value, pending_comments)
            current["entries"].append(
                {
                    "key": key,
                    "value": value,
                    "value_type": vtype,
                    "comment": "\n".join(pending_comments).strip() or None,
                    "enum_values": enums,
                }
            )
            pending_comments = []
            continue
        pending_comments.append(line)

    if current is not None:
        sections.append(current)

    return {"sections": sections}


def structured_to_raw(original: str, structured: dict[str, Any]) -> str:
    """Update values in original text from structured data, preserving comments/order where possible."""
    value_map: dict[tuple[str, str], str] = {}
    for section in structured.get("sections") or []:
        sname = section.get("name") or ""
        for entry in section.get("entries") or []:
            value_map[(sname, entry["key"])] = str(entry["value"])

    lines = original.splitlines()
    out: list[str] = []
    current_section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            out.append(line)
            continue
        if "=" in stripped and not stripped.startswith("#") and not stripped.startswith(";"):
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if (current_section, key) in value_map:
                # Preserve left side (key + spacing) and normalize a single space after '='
                left = line.split("=", 1)[0]
                out.append(f"{left}= {value_map[(current_section, key)]}")
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if original.endswith("\n") else "")


def read_config(relative: str) -> dict[str, Any]:
    path = resolve_config_path(relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    raw = path.read_text(encoding="utf-8-sig")
    try:
        structured = parse_bepinex_cfg(raw)
        parse_ok = True
    except Exception:
        structured = None
        parse_ok = False
    return {
        "path": path.name,
        "raw": raw,
        "structured": structured,
        "parse_ok": parse_ok,
        "restart_required": True,
    }


def save_config(relative: str, *, raw: str | None = None, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    path = resolve_config_path(relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    original = path.read_text(encoding="utf-8-sig")

    if structured is not None:
        content = structured_to_raw(original, structured)
    elif raw is not None:
        content = raw
    else:
        raise ValueError("Either raw or structured content is required")

    # Backup
    backup_dir = settings.backups_dir / "configs"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")

    path.write_text(content, encoding="utf-8")
    return read_config(relative)
