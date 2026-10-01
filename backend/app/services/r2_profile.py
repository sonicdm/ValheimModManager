"""Gale / r2modman profile zip (.r2z) and share-code helpers."""

from __future__ import annotations

import base64
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Literal

import httpx
import yaml

logger = logging.getLogger(__name__)

PROFILE_DATA_PREFIX = "#r2modman\n"
USER_AGENT = "ValheimModManager/0.3.0 (compatible; Gale/r2modman profile interop)"

THUNDERSTORE_GET = "https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"
THUNDERSTORE_CREATE = "https://thunderstore.io/api/experimental/legacyprofile/create/"
HEXIUM_GET = "https://hexium.gg/api/experimental/legacyprofile/get/{code}/"
HEXIUM_CREATE = "https://hexium.gg/api/experimental/legacyprofile/create/"

ExportMode = Literal["full", "thunderstore"]

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass
class ProfileModSpec:
    full_name: str
    version: str
    enabled: bool = True
    source: str = "thunderstore"  # thunderstore|hexium


@dataclass
class ParsedProfile:
    name: str
    mods: list[ProfileModSpec]
    community: str | None = None
    style: str = "gale"  # gale|r2modman (heuristic)
    config_files: dict[str, bytes] = field(default_factory=dict)  # relative under bepinex root


def version_parts(version: str | None) -> tuple[int, int, int]:
    if not version:
        return 0, 0, 0
    nums = re.findall(r"\d+", version)
    major = int(nums[0]) if len(nums) > 0 else 0
    minor = int(nums[1]) if len(nums) > 1 else 0
    patch = int(nums[2]) if len(nums) > 2 else 0
    return major, minor, patch


def version_string(parts: dict[str, Any] | str | None) -> str:
    if parts is None:
        return "0.0.0"
    if isinstance(parts, str):
        return parts
    return f"{int(parts.get('major', 0))}.{int(parts.get('minor', 0))}.{int(parts.get('patch', 0))}"


def normalize_source(raw: Any) -> str:
    if raw is None:
        return "thunderstore"
    s = str(raw).strip().lower()
    if s in ("hexium",):
        return "hexium"
    return "thunderstore"


def source_label(source: str) -> str:
    return "Hexium" if source == "hexium" else "Thunderstore"


def normalize_config_member(path: str) -> str | None:
    """Map zip member → path relative to bepinex_root, or None if ignored."""
    p = path.replace("\\", "/").lstrip("/")
    if not p or p.endswith("/"):
        return None
    if p in ("export.r2x", "changelog.txt", "doorstop_config.ini"):
        return None
    if p.startswith("BepInEx/cache/") or p.startswith("cache/"):
        return None
    if "/plugins/" in f"/{p}" or p.startswith("BepInEx/plugins/") or p.startswith("plugins/"):
        return None
    if p.startswith("BepInEx/"):
        p = p[len("BepInEx/") :]
    # Accept config/** (and other non-plugin paths under BepInEx)
    if p.startswith("config/") or p.endswith(".cfg") or p.endswith(".yaml") or p.endswith(".yml"):
        return p
    if p.startswith("config"):
        return p
    return None


def parse_export_r2x(text: str) -> tuple[str, list[ProfileModSpec], str | None, str]:
    data = yaml.safe_load(text) or {}
    name = str(data.get("profileName") or data.get("name") or "Imported")
    community = data.get("community")
    community_s = str(community) if community else None
    mods_raw = data.get("mods") or []
    has_source = False
    mods: list[ProfileModSpec] = []
    for item in mods_raw:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("name") or "").strip()
        if not full_name:
            continue
        if "source" in item and item.get("source") is not None:
            has_source = True
        mods.append(
            ProfileModSpec(
                full_name=full_name,
                version=version_string(item.get("version") or item.get("versionNumber")),
                enabled=bool(item.get("enabled", True)),
                source=normalize_source(item.get("source")),
            )
        )
    style = "gale" if has_source else "r2modman"
    return name, mods, community_s, style


def parse_r2z_bytes(data: bytes) -> ParsedProfile:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if "export.r2x" not in names:
            raise ValueError("Not a Gale/r2modman profile zip (missing export.r2x)")
        text = zf.read("export.r2x").decode("utf-8")
        name, mods, community, style = parse_export_r2x(text)
        configs: dict[str, bytes] = {}
        for member in names:
            rel = normalize_config_member(member)
            if rel is None:
                continue
            try:
                configs[rel] = zf.read(member)
            except Exception:
                continue
        if any(n.startswith("config/") and not n.startswith("BepInEx/") for n in names):
            style = "r2modman"
        elif any(n.startswith("BepInEx/config/") for n in names):
            style = "gale"
        return ParsedProfile(name=name, mods=mods, community=community, style=style, config_files=configs)


def decode_share_blob(text: str) -> bytes:
    raw = text.strip()
    if raw.startswith(PROFILE_DATA_PREFIX):
        raw = raw[len(PROFILE_DATA_PREFIX) :]
    elif raw.startswith("#r2modman"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    return base64.b64decode(raw)


async def fetch_share_code(code: str) -> tuple[bytes, str]:
    """Return (zip_bytes, backend_host). Tries Thunderstore then Hexium."""
    code = code.strip()
    if not UUID_RE.match(code):
        raise ValueError("Share code must be a UUID")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
        for backend, url in (
            ("thunderstore", THUNDERSTORE_GET.format(code=code)),
            ("hexium", HEXIUM_GET.format(code=code)),
        ):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    body = resp.text
                    if not body.startswith("#r2modman") and not body.startswith(PROFILE_DATA_PREFIX.strip()):
                        # Some responses are raw base64 without prefix
                        try:
                            return decode_share_blob(PROFILE_DATA_PREFIX + body), backend
                        except Exception:
                            pass
                    return decode_share_blob(body), backend
                last_err = ValueError(f"{backend} returned HTTP {resp.status_code}")
            except Exception as exc:
                last_err = exc
                logger.info("share-code fetch %s failed: %s", backend, exc)
    raise ValueError(f"Could not fetch profile code {code}: {last_err}")


async def resolve_import_bytes(*, code: str | None = None, file_bytes: bytes | None = None) -> bytes:
    if file_bytes:
        if file_bytes[:2] == b"PK":
            return file_bytes
        # Maybe a text blob uploaded
        try:
            return decode_share_blob(file_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError("Upload is not a .r2z zip or #r2modman blob") from exc
    if code:
        code = code.strip()
        if code.startswith("#r2modman"):
            return decode_share_blob(code)
        zip_bytes, _ = await fetch_share_code(code)
        return zip_bytes
    raise ValueError("Provide a share code or .r2z file")


def build_manifest_dict(
    *,
    name: str,
    mods: Iterable[ProfileModSpec],
    community: str | None = "valheim",
    include_source: bool = True,
) -> dict[str, Any]:
    out_mods: list[dict[str, Any]] = []
    for m in mods:
        major, minor, patch = version_parts(m.version)
        entry: dict[str, Any] = {
            "name": m.full_name,
            "version": {"major": major, "minor": minor, "patch": patch},
            "enabled": bool(m.enabled),
        }
        if include_source:
            entry["source"] = source_label(m.source)
        out_mods.append(entry)
    manifest: dict[str, Any] = {"profileName": name, "mods": out_mods}
    if community:
        manifest["community"] = community
    return manifest


def build_r2z_bytes(
    *,
    name: str,
    mods: list[ProfileModSpec],
    config_files: dict[str, bytes] | None = None,
    mode: ExportMode = "full",
    community: str | None = "valheim",
) -> bytes:
    include_source = mode == "full"
    manifest = build_manifest_dict(
        name=name, mods=mods, community=community, include_source=include_source
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export.r2x", yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
        for rel, content in (config_files or {}).items():
            rel = rel.replace("\\", "/").lstrip("/")
            if mode == "full":
                member = rel if rel.startswith("BepInEx/") else f"BepInEx/{rel}"
            else:
                # r2modman-style: config/... without BepInEx prefix
                member = rel[len("BepInEx/") :] if rel.startswith("BepInEx/") else rel
            zf.writestr(member, content)
    return buf.getvalue()


def encode_share_blob(zip_bytes: bytes) -> str:
    return PROFILE_DATA_PREFIX + base64.b64encode(zip_bytes).decode("ascii")


def has_hexium_exclusive(
    mods: Iterable[ProfileModSpec],
    *,
    thunderstore_has_version,
) -> bool:
    """Gale rule: Hexium-backed mod whose exact version is missing on Thunderstore."""
    for m in mods:
        if m.source != "hexium":
            continue
        if not thunderstore_has_version(m.full_name, m.version):
            return True
    return False


async def upload_share_code(zip_bytes: bytes, *, backend: str) -> str:
    blob = encode_share_blob(zip_bytes)
    url = HEXIUM_CREATE if backend == "hexium" else THUNDERSTORE_CREATE
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, headers=headers) as client:
        resp = await client.post(url, content=blob.encode("utf-8"))
        if resp.status_code in (413, 400) and "large" in resp.text.lower():
            raise ValueError(
                "Profile is too large for a share code; download the .r2z file instead."
            )
        if resp.status_code >= 400:
            raise ValueError(f"Share-code upload failed ({backend} HTTP {resp.status_code})")
        data = resp.json()
        key = data.get("key") or data.get("code")
        if not key:
            raise ValueError(f"Share-code upload returned no key: {data}")
        key_s = str(key)
        # Thunderstore returns UUIDs Gale/r2 can paste into "code" import.
        # Hexium often returns 32-char hex — Gale Sync paste field rejects those.
        if backend == "hexium" and not UUID_RE.match(key_s):
            if len(key_s) == 32 and all(c in "0123456789abcdefABCDEF" for c in key_s):
                key_s = (
                    f"{key_s[0:8]}-{key_s[8:12]}-{key_s[12:16]}-"
                    f"{key_s[16:20]}-{key_s[20:32]}"
                )
        return key_s


def collect_bepinex_configs(bepinex_root: Path) -> dict[str, bytes]:
    """Read config files from a live BepInEx tree for export."""
    out: dict[str, bytes] = {}
    config_dir = bepinex_root / "config"
    if not config_dir.is_dir():
        return out
    for path in config_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".cfg", ".txt", ".json", ".yml", ".yaml", ".ini"}:
            continue
        rel = path.relative_to(bepinex_root).as_posix()
        try:
            out[rel] = path.read_bytes()
        except OSError:
            continue
    return out


def apply_configs_to_bepinex(bepinex_root: Path, files: dict[str, bytes]) -> list[str]:
    written: list[str] = []
    root = bepinex_root.resolve()
    for rel, content in files.items():
        rel = rel.replace("\\", "/").strip("/")
        if not rel or rel.endswith("/"):
            continue
        dest = (bepinex_root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError:
            continue
        cursor = dest.parent
        while cursor != root:
            try:
                cursor.relative_to(root)
            except ValueError:
                break
            if cursor.is_file():
                cursor.unlink()
            if cursor.parent == cursor:
                break
            cursor = cursor.parent
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        written.append(rel)
    return written
