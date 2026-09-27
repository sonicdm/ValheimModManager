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

from ..config import get_settings
from ..models import ConfigAssociation, InstalledPackage, OwnedFile
from .dll_meta import read_bepinex_plugin_metadata
from .paths import PathEscapeError, ensure_within, validate_archive_member
from .settings_service import log_activity, set_setting
from .packages import get_package, match_installed_to_remote, resolve_dependencies

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def download_package(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
    return dest


def _safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                # create directory safely
                if name:
                    target_dir = validate_archive_member(name.rstrip("/"), dest)
                    target_dir.mkdir(parents=True, exist_ok=True)
                continue
            target = validate_archive_member(name, dest)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            # Reject symlink-like external attrs on unix (bit 0o120000)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PathEscapeError(f"Refusing to extract symlink: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(target)
    return extracted


def _detect_plugin_root(stage: Path) -> Path:
    """Find the directory that should be copied into BepInEx/plugins."""
    # Prefer a folder containing dlls or manifest.json
    if (stage / "manifest.json").is_file() or any(stage.glob("*.dll")):
        return stage
    # plugins/ nested
    if (stage / "plugins").is_dir():
        return stage / "plugins"
    if (stage / "BepInEx" / "plugins").is_dir():
        return stage / "BepInEx" / "plugins"
    # Single subdirectory
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
    """Copy files from src into dest; return list of (relative_to_dest_parent, full path)."""
    copied: list[tuple[str, Path]] = []
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        target = dest / src.name
        shutil.copy2(src, target)
        copied.append((src.name, target))
        return copied
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        ensure_within(dest, target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append((rel, target))
    return copied


def preview_install(db: Session, source: str, full_name: str, version: str | None = None) -> dict[str, Any]:
    plan = resolve_dependencies(db, source, full_name, version)
    conflicts: list[str] = []
    warnings: list[str] = []
    settings = get_settings()
    for item in plan:
        folder = settings.plugins_dir / item["full_name"]
        existing = (
            db.query(InstalledPackage)
            .filter(InstalledPackage.full_name == item["full_name"])
            .first()
        )
        if existing and existing.version and existing.version != item["version"]:
            warnings.append(
                f"{item['full_name']} will be upgraded from {existing.version} to {item['version']}"
            )
        if folder.exists() and existing is None:
            conflicts.append(f"Directory already exists without ownership record: {folder.name}")
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
            .filter(InstalledPackage.full_name == item["full_name"])
            .first()
        )
        # Skip if already at target version and managed
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
            plugin_root = _detect_plugin_root(stage)
            dest_dir = settings.plugins_dir / item["full_name"]
            # Backup existing destination into data backups staging
            if dest_dir.exists():
                backup_tmp = settings.backups_dir / "pre_install" / f"{item['full_name']}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                backup_tmp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(dest_dir, backup_tmp)
                shutil.rmtree(dest_dir)

            dest_dir.mkdir(parents=True, exist_ok=True)
            ensure_within(settings.plugins_dir, dest_dir)

            # If plugin_root is stage itself with mixed content, copy contents into Team-Mod folder
            if plugin_root == stage or plugin_root.name == "plugins":
                # If plugins/ contains Team-Mod subfolders, prefer matching folder
                match = None
                if plugin_root.name == "plugins":
                    for child in plugin_root.iterdir():
                        if child.is_dir() and (
                            child.name == item["full_name"] or child.name == item["name"]
                        ):
                            match = child
                            break
                    if match is None and len(list(plugin_root.iterdir())) == 1:
                        only = next(plugin_root.iterdir())
                        if only.is_dir():
                            match = only
                    if match:
                        plugin_root = match
                    else:
                        # copy all plugin files into dest
                        pass

                if (plugin_root / "manifest.json").is_file() or any(plugin_root.glob("*.dll")):
                    copied = _copy_tree(plugin_root, dest_dir)
                else:
                    copied = _copy_tree(plugin_root, dest_dir)
            else:
                copied = _copy_tree(plugin_root, dest_dir)

            # Patchers
            patchers = _detect_patchers(stage)
            patcher_files: list[tuple[str, Path]] = []
            if patchers:
                for path in patchers.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(patchers).as_posix()
                        target = settings.patchers_dir / rel
                        ensure_within(settings.patchers_dir, target.parent)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
                        patcher_files.append((f"../patchers/{rel}", target))

            owned_rels: list[str] = []
            for rel, full in copied:
                owned_rels.append(f"{item['full_name']}/{rel}" if not rel.startswith(item["full_name"]) else rel)
                # Normalize: paths relative to plugins_dir
            normalized: list[tuple[str, Path]] = []
            for rel, full in copied:
                try:
                    normalized.append((full.relative_to(settings.plugins_dir).as_posix(), full))
                except ValueError:
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
                message=f"Installed {item['full_name']} {item['version']}",
            )
            installed.append(existing)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    set_setting(db, "restart_required", True)
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


def install_from_zip_file(
    db: Session,
    zip_path: Path,
    *,
    full_name_override: str | None = None,
) -> InstalledPackage:
    """Install a manually uploaded Thunderstore-style (or plain) zip into plugins/."""
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    stage = settings.staging_dir / f"manual-import-{stamp}"
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

        # Prefer Team-Mod folder naming when present
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

        # DLL metadata
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

        dest_dir = settings.plugins_dir / full_name
        ensure_within(settings.plugins_dir, dest_dir)
        if dest_dir.exists():
            backup_tmp = (
                settings.backups_dir
                / "pre_install"
                / f"{full_name}-{stamp}"
            )
            backup_tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dest_dir, backup_tmp)
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        copied = _copy_tree(plugin_root, dest_dir)
        patchers = _detect_patchers(stage)
        if patchers:
            for path in patchers.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(patchers).as_posix()
                    target = settings.patchers_dir / rel
                    ensure_within(settings.patchers_dir, target.parent)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)

        normalized: list[tuple[str, Path]] = []
        for rel, full in copied:
            try:
                normalized.append((full.relative_to(settings.plugins_dir).as_posix(), full))
            except ValueError:
                normalized.append((f"{full_name}/{rel}", full))

        existing = (
            db.query(InstalledPackage).filter(InstalledPackage.full_name == full_name).first()
        )
        source = "manual"
        # Auto-link to a store if we can
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
            message=f"Imported zip as {full_name}" + (f" {version}" if version else ""),
        )
        set_setting(db, "restart_required", True)
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

    # Disallow path tricks in the chosen name
    if "/" in full_name or "\\" in full_name or full_name in (".", ".."):
        raise ValueError("Invalid package name")

    dest = settings.plugins_dir / f"{Path(full_name).name}.dll"
    # If they gave Team-Mod style, keep as loose dll named after stem of last segment
    if "-" in full_name and not full_name_override:
        dest = settings.plugins_dir / f"{dll_path.stem}.dll"
    elif full_name_override and not full_name_override.lower().endswith(".dll"):
        # Store as folder? For single DLL keep loose file named after override stem
        dest = settings.plugins_dir / f"{Path(full_name_override).name}.dll"

    ensure_within(settings.plugins_dir, dest)
    if dest.exists():
        backup_tmp = (
            settings.backups_dir
            / "pre_install"
            / f"{dest.name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        backup_tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, backup_tmp)

    shutil.copy2(dll_path, dest)
    rel = dest.relative_to(settings.plugins_dir).as_posix()

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
        install_path=str(dest),
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
        message=f"Imported DLL {dest.name}",
    )
    set_setting(db, "restart_required", True)
    db.commit()
    db.refresh(pkg)
    return pkg


def uninstall_package(db: Session, package_id: int) -> None:
    settings = get_settings()
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise ValueError("Package not found")

    # Shared dependency check
    dependents = []
    for other in db.query(InstalledPackage).filter(InstalledPackage.id != pkg.id).all():
        deps = json.loads(other.dependencies_json or "[]")
        for dep in deps:
            if pkg.full_name in dep or dep.startswith(pkg.full_name + "-"):
                dependents.append(other.full_name)
    # Only block if this package is a dependency and still required —
    # actually for uninstall of the selected package we remove it; we refuse
    # to auto-remove shared deps. If user uninstalls a leaf package, OK.
    # If other packages depend on THIS package, warn but allow with cascade check:
    if dependents:
        raise ValueError(
            f"Cannot uninstall {pkg.full_name}; required by: {', '.join(dependents)}"
        )

    # Delete owned files only
    for owned in list(pkg.files):
        target = settings.plugins_dir / owned.relative_path
        try:
            ensure_within(settings.plugins_dir, target if target.exists() else target.parent)
        except PathEscapeError:
            continue
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=True)

    # Remove empty package directory
    install = Path(pkg.install_path)
    if install.is_dir() and install.parent == settings.plugins_dir:
        try:
            if not any(install.rglob("*")):
                shutil.rmtree(install, ignore_errors=True)
            else:
                # remove dir if only empty leftovers
                shutil.rmtree(install, ignore_errors=True)
        except OSError:
            pass

    name = pkg.full_name
    source = pkg.source
    db.delete(pkg)
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
        if enabled and path.name.endswith(".dll.disabled"):
            new_path = path.with_name(path.name[: -len(".disabled")])
            path.rename(new_path)
        elif not enabled and path.suffix == ".dll" and not path.name.endswith(".dll.disabled"):
            new_path = path.with_name(path.name + ".disabled")
            path.rename(new_path)

    pkg.enabled = enabled
    set_setting(db, "restart_required", True)
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
    # Keep the installed on-disk version unless caller overrides
    ver = version or pkg.version
    if ver and not any(v.version_number == ver for v in info.versions):
        # Still allow link; version may be a fork / slightly different
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
