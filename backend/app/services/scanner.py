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
from .live_sync import live_only_plugin_names, path_is_under_live
from .settings_service import log_activity, set_setting

# Generated / runtime trees inside live-only plugins — skip for ownership inventory.
_RUNTIME_DIR_NAMES = frozenset({"map_data", "cache", "logs", ".git", "__pycache__"})


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
    # Common Thunderstore package → cfg filename hints (not host-specific).
    known = {
        "serverdevcommands": "server_devcommands.cfg",
        "webmap": "com.valheimwebmap.server.cfg",
        "jotunn": None,
    }
    key = (name or "").lower().replace(" ", "").replace("ö", "o")
    mapped = known.get(key)
    if mapped and mapped not in matches and (config_dir / mapped).is_file():
        matches.append(mapped)
    return sorted(set(matches))


def _collect_files(folder: Path, root: Path, *, skip_runtime: bool = False) -> list[str]:
    files: list[str] = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if skip_runtime:
            try:
                parts = path.relative_to(folder).parts
            except ValueError:
                continue
            if any(part.lower() in _RUNTIME_DIR_NAMES for part in parts):
                continue
        files.append(_rel(root, path))
    return sorted(files)


def _scan_folder(
    entry: Path,
    *,
    files_root: Path,
    config_dir: Path,
    skip_runtime: bool = False,
) -> ScannedPlugin:
    manifest = _load_manifest(entry)
    files = _collect_files(entry, files_root, skip_runtime=skip_runtime)
    dlls = [
        d
        for d in entry.rglob("*.dll")
        if not any(part.lower() in _RUNTIME_DIR_NAMES for part in d.relative_to(entry).parts)
    ]
    guid = name = version = None
    for dll in dlls:
        if dll.name.endswith(".dll.disabled"):
            continue
        m = read_bepinex_plugin_metadata(str(dll))
        if m.guid or m.name:
            guid, name, version = m.guid, m.name, m.version
            break

    enabled = True
    disabled_dlls = [d for d in entry.rglob("*.dll.disabled")]
    active_dlls = [d for d in dlls if not d.name.endswith(".dll.disabled")]
    if disabled_dlls and not active_dlls:
        enabled = False

    if manifest:
        owner = None
        full_name = entry.name
        if "-" in entry.name:
            owner = entry.name.split("-", 1)[0]
        return ScannedPlugin(
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
    return ScannedPlugin(
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


def scan_plugins(settings: Settings | None = None) -> list[ScannedPlugin]:
    settings = settings or get_settings()
    plugins_dir = settings.plugins_dir
    config_dir = settings.config_dir
    results: list[ScannedPlugin] = []
    if not plugins_dir.is_dir():
        # Still scan live-only below if config mount is missing
        plugins_dir_ok = False
    else:
        plugins_dir_ok = True

    seen_files: set[str] = set()
    seen_names: set[str] = set()

    if plugins_dir_ok:
        for entry in sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.is_symlink():
                continue
            scanned = _scan_folder(entry, files_root=plugins_dir, config_dir=config_dir)
            seen_files.update(scanned.files)
            seen_names.add(entry.name.lower())
            results.append(scanned)

        folder_names = {r.name.lower().replace(" ", "") for r in results}
        folder_guids = {r.plugin_guid.lower() for r in results if r.plugin_guid}
        for dll in sorted(plugins_dir.glob("*.dll")):
            if dll.is_symlink():
                continue
            rel = _rel(plugins_dir, dll)
            if rel in seen_files:
                continue
            m = read_bepinex_plugin_metadata(str(dll))
            if m.guid and m.guid.lower() in folder_guids:
                continue
            if (m.name or dll.stem).lower().replace(" ", "") in folder_names:
                continue
            results.append(
                ScannedPlugin(
                    name=m.name or dll.stem,
                    full_name=dll.stem,
                    version=m.version,
                    owner=None,
                    plugin_guid=m.guid,
                    install_path=str(dll),
                    managed=False,
                    enabled=True,
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

    # Persistent packages: plugins/<name> is a symlink into .persistent
    from .live_sync import is_persistent_link, persistent_package_names

    for name in sorted(persistent_package_names(settings), key=str.lower):
        if name.lower() in seen_names:
            continue
        link = settings.plugins_dir / name
        if not is_persistent_link(link, settings):
            continue
        try:
            entry = link.resolve()
        except OSError:
            entry = settings.persistent_dir / name
        if not entry.is_dir():
            continue
        scanned = _scan_folder(
            entry, files_root=settings.persistent_dir, config_dir=config_dir, skip_runtime=True
        )
        scanned.full_name = name
        results.append(scanned)

    return results


def package_present_on_disk(pkg: InstalledPackage, settings: Settings) -> bool:
    """True if the package folder/file still exists under plugins or .persistent."""
    install = Path(pkg.install_path)
    try:
        if install.exists():
            return True
    except OSError:
        pass

    names = {pkg.full_name, install.name}
    if pkg.name:
        names.add(pkg.name)
        names.add(pkg.name.replace(" ", ""))
    for name in names:
        if not name or name in (".", ".."):
            continue
        try:
            if (settings.plugins_dir / name).exists():
                return True
            if (settings.persistent_dir / name).exists():
                return True
        except OSError:
            continue
    return False


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
            files_root = (
                settings.persistent_dir
                if path_is_under_live(item.install_path, settings)
                else settings.plugins_dir
            )
            for rel in item.files:
                full = files_root / rel
                sha = _file_sha256(full) if full.is_file() else None
                size = full.stat().st_size if full.is_file() else None
                pkg.files.append(OwnedFile(relative_path=rel, sha256=sha, size=size))

        pkg.configs.clear()
        for cfg in item.config_files:
            pkg.configs.append(ConfigAssociation(relative_path=cfg))

        # Auto-link local/unmanaged packages to Thunderstore/Hexium when unambiguous
        if pkg.source in ("local", "unmanaged"):
            try:
                from .packages import match_installed_to_remote

                matched = match_installed_to_remote(
                    db,
                    full_name=pkg.full_name,
                    name=pkg.name,
                    owner=pkg.owner,
                    version=pkg.version,
                )
                if matched:
                    src, info = matched
                    pkg.source = src
                    pkg.full_name = info.full_name
                    pkg.owner = info.owner or pkg.owner
                    pkg.managed = True
                    pkg.package_url = info.package_url
                    pkg.icon_url = info.icon_url or pkg.icon_url
                    pkg.description = info.description or pkg.description
                    if info.latest:
                        # Keep installed version; only fill deps from matching or latest
                        ver = next(
                            (v for v in info.versions if v.version_number == pkg.version),
                            info.latest,
                        )
                        pkg.dependencies_json = json.dumps(ver.dependencies)
            except Exception:
                # Matching is best-effort; never fail the scan
                pass

        result_packages.append(pkg)

    # Drop DB rows whose files are gone — including managed Thunderstore/Hexium installs.
    # Previously only local/unmanaged were pruned, so removed mods stayed listed forever.
    pruned: list[str] = []
    for pkg in list(db.query(InstalledPackage).all()):
        if pkg.install_path in seen_paths:
            continue
        if package_present_on_disk(pkg, settings):
            continue
        pruned.append(pkg.full_name)
        db.delete(pkg)

    set_setting(db, "last_scan_at", datetime.now(timezone.utc).isoformat())
    log_activity(
        db,
        "scan",
        result="ok",
        message=f"Scanned {len(result_packages)} plugins"
        + (f"; pruned {len(pruned)} missing" if pruned else ""),
        details={"count": len(result_packages), "pruned": pruned},
    )
    db.commit()
    for pkg in result_packages:
        db.refresh(pkg)
    return result_packages


def detect_drift(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    messages = []
    for name in sorted(live_only_plugin_names(settings)):
        messages.append(
            f"Persistent package (symlink in plugins → .persistent/{name}; bootstrap copies the link only)"
        )
    return messages
