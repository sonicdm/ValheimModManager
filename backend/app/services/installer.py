from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import InstalledPackage, OwnedFile
from .dll_meta import read_bepinex_plugin_metadata
from .live_sync import (
    ensure_persistent_symlink,
    merge_tree_into,
    path_is_under_live,
    remove_paths_from_live,
    resolve_live_only_folder,
    sync_config_tree_to_live,
)
from .paths import PathEscapeError, ensure_directory, ensure_within, validate_archive_member
from .settings_service import log_activity, set_setting
from .packages import get_package, match_installed_to_remote, resolve_dependencies

logger = logging.getLogger(__name__)

# Runtime / generated trees inside live-only plugins — never treat as package-owned.
_RUNTIME_DIR_NAMES = frozenset({"map_data", "cache", "logs", ".git", "__pycache__"})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


async def download_package(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
    return dest


def _ensure_directory(path: Path) -> None:
    """Create path as a directory, removing any file that blocks the path.

    Some Thunderstore zips (or leftover staging) can leave a file where a directory
    is required; mkdir(parents=True) then raises NotADirectoryError (Errno 20).
    """
    ensure_directory(path)


def _safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Directories first, then shorter paths, so parents exist before children.
        members = sorted(
            zf.infolist(),
            key=lambda info: (
                0 if (info.is_dir() or info.filename.replace("\\", "/").endswith("/")) else 1,
                info.filename.replace("\\", "/").count("/"),
                info.filename.replace("\\", "/"),
            ),
        )
        for info in members:
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/") or info.is_dir():
                if name:
                    target_dir = validate_archive_member(name.rstrip("/"), dest)
                    _ensure_directory(target_dir)
                continue
            target = validate_archive_member(name, dest)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PathEscapeError(f"Refusing to extract symlink: {name}")
            _ensure_directory(target.parent)
            # A prior bad extract may have left a directory where this file belongs.
            if target.exists() and target.is_dir():
                shutil.rmtree(target)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(target)
    return extracted


def _detect_plugin_root(stage: Path) -> Path:
    # Prefer BepInEx plugins tree when present (Thunderstore packs often have
    # manifest.json at the zip root AND plugins/<Mod>/ underneath).
    if (stage / "plugins").is_dir():
        return stage / "plugins"
    if (stage / "BepInEx" / "plugins").is_dir():
        return stage / "BepInEx" / "plugins"
    if (stage / "manifest.json").is_file() or any(stage.glob("*.dll")):
        return stage
    subdirs = [p for p in stage.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return _detect_plugin_root(subdirs[0])
    return stage


def _detect_patchers(stage: Path) -> Path | None:
    if (stage / "patchers").is_dir():
        return stage / "patchers"
    if (stage / "BepInEx" / "patchers").is_dir():
        return stage / "BepInEx" / "patchers"
    return None


def _copy_tree(src: Path, dest: Path) -> list[tuple[str, Path]]:
    copied: list[tuple[str, Path]] = []
    _ensure_directory(dest)
    if src.is_file():
        target = dest / src.name
        shutil.copy2(src, target)
        copied.append((src.name, target))
        return copied
    for path in src.rglob("*"):
        if path.is_dir() or path.is_symlink():
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        ensure_within(dest, target.parent)
        _ensure_directory(target.parent)
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
        shutil.copy2(path, target)
        copied.append((rel, target))
    return copied


def _backup_files(files: list[Path], label: str, settings: Settings) -> Path | None:
    existing = [p for p in files if p.exists() and p.is_file()]
    if not existing:
        return None
    backup_tmp = settings.backups_dir / "pre_install" / f"{label}-{_stamp()}"
    backup_tmp.mkdir(parents=True, exist_ok=True)
    for path in existing:
        try:
            dest = backup_tmp / path.name
            # Preserve relative structure when under a common parent
            shutil.copy2(path, dest)
        except OSError as exc:
            logger.warning("Backup skip %s: %s", path, exc)
    return backup_tmp


def _resolve_dest(
    settings: Settings,
    item: dict[str, Any],
    existing: InstalledPackage | None,
) -> tuple[Path, Path, bool]:
    """Return (dest_dir, files_root, live_only).

    Persistent packages install/update under config/bepinex/.persistent with a
    plugins/ symlink; normal packages install under plugins/.

    When updating an already-tracked package, always reuse its current install
    folder — never create plugins/<Team-Mod> beside an older plugins/<Name>
    path (that is how PortalAtlas duplicates appear).
    """
    name = item.get("name") or item["full_name"]
    live_folder = resolve_live_only_folder(
        settings, full_name=item["full_name"], name=name
    )
    if live_folder is None and existing and path_is_under_live(existing.install_path, settings):
        live_folder = Path(existing.install_path)
        if live_folder.is_file():
            live_folder = live_folder.parent
        # Prefer the canonical .persistent folder name
        if live_folder.parent != settings.persistent_dir:
            candidate = settings.persistent_dir / item["full_name"]
            if candidate.is_dir():
                live_folder = candidate

    if live_folder is not None:
        settings.persistent_dir.mkdir(parents=True, exist_ok=True)
        return live_folder, settings.persistent_dir, True

    if existing and existing.install_path:
        cur = Path(existing.install_path)
        try:
            if cur.is_file():
                # Loose DLL under plugins/ — promote into a folder on upgrade only
                # when we do not already own a package directory.
                if cur.parent == settings.plugins_dir:
                    return settings.plugins_dir / item["full_name"], settings.plugins_dir, False
            elif cur.is_dir():
                try:
                    cur.relative_to(settings.plugins_dir)
                    return cur, settings.plugins_dir, False
                except ValueError:
                    pass
        except OSError:
            pass

    dest = settings.plugins_dir / item["full_name"]
    return dest, settings.plugins_dir, False


def _normalize_copied(
    copied: list[tuple[str, Path]],
    files_root: Path,
    folder_name: str,
) -> list[tuple[str, Path]]:
    normalized: list[tuple[str, Path]] = []
    for rel, full in copied:
        try:
            normalized.append((full.relative_to(files_root).as_posix(), full))
        except ValueError:
            normalized.append((f"{folder_name}/{rel}", full))
    return normalized


def _pick_plugin_root(plugin_root: Path, stage: Path, item: dict[str, Any]) -> Path:
    if plugin_root == stage or plugin_root.name == "plugins":
        if plugin_root.name == "plugins":
            match = None
            for child in plugin_root.iterdir():
                if child.is_dir() and (
                    child.name == item["full_name"] or child.name == item.get("name")
                ):
                    match = child
                    break
            if match is None:
                children = [c for c in plugin_root.iterdir() if c.is_dir()]
                if len(children) == 1:
                    match = children[0]
            if match:
                return match
    return plugin_root


def preview_install(db: Session, source: str, full_name: str, version: str | None = None) -> dict[str, Any]:
    plan = resolve_dependencies(db, source, full_name, version)
    conflicts: list[str] = []
    warnings: list[str] = []
    settings = get_settings()
    for item in plan:
        existing = (
            db.query(InstalledPackage)
            .filter(
                InstalledPackage.full_name == item["full_name"],
                InstalledPackage.source == item["source"],
            )
            .first()
        )
        dest_dir, _, live_only = _resolve_dest(settings, item, existing)
        if existing and existing.version and existing.version != item["version"]:
            warnings.append(
                f"{item['full_name']} will be upgraded from {existing.version} to {item['version']}"
                + (" (live-only, in place)" if live_only else "")
            )
        elif live_only:
            warnings.append(
                f"{item['full_name']} is persistent (.persistent + plugins symlink; keeps runtime data)"
            )
        if dest_dir.exists() and existing is None and not live_only:
            conflicts.append(f"Directory already exists without ownership record: {dest_dir.name}")
    return {"packages": plan, "conflicts": conflicts, "warnings": warnings}


async def install_packages(
    db: Session,
    source: str,
    full_name: str,
    version: str | None = None,
) -> list[InstalledPackage]:
    settings = get_settings()
    preview = preview_install(db, source, full_name, version)
    if preview["conflicts"]:
        raise ValueError("; ".join(preview["conflicts"]))

    installed: list[InstalledPackage] = []
    for item in preview["packages"]:
        existing = (
            db.query(InstalledPackage)
            .filter(
                InstalledPackage.full_name == item["full_name"],
                InstalledPackage.source == item["source"],
            )
            .first()
        )
        if existing and existing.managed and existing.version == item["version"] and existing.enabled:
            installed.append(existing)
            continue

        zip_name = f"{item['full_name']}-{item['version']}.zip"
        zip_path = settings.downloads_dir / zip_name
        await download_package(item["download_url"], zip_path)

        stage = settings.staging_dir / f"{item['full_name']}-{item['version']}"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            _safe_extract(zip_path, stage)
            plugin_root = _pick_plugin_root(_detect_plugin_root(stage), stage, item)
            dest_dir, files_root, live_only = _resolve_dest(settings, item, existing)
            ensure_within(files_root, dest_dir)

            if live_only:
                # Merge into .persistent; never wipe (preserves runtime data). Keep plugins symlink.
                to_overwrite: list[Path] = []
                if plugin_root.is_file():
                    to_overwrite.append(dest_dir / plugin_root.name)
                else:
                    for path in plugin_root.rglob("*"):
                        if path.is_file():
                            to_overwrite.append(dest_dir / path.relative_to(plugin_root))
                _backup_files(to_overwrite, item["full_name"], settings)
                copied = merge_tree_into(plugin_root, dest_dir)
                try:
                    ensure_persistent_symlink(dest_dir.name, settings)
                except FileExistsError:
                    logger.warning(
                        "Could not create plugins symlink for persistent package %s",
                        dest_dir.name,
                    )
                logger.info(
                    "Persistent update of %s → %s (%s files merged)",
                    item["full_name"],
                    dest_dir,
                    len(copied),
                )
            else:
                if dest_dir.exists():
                    backup_tmp = (
                        settings.backups_dir / "pre_install" / f"{item['full_name']}-{_stamp()}"
                    )
                    backup_tmp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(dest_dir, backup_tmp)
                    shutil.rmtree(dest_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                copied = _copy_tree(plugin_root, dest_dir)

            patcher_files: list[tuple[str, Path]] = []
            patchers = _detect_patchers(stage)
            if patchers and not live_only:
                for path in patchers.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(patchers).as_posix()
                        target = settings.patchers_dir / rel
                        ensure_within(settings.patchers_dir, target.parent)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
                        patcher_files.append((f"../patchers/{rel}", target))

            normalized = _normalize_copied(copied, files_root, dest_dir.name)
            for rel, full in patcher_files:
                normalized.append((rel, full))

            if existing is None:
                existing = InstalledPackage(
                    source=item["source"],
                    full_name=item["full_name"],
                    name=item["name"],
                    owner=item["owner"],
                    version=item["version"],
                    install_path=str(dest_dir),
                    managed=True,
                    enabled=True,
                    description=item.get("description"),
                    icon_url=item.get("icon_url"),
                    package_url=item.get("package_url"),
                    dependencies_json=json.dumps(item.get("dependencies") or []),
                )
                db.add(existing)
                db.flush()
            else:
                existing.source = item["source"]
                existing.version = item["version"]
                existing.managed = True
                existing.enabled = True
                existing.install_path = str(dest_dir)
                existing.description = item.get("description") or existing.description
                existing.icon_url = item.get("icon_url") or existing.icon_url
                existing.package_url = item.get("package_url") or existing.package_url
                existing.dependencies_json = json.dumps(item.get("dependencies") or [])
                existing.files.clear()

            for rel, full in normalized:
                existing.files.append(
                    OwnedFile(
                        relative_path=rel,
                        sha256=_sha256(full) if full.is_file() else None,
                        size=full.stat().st_size if full.is_file() else None,
                    )
                )

            log_activity(
                db,
                "install",
                package=item["full_name"],
                source=item["source"],
                result="ok",
                message=(
                    f"{'Updated live-only' if live_only else 'Installed'} "
                    f"{item['full_name']} {item['version']}"
                ),
            )
            installed.append(existing)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    set_setting(db, "restart_required", True)
    # Config→live only for config-managed installs; live-only already updated in place.
    owned: list[str] = []
    for pkg in installed:
        if path_is_under_live(pkg.install_path, settings):
            continue
        owned.extend(f.relative_path for f in pkg.files)
        owned.append(pkg.full_name)
    if owned:
        sync_config_tree_to_live(only_relative_paths=owned)
    db.commit()
    for pkg in installed:
        db.refresh(pkg)
    return installed


def _read_manifest(folder: Path) -> dict[str, Any]:
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _finalize_package_record(
    db: Session,
    *,
    existing: InstalledPackage | None,
    source: str,
    full_name: str,
    name: str,
    owner: str | None,
    version: str | None,
    install_path: str,
    description: str | None,
    dependencies: list[str],
    normalized_files: list[tuple[str, Path]],
    plugin_guid: str | None = None,
) -> InstalledPackage:
    if existing is None:
        existing = InstalledPackage(
            source=source,
            full_name=full_name,
            name=name,
            owner=owner,
            version=version,
            plugin_guid=plugin_guid,
            install_path=install_path,
            managed=True,
            enabled=True,
            description=description,
            dependencies_json=json.dumps(dependencies),
        )
        db.add(existing)
        db.flush()
    else:
        existing.source = source
        existing.full_name = full_name
        existing.name = name
        existing.owner = owner or existing.owner
        existing.version = version or existing.version
        existing.plugin_guid = plugin_guid or existing.plugin_guid
        existing.managed = True
        existing.enabled = True
        existing.install_path = install_path
        existing.description = description or existing.description
        existing.dependencies_json = json.dumps(dependencies)
        existing.files.clear()

    for rel, full in normalized_files:
        existing.files.append(
            OwnedFile(
                relative_path=rel,
                sha256=_sha256(full) if full.is_file() else None,
                size=full.stat().st_size if full.is_file() else None,
            )
        )
    return existing


def _files_root_for_path(path: Path | str, settings: Settings) -> Path:
    if path_is_under_live(path, settings):
        return settings.persistent_dir
    return settings.plugins_dir


def install_from_zip_file(
    db: Session,
    zip_path: Path,
    *,
    full_name_override: str | None = None,
) -> InstalledPackage:
    """Install a manually uploaded Thunderstore-style zip into config (or live if already live-only)."""
    settings = get_settings()
    stamp = _stamp()
    stage = settings.staging_dir / f"import-{stamp}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        _safe_extract(zip_path, stage)
        plugin_root = _detect_plugin_root(stage)

        if plugin_root.name == "plugins":
            children = [c for c in plugin_root.iterdir() if c.is_dir() or c.suffix.lower() == ".dll"]
            if len(children) == 1 and children[0].is_dir():
                plugin_root = children[0]

        manifest = _read_manifest(plugin_root)
        name = manifest.get("name") or plugin_root.name or zip_path.stem
        version = manifest.get("version_number")
        deps = list(manifest.get("dependencies") or [])
        description = manifest.get("description")
        owner = manifest.get("author")

        if full_name_override:
            full_name = full_name_override.strip()
        elif "-" in plugin_root.name and plugin_root != stage:
            full_name = plugin_root.name
            if not owner:
                owner = plugin_root.name.split("-", 1)[0]
        elif owner:
            full_name = f"{owner}-{name}".replace(" ", "")
        else:
            full_name = name.replace(" ", "")

        guid = None
        for dll in plugin_root.rglob("*.dll"):
            meta = read_bepinex_plugin_metadata(str(dll))
            if meta.guid or meta.name:
                guid = meta.guid
                if not version:
                    version = meta.version
                if meta.name:
                    name = meta.name
                break

        item = {"full_name": full_name, "name": name}
        existing = (
            db.query(InstalledPackage).filter(InstalledPackage.full_name == full_name).first()
        )
        dest_dir, files_root, live_only = _resolve_dest(settings, item, existing)
        ensure_within(files_root, dest_dir)

        if live_only:
            to_overwrite: list[Path] = []
            for path in plugin_root.rglob("*"):
                if path.is_file():
                    to_overwrite.append(dest_dir / path.relative_to(plugin_root))
            _backup_files(to_overwrite, full_name, settings)
            copied = merge_tree_into(plugin_root, dest_dir)
            try:
                ensure_persistent_symlink(dest_dir.name, settings)
            except FileExistsError:
                logger.warning(
                    "Could not create plugins symlink for persistent package %s",
                    dest_dir.name,
                )
        else:
            if dest_dir.exists():
                backup_tmp = settings.backups_dir / "pre_install" / f"{full_name}-{stamp}"
                backup_tmp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(dest_dir, backup_tmp)
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            copied = _copy_tree(plugin_root, dest_dir)

        patchers = _detect_patchers(stage)
        if patchers and not live_only:
            for path in patchers.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(patchers).as_posix()
                    target = settings.patchers_dir / rel
                    ensure_within(settings.patchers_dir, target.parent)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)

        normalized = _normalize_copied(copied, files_root, dest_dir.name)

        source = "manual"
        matched = match_installed_to_remote(
            db, full_name=full_name, name=name, owner=owner, version=version
        )
        package_url = None
        icon_url = None
        if matched:
            source, info = matched
            full_name = info.full_name
            owner = info.owner or owner
            package_url = info.package_url
            icon_url = info.icon_url

        pkg = _finalize_package_record(
            db,
            existing=existing,
            source=source,
            full_name=full_name,
            name=name,
            owner=owner,
            version=version,
            install_path=str(dest_dir),
            description=description,
            dependencies=deps,
            normalized_files=normalized,
            plugin_guid=guid,
        )
        if package_url:
            pkg.package_url = package_url
        if icon_url:
            pkg.icon_url = icon_url

        log_activity(
            db,
            "import",
            package=full_name,
            source=source,
            result="ok",
            message=(
                f"Imported zip as {full_name}"
                + (f" {version}" if version else "")
                + (" (live-only merge)" if live_only else "")
            ),
        )
        set_setting(db, "restart_required", True)
        if not live_only:
            sync_config_tree_to_live(only_relative_paths=[f.relative_path for f in pkg.files] + [full_name])
        db.commit()
        db.refresh(pkg)
        return pkg
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def import_dll_file(
    db: Session,
    dll_path: Path,
    *,
    full_name_override: str | None = None,
) -> InstalledPackage:
    """Install a manually uploaded plugin DLL into the plugins root."""
    settings = get_settings()
    if dll_path.suffix.lower() != ".dll":
        raise ValueError("Only .dll files are accepted for DLL import")

    meta = read_bepinex_plugin_metadata(str(dll_path))
    name = meta.name or dll_path.stem
    version = meta.version
    guid = meta.guid
    full_name = (full_name_override or dll_path.stem).strip()
    if not full_name:
        raise ValueError("Package name is required")

    if "/" in full_name or "\\" in full_name or full_name in (".", ".."):
        raise ValueError("Invalid package name")

    item = {"full_name": full_name, "name": name}
    existing = (
        db.query(InstalledPackage)
        .filter(
            (InstalledPackage.install_path.contains(dll_path.stem))
            | (InstalledPackage.full_name == full_name)
        )
        .first()
    )
    # Prefer updating an existing persistent folder's DLL when matched
    live_folder = resolve_live_only_folder(settings, full_name=full_name, name=name)
    if live_folder is not None:
        dest = live_folder / f"{dll_path.stem}.dll"
        files_root = settings.persistent_dir
        live_only = True
    else:
        dest = settings.plugins_dir / f"{Path(full_name).name}.dll"
        if "-" in full_name and not full_name_override:
            dest = settings.plugins_dir / f"{dll_path.stem}.dll"
        elif full_name_override and not full_name_override.lower().endswith(".dll"):
            dest = settings.plugins_dir / f"{Path(full_name_override).name}.dll"
        files_root = settings.plugins_dir
        live_only = False

    ensure_within(files_root, dest)
    if dest.exists():
        _backup_files([dest], dest.name, settings)

    shutil.copy2(dll_path, dest)
    if live_only:
        try:
            ensure_persistent_symlink(live_folder.name if live_folder is not None else full_name, settings)
        except FileExistsError:
            pass
    rel = dest.relative_to(files_root).as_posix()

    existing = (
        db.query(InstalledPackage)
        .filter(
            (InstalledPackage.install_path == str(dest))
            | (InstalledPackage.full_name == full_name)
        )
        .first()
    )

    source = "manual"
    matched = match_installed_to_remote(
        db, full_name=full_name, name=name, version=version
    )
    package_url = icon_url = None
    if matched:
        source, info = matched
        full_name = info.full_name
        package_url = info.package_url
        icon_url = info.icon_url

    pkg = _finalize_package_record(
        db,
        existing=existing,
        source=source,
        full_name=full_name,
        name=name,
        owner=None,
        version=version,
        install_path=str(dest if not live_only else live_folder or dest),
        description=None,
        dependencies=[],
        normalized_files=[(rel, dest)],
        plugin_guid=guid,
    )
    if package_url:
        pkg.package_url = package_url
    if icon_url:
        pkg.icon_url = icon_url

    log_activity(
        db,
        "import",
        package=full_name,
        source=source,
        result="ok",
        message=f"Imported DLL {dest.name}" + (" (live-only)" if live_only else ""),
    )
    set_setting(db, "restart_required", True)
    if not live_only:
        sync_config_tree_to_live(only_relative_paths=[rel])
    db.commit()
    db.refresh(pkg)
    return pkg


def uninstall_package(db: Session, package_id: int) -> None:
    settings = get_settings()
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise ValueError("Package not found")

    dependents = []
    for other in db.query(InstalledPackage).filter(InstalledPackage.id != pkg.id).all():
        deps = json.loads(other.dependencies_json or "[]")
        for dep in deps:
            if pkg.full_name in dep or dep.startswith(pkg.full_name + "-"):
                dependents.append(other.full_name)
    if dependents:
        raise ValueError(
            f"Cannot uninstall {pkg.full_name}; required by: {', '.join(dependents)}"
        )

    live_only = path_is_under_live(pkg.install_path, settings)
    files_root = _files_root_for_path(pkg.install_path, settings)
    owned_rels = [owned.relative_path for owned in pkg.files]
    package_folder_name = Path(pkg.install_path).name

    for owned in list(pkg.files):
        target = files_root / owned.relative_path
        try:
            ensure_within(files_root, target if target.exists() else target.parent)
        except PathEscapeError:
            continue
        if target.is_file():
            target.unlink()
        elif target.is_dir() and not live_only:
            shutil.rmtree(target, ignore_errors=True)

    install = Path(pkg.install_path)
    if install.is_dir():
        if live_only:
            # Leave runtime dirs (map_data etc.); only remove empty leftover package dirs
            try:
                leftovers = [
                    p
                    for p in install.rglob("*")
                    if p.is_file()
                    and not any(part.lower() in _RUNTIME_DIR_NAMES for part in p.relative_to(install).parts)
                ]
                if not leftovers and not any(
                    (install / d).is_dir() for d in _RUNTIME_DIR_NAMES if (install / d).exists()
                ):
                    shutil.rmtree(install, ignore_errors=True)
                # If map_data remains, keep the folder
            except OSError:
                pass
            # Always remove the plugins symlink so bootstrap stops loading it
            link = settings.plugins_dir / package_folder_name
            try:
                if link.is_symlink() or link.exists():
                    link.unlink()
            except OSError as exc:
                logger.warning("Could not remove persistent plugins link %s: %s", link, exc)
        elif install.parent == settings.plugins_dir:
            shutil.rmtree(install, ignore_errors=True)

    name = pkg.full_name
    source = pkg.source
    db.delete(pkg)
    if not live_only:
        remove_paths_from_live(owned_rels)
        remove_paths_from_live([name])
    set_setting(db, "restart_required", True)
    log_activity(db, "uninstall", package=name, source=source, result="ok", message=f"Uninstalled {name}")
    db.commit()


def set_enabled(db: Session, package_id: int, enabled: bool) -> InstalledPackage:
    settings = get_settings()
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise ValueError("Package not found")

    install = Path(pkg.install_path)
    paths: list[Path] = []
    if install.is_file():
        paths = [install]
    elif install.is_dir():
        paths = list(install.rglob("*.dll")) + list(install.rglob("*.dll.disabled"))

    for path in paths:
        # Skip runtime trees
        try:
            if any(part.lower() in _RUNTIME_DIR_NAMES for part in path.parts):
                continue
        except Exception:
            pass
        if enabled and path.name.endswith(".dll.disabled"):
            new_path = path.with_name(path.name[: -len(".disabled")])
            path.rename(new_path)
        elif not enabled and path.suffix == ".dll" and not path.name.endswith(".dll.disabled"):
            new_path = path.with_name(path.name + ".disabled")
            path.rename(new_path)

    pkg.enabled = enabled
    set_setting(db, "restart_required", True)
    if not path_is_under_live(pkg.install_path, settings):
        sync_config_tree_to_live(only_relative_paths=[f.relative_path for f in pkg.files] + [pkg.full_name])
    log_activity(
        db,
        "enable" if enabled else "disable",
        package=pkg.full_name,
        source=pkg.source,
        result="ok",
    )
    db.commit()
    db.refresh(pkg)
    return pkg


def set_package_persistent(db: Session, package_id: int, persistent: bool) -> InstalledPackage:
    """Move package between plugins/ and .persistent/ with a plugins symlink."""
    from .live_sync import make_normal, make_persistent, path_is_persistent

    settings = get_settings()
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise ValueError("Package not found")

    folder_name = Path(pkg.install_path).name
    # Prefer the plugins entry name when install_path already points at .persistent
    plugins_entry = settings.plugins_dir / folder_name
    if plugins_entry.exists() or plugins_entry.is_symlink():
        folder_name = plugins_entry.name
    elif (settings.plugins_dir / pkg.full_name).exists() or (
        settings.plugins_dir / pkg.full_name
    ).is_symlink():
        folder_name = pkg.full_name

    already = path_is_persistent(pkg.install_path, settings) or path_is_persistent(
        settings.plugins_dir / folder_name, settings
    )
    if persistent:
        if already:
            dest = settings.persistent_dir / folder_name
        else:
            dest = make_persistent(folder_name, settings)
        pkg.install_path = str(dest)
        message = f"Marked {pkg.full_name} persistent (.persistent + plugins symlink)"
    else:
        if not already:
            dest = settings.plugins_dir / folder_name
        else:
            dest = make_normal(folder_name, settings)
        pkg.install_path = str(dest)
        message = f"Marked {pkg.full_name} normal (files in plugins/)"

    set_setting(db, "restart_required", True)
    log_activity(
        db,
        "persistent" if persistent else "normal",
        package=pkg.full_name,
        source=pkg.source,
        result="ok",
        message=message,
    )
    db.commit()
    db.refresh(pkg)
    return pkg


def link_package(
    db: Session,
    package_id: int,
    source: str,
    full_name: str,
    version: str | None = None,
) -> InstalledPackage:
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise ValueError("Package not found")
    if source not in ("thunderstore", "hexium"):
        raise ValueError("Source must be thunderstore or hexium")
    info = get_package(source, full_name)
    if info is None:
        raise ValueError(
            f"Remote package not found on {source}: {full_name}. Refresh package indexes first."
        )
    ver = version or pkg.version
    if ver and not any(v.version_number == ver for v in info.versions):
        pass
    if not ver and info.latest:
        ver = info.latest.version_number
    pkg.source = source
    pkg.full_name = full_name
    pkg.name = info.name
    pkg.owner = info.owner
    if ver:
        pkg.version = ver
    pkg.managed = True
    pkg.package_url = info.package_url
    pkg.icon_url = info.icon_url
    pkg.description = info.description
    matched = next((v for v in info.versions if v.version_number == ver), info.latest)
    if matched:
        pkg.dependencies_json = json.dumps(matched.dependencies)
    log_activity(db, "link", package=full_name, source=source, result="ok")
    db.commit()
    db.refresh(pkg)
    return pkg
