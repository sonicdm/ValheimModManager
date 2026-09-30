from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import ConfigAssociation, InstalledPackage, OwnedFile, PendingUpdate
from .dll_meta import read_bepinex_plugin_metadata
from .live_sync import live_only_plugin_names, path_is_under_live
from .settings_service import log_activity, set_setting

logger = logging.getLogger(__name__)


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
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        # Dual mounts (/valheim/bepinex vs /config/bepinex) can disagree on the
        # absolute prefix after symlink resolve — fall back to the basename path.
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return path.name


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


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


def _collect_top_level_files(folder: Path, root: Path) -> list[str]:
    """Package-root payload only (DLL, manifest, README, icon, …) — never recurse."""
    files: list[str] = []
    try:
        entries = folder.iterdir()
    except OSError:
        return files
    for path in entries:
        if not _safe_is_file(path) or _safe_is_symlink(path):
            continue
        try:
            files.append(_rel(root, path))
        except (ValueError, OSError):
            continue
    return sorted(files)


def _glob_dlls(folder: Path, pattern: str) -> list[Path]:
    try:
        return [p for p in folder.glob(pattern) if _safe_is_file(p)]
    except OSError:
        return []


def _find_plugin_dlls(folder: Path, *, root_only: bool = False) -> tuple[list[Path], list[Path]]:
    """Locate *.dll / *.dll.disabled via glob.

    Package root first (Thunderstore-style flat packs). Nested rglob only when
    there is no root DLL and the caller allows it (folders without a manifest).
    """
    active = _glob_dlls(folder, "*.dll")
    disabled = _glob_dlls(folder, "*.dll.disabled")
    if active or disabled or root_only:
        return active, disabled
    try:
        active = [p for p in folder.rglob("*.dll") if _safe_is_file(p)]
        disabled = [p for p in folder.rglob("*.dll.disabled") if _safe_is_file(p)]
    except OSError:
        return [], []
    return active, disabled


def _pick_plugin_metadata(
    dlls: list[Path], *, prefer_name: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """One folder = one mod. Among many DLLs, pick a single BepInEx plugin identity."""
    hits: list[tuple[Path, object]] = []
    for dll in dlls:
        m = read_bepinex_plugin_metadata(str(dll))
        if m.guid or m.name:
            hits.append((dll, m))
    if not hits:
        return None, None, None

    if prefer_name:
        key = prefer_name.lower().replace(" ", "")
        for dll, m in hits:
            stem = dll.stem.lower().replace(" ", "")
            meta_name = (getattr(m, "name", None) or "").lower().replace(" ", "")
            if stem == key or meta_name == key:
                return m.guid, m.name, m.version

    m = hits[0][1]
    return m.guid, m.name, m.version


def _scan_folder(
    entry: Path,
    *,
    files_root: Path,
    config_dir: Path,
) -> ScannedPlugin:
    """Scan one package directory as a single mod (never one-mod-per-DLL).

    Unit of packaging is the folder. ``manifest.json`` (if present) is authoritative
    for name/version/deps; DLLs only enrich guid / enabled state.
    """
    # Do not rglob("*") — only root payload + targeted *.dll globs. Managed installs
    # already record full zip membership for uninstall/backup.
    manifest = _load_manifest(entry)
    files = _collect_top_level_files(entry, files_root)
    dlls_active, dlls_disabled = _find_plugin_dlls(entry, root_only=manifest is not None)
    for dll in dlls_active + dlls_disabled:
        try:
            rel = _rel(files_root, dll)
        except (ValueError, OSError):
            continue
        if rel not in files:
            files.append(rel)
    files = sorted(files)

    prefer = (manifest or {}).get("name") if manifest else None
    guid, name, version = _pick_plugin_metadata(dlls_active, prefer_name=prefer)

    enabled = True
    if dlls_disabled and not dlls_active:
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
        try:
            entries = sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            # Check symlink before is_dir(): following a broken / cross-mount link
            # can raise OSError (Errno / WinError) and abort the whole scan.
            if _safe_is_symlink(entry) or not _safe_is_dir(entry):
                continue
            scanned = _scan_folder(entry, files_root=plugins_dir, config_dir=config_dir)
            seen_files.update(scanned.files)
            seen_names.add(entry.name.lower())
            results.append(scanned)

        # Loose DLLs directly under plugins/ are each their own unmanaged mod.
        # DLLs inside a package folder are never split out here — folders are
        # already one ScannedPlugin from _scan_folder above.
        folder_names = {r.name.lower().replace(" ", "") for r in results}
        folder_guids = {r.plugin_guid.lower() for r in results if r.plugin_guid}
        try:
            dll_iter = sorted(plugins_dir.glob("*.dll"))
        except OSError:
            dll_iter = []
        for dll in dll_iter:
            if _safe_is_symlink(dll):
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

        try:
            disabled_iter = sorted(plugins_dir.glob("*.dll.disabled"))
        except OSError:
            disabled_iter = []
        for disabled in disabled_iter:
            if _safe_is_symlink(disabled):
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

    # Persistent packages: always scan via /valheim/bepinex/.persistent/<name>
    # (not the resolved /config/... symlink target) so relative paths stay under
    # the manager's bepinex_root mount.
    from .live_sync import persistent_package_names

    for name in sorted(persistent_package_names(settings), key=str.lower):
        if name.lower() in seen_names:
            continue
        entry = settings.persistent_dir / name
        if not _safe_is_dir(entry):
            continue
        scanned = _scan_folder(entry, files_root=settings.persistent_dir, config_dir=config_dir)
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


def persist_scan(
    db: Session, settings: Settings | None = None, *, reason: str = "manual"
) -> tuple[list[InstalledPackage], list[str]]:
    settings = settings or get_settings()
    scanned = scan_plugins(settings)
    by_path = {p.install_path: p for p in db.query(InstalledPackage).all()}
    # (source, full_name) → install_path — prevents two folders linking to one store id
    claimed: dict[tuple[str, str], str] = {
        (p.source, p.full_name): p.install_path for p in by_path.values()
    }
    seen_paths: set[str] = set()
    result_packages: list[InstalledPackage] = []

    def _claim(source: str, full_name: str, install_path: str) -> bool:
        key = (source, full_name)
        holder = claimed.get(key)
        if holder is not None and holder != install_path:
            return False
        claimed[key] = install_path
        return True

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
            initial_source = item.source if item.managed else "unmanaged"
            if not _claim(initial_source, item.full_name, item.install_path):
                # Extremely rare: two scanned entries want the same local identity.
                # Keep a unique full_name so the scan can continue.
                alt_name = f"{item.full_name}@{Path(item.install_path).name}"
                if not _claim(initial_source, alt_name, item.install_path):
                    logger.warning(
                        "Skipping scan entry %s — identity %s/%s already claimed",
                        item.install_path,
                        initial_source,
                        item.full_name,
                    )
                    continue
                item.full_name = alt_name
            pkg = InstalledPackage(
                source=initial_source,
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
            by_path[item.install_path] = pkg
        else:
            # Preserve thunderstore/hexium source if already linked
            if pkg.source not in ("thunderstore", "hexium"):
                # Do not demote a managed package when revisiting via a weaker match
                if item.managed or not pkg.managed:
                    new_source = item.source if item.managed else "unmanaged"
                    if (pkg.source, pkg.full_name) != (new_source, item.full_name):
                        if _claim(new_source, item.full_name, item.install_path):
                            old = (pkg.source, pkg.full_name)
                            if claimed.get(old) == pkg.install_path:
                                claimed.pop(old, None)
                            pkg.source = new_source
                            pkg.full_name = item.full_name
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

        # Auto-link local/unmanaged packages to Thunderstore/Hexium when unambiguous.
        # Never steal a (source, full_name) already claimed by another install path
        # (e.g. plugins/PortalAtlas and plugins/SonicDM-PortalAtlas both matching
        # SonicDM-PortalAtlas).
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
                    if _claim(src, info.full_name, pkg.install_path):
                        old = (pkg.source, pkg.full_name)
                        if claimed.get(old) == pkg.install_path:
                            claimed.pop(old, None)
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
                    else:
                        logger.info(
                            "Skip auto-link for %s → %s/%s (already installed at %s)",
                            pkg.install_path,
                            src,
                            info.full_name,
                            claimed.get((src, info.full_name)),
                        )
            except Exception:
                # Matching is best-effort; never fail the scan
                pass

        result_packages.append(pkg)

    pruned: list[str] = []
    for pkg in list(db.query(InstalledPackage).all()):
        if pkg.install_path in seen_paths:
            continue
        if package_present_on_disk(pkg, settings):
            continue
        pruned.append(pkg.full_name)
        # Drop queued update rows for packages that no longer exist on disk
        db.query(PendingUpdate).filter(
            PendingUpdate.full_name == pkg.full_name,
            PendingUpdate.source == pkg.source,
        ).delete(synchronize_session=False)
        db.delete(pkg)

    set_setting(db, "last_scan_at", datetime.now(timezone.utc).isoformat())
    label = "Startup scan" if reason == "startup" else "Plugin scan"
    log_activity(
        db,
        "scan",
        result="ok",
        message=f"{label} finished: {len(result_packages)} plugins"
        + (f"; pruned {len(pruned)} missing" if pruned else ""),
        details={"count": len(result_packages), "pruned": pruned, "reason": reason},
    )
    db.commit()
    for pkg in result_packages:
        db.refresh(pkg)
    return result_packages, pruned


def detect_drift(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    messages = []
    for name in sorted(live_only_plugin_names(settings)):
        messages.append(
            f"Persistent package (symlink in plugins → .persistent/{name}; bootstrap copies the link only)"
        )
    return messages
