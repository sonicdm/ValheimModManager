from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import ConfigAssociation, InstalledPackage, OwnedFile
from .dll_meta import read_bepinex_plugin_metadata
from .settings_service import log_activity, set_setting


@dataclass
class ScannedPlugin:
    name: str
    full_name: str
    version: str | None
    owner: str | None
    plugin_guid: str | None
    install_path: str
    managed: bool
    enabled: bool
    source: str
    description: str | None = None
    dependencies: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_manifest(folder: Path) -> dict | None:
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_configs(config_dir: Path, guid: str | None, name: str | None) -> list[str]:
    matches: list[str] = []
    if not config_dir.is_dir():
        return matches
    candidates: list[str] = []
    if guid:
        candidates.append(f"{guid}.cfg")
        # common pattern: last segment of guid
        if "." in guid:
            candidates.append(f"{guid.split('.')[-1]}.cfg")
    if name:
        safe = name.replace(" ", "").replace("ö", "o").replace("Ö", "O")
        candidates.append(f"{safe}.cfg")
        candidates.append(f"{name.lower().replace(' ', '_')}.cfg")
        candidates.append(f"{name}.cfg")

    lowered = {c.lower() for c in candidates}
    for cfg in config_dir.glob("*.cfg"):
        if cfg.name.lower() in lowered or (guid and guid.lower() in cfg.name.lower()):
            matches.append(cfg.name)
        elif name and name.lower().replace(" ", "") in cfg.name.lower().replace(" ", ""):
            matches.append(cfg.name)
    # known mappings
    known = {
        "serverdevcommands": "server_devcommands.cfg",
        "webmap": "com.valheimwebmap.server.cfg",
        "portalatlas": "sonicdm.valheimportallist.cfg",
        "jotunn": None,
    }
    key = (name or "").lower().replace(" ", "").replace("ö", "o")
    mapped = known.get(key)
    if mapped and mapped not in matches and (config_dir / mapped).is_file():
        matches.append(mapped)
    return sorted(set(matches))


def _collect_files(folder: Path, root: Path) -> list[str]:
    files: list[str] = []
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files.append(_rel(root, path))
    return sorted(files)


def scan_plugins(settings: Settings | None = None) -> list[ScannedPlugin]:
    settings = settings or get_settings()
    plugins_dir = settings.plugins_dir
    config_dir = settings.config_dir
    results: list[ScannedPlugin] = []
    if not plugins_dir.is_dir():
        return results

    seen_files: set[str] = set()

    # Package folders first
    for entry in sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        manifest = _load_manifest(entry)
        files = _collect_files(entry, plugins_dir)
        seen_files.update(files)
        dlls = list(entry.rglob("*.dll"))
        guid = name = version = None
        for dll in dlls:
            if dll.name.endswith(".dll.disabled"):
                continue
            m = read_bepinex_plugin_metadata(str(dll))
            if m.guid or m.name:
                guid, name, version = m.guid, m.name, m.version
                break

        enabled = True
        disabled_dlls = list(entry.rglob("*.dll.disabled"))
        active_dlls = list(entry.rglob("*.dll"))
        if disabled_dlls and not active_dlls:
            enabled = False

        if manifest:
            owner = None
            full_name = entry.name
            # Prefer folder naming Team-Mod
            if "-" in entry.name:
                parts = entry.name.split("-", 1)
                owner = parts[0]
            results.append(
                ScannedPlugin(
                    name=manifest.get("name") or entry.name,
                    full_name=full_name if owner else (manifest.get("name") or entry.name),
                    version=manifest.get("version_number") or version,
                    owner=owner or manifest.get("author"),
                    plugin_guid=guid,
                    install_path=str(entry),
                    managed=True,
                    enabled=enabled,
                    source="local",
                    description=manifest.get("description"),
                    dependencies=list(manifest.get("dependencies") or []),
                    files=files,
                    config_files=_find_configs(config_dir, guid, manifest.get("name") or name),
                )
            )
        else:
            results.append(
                ScannedPlugin(
                    name=name or entry.name,
                    full_name=entry.name,
                    version=version,
                    owner=None,
                    plugin_guid=guid,
                    install_path=str(entry),
                    managed=False,
                    enabled=enabled,
                    source="unmanaged",
                    files=files,
                    config_files=_find_configs(config_dir, guid, name),
                )
            )

    # Loose DLLs at plugins root
    folder_names = {r.name.lower().replace(" ", "") for r in results}
    folder_guids = {r.plugin_guid.lower() for r in results if r.plugin_guid}
    for dll in sorted(plugins_dir.glob("*.dll")):
        if dll.is_symlink():
            continue
        rel = _rel(plugins_dir, dll)
        if rel in seen_files:
            continue
        m = read_bepinex_plugin_metadata(str(dll))
        # Skip loose copies that duplicate a package folder already inventoried
        if m.guid and m.guid.lower() in folder_guids:
            continue
        if (m.name or dll.stem).lower().replace(" ", "") in folder_names:
            continue
        enabled = True
        results.append(
            ScannedPlugin(
                name=m.name or dll.stem,
                full_name=dll.stem,
                version=m.version,
                owner=None,
                plugin_guid=m.guid,
                install_path=str(dll),
                managed=False,
                enabled=enabled,
                source="unmanaged",
                files=[rel],
                config_files=_find_configs(config_dir, m.guid, m.name or dll.stem),
            )
        )

    for disabled in sorted(plugins_dir.glob("*.dll.disabled")):
        if disabled.is_symlink():
            continue
        rel = _rel(plugins_dir, disabled)
        active_name = disabled.name[: -len(".disabled")]
        # Skip if active counterpart exists
        if (plugins_dir / active_name).exists():
            continue
        m = read_bepinex_plugin_metadata(str(disabled))
        results.append(
            ScannedPlugin(
                name=m.name or Path(active_name).stem,
                full_name=Path(active_name).stem,
                version=m.version,
                owner=None,
                plugin_guid=m.guid,
                install_path=str(disabled),
                managed=False,
                enabled=False,
                source="unmanaged",
                files=[rel],
                config_files=_find_configs(config_dir, m.guid, m.name),
            )
        )

    return results


def persist_scan(db: Session, settings: Settings | None = None) -> list[InstalledPackage]:
    settings = settings or get_settings()
    scanned = scan_plugins(settings)
    by_path = {p.install_path: p for p in db.query(InstalledPackage).all()}
    seen_paths: set[str] = set()
    result_packages: list[InstalledPackage] = []

    for item in scanned:
        seen_paths.add(item.install_path)
        pkg = by_path.get(item.install_path)
        if pkg is None and item.managed:
            # Only reuse an existing row for managed packages with the same full_name
            # when that row's path is gone or identical — never collapse onto a loose DLL.
            candidate = (
                db.query(InstalledPackage)
                .filter(
                    InstalledPackage.full_name == item.full_name,
                    InstalledPackage.managed.is_(True),
                )
                .first()
            )
            if candidate is not None and (
                candidate.install_path == item.install_path
                or candidate.install_path not in seen_paths
            ):
                # Avoid stealing another scanned path in this same run
                if candidate.install_path == item.install_path or candidate.install_path not in {
                    s.install_path for s in scanned
                }:
                    pkg = candidate
        if pkg is None:
            pkg = InstalledPackage(
                source=item.source if item.managed else "unmanaged",
                full_name=item.full_name,
                name=item.name,
                owner=item.owner,
                version=item.version,
                plugin_guid=item.plugin_guid,
                install_path=item.install_path,
                managed=item.managed,
                enabled=item.enabled,
                description=item.description,
                dependencies_json=json.dumps(item.dependencies),
            )
            db.add(pkg)
            db.flush()
        else:
            # Preserve thunderstore/hexium source if already linked
            if pkg.source not in ("thunderstore", "hexium"):
                # Do not demote a managed package when revisiting via a weaker match
                if item.managed or not pkg.managed:
                    pkg.source = item.source if item.managed else "unmanaged"
                    pkg.managed = item.managed
            if item.managed or not pkg.managed:
                pkg.install_path = item.install_path
            pkg.name = item.name
            pkg.owner = item.owner or pkg.owner
            pkg.version = item.version or pkg.version
            pkg.plugin_guid = item.plugin_guid or pkg.plugin_guid
            pkg.enabled = item.enabled
            if item.description:
                pkg.description = item.description
            if item.dependencies:
                pkg.dependencies_json = json.dumps(item.dependencies)
            pkg.last_scanned_at = datetime.now(timezone.utc)

        # Refresh owned files for unmanaged/local; keep managed ownership if already tracked
        if not pkg.files or pkg.source in ("local", "unmanaged"):
            pkg.files.clear()
            for rel in item.files:
                full = settings.plugins_dir / rel
                sha = _file_sha256(full) if full.is_file() else None
                size = full.stat().st_size if full.is_file() else None
                pkg.files.append(OwnedFile(relative_path=rel, sha256=sha, size=size))

        pkg.configs.clear()
        for cfg in item.config_files:
            pkg.configs.append(ConfigAssociation(relative_path=cfg))

        result_packages.append(pkg)

    # Remove DB rows for paths that disappeared (only unmanaged/local auto-scanned)
    for pkg in list(db.query(InstalledPackage).all()):
        if pkg.install_path not in seen_paths and pkg.source in ("local", "unmanaged"):
            db.delete(pkg)

    set_setting(db, "last_scan_at", datetime.now(timezone.utc).isoformat())
    log_activity(
        db,
        "scan",
        result="ok",
        message=f"Scanned {len(result_packages)} plugins",
        details={"count": len(result_packages)},
    )
    db.commit()
    for pkg in result_packages:
        db.refresh(pkg)
    return result_packages


def detect_drift(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    live = settings.live_plugins_root
    config_plugins = settings.plugins_dir
    if live is None or not live.is_dir() or not config_plugins.is_dir():
        return []
    config_names = {p.name for p in config_plugins.iterdir()}
    live_names = {p.name for p in live.iterdir()}
    only_config = sorted(config_names - live_names)
    only_live = sorted(live_names - config_names)
    messages = []
    for name in only_config:
        messages.append(f"Present in config but missing from live plugins: {name}")
    for name in only_live:
        messages.append(f"Present in live plugins but missing from config: {name}")
    return messages
