from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import BackupRecord, ConfigAssociation, InstalledPackage, OwnedFile
from .live_sync import make_normal, make_persistent, path_is_under_live
from .paths import ensure_within, safe_join
from .settings_service import log_activity, set_setting


def _package_snapshot(pkg: InstalledPackage) -> dict[str, Any]:
    return {
        "id": pkg.id,
        "source": pkg.source,
        "full_name": pkg.full_name,
        "name": pkg.name,
        "owner": pkg.owner,
        "version": pkg.version,
        "plugin_guid": pkg.plugin_guid,
        "install_path": pkg.install_path,
        "managed": pkg.managed,
        "enabled": pkg.enabled,
        "pinned": pkg.pinned,
        "auto_update": pkg.auto_update,
        "description": pkg.description,
        "icon_url": pkg.icon_url,
        "package_url": pkg.package_url,
        "dependencies": json.loads(pkg.dependencies_json or "[]"),
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size} for f in pkg.files
        ],
        "configs": [c.relative_path for c in pkg.configs],
    }


def create_backup(
    db: Session,
    *,
    label: str | None = None,
    reason: str = "manual",
    notes: str | None = None,
    package_ids: list[int] | None = None,
) -> BackupRecord:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    label = label or f"backup-{stamp}"
    backup_root = settings.backups_dir / stamp
    plugins_out = backup_root / "plugins"
    patchers_out = backup_root / "patchers"
    configs_out = backup_root / "configs"
    plugins_out.mkdir(parents=True)
    patchers_out.mkdir(parents=True)
    configs_out.mkdir(parents=True)

    query = db.query(InstalledPackage)
    if package_ids:
        query = query.filter(InstalledPackage.id.in_(package_ids))
    packages = query.all()
    inventory = []

    for pkg in packages:
        snap = _package_snapshot(pkg)
        inventory.append(snap)
        files_root = (
            settings.persistent_dir
            if path_is_under_live(pkg.install_path, settings)
            else settings.plugins_dir
        )
        for owned in pkg.files:
            relative = owned.relative_path.replace("\\", "/")
            if relative.startswith("../patchers/"):
                src_root = settings.patchers_dir
                src_rel = relative[len("../patchers/") :]
                dest_root = patchers_out
            else:
                src_root = files_root
                src_rel = relative
                dest_root = plugins_out
            src = safe_join(src_root, src_rel)
            if not src.is_file():
                continue
            try:
                ensure_within(src_root, src)
            except Exception:
                continue
            dest = safe_join(dest_root, src_rel)
            ensure_within(dest_root, dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        for cfg in pkg.configs:
            src = safe_join(settings.config_dir, cfg.relative_path)
            if src.is_file():
                ensure_within(settings.config_dir, src)
                dest = safe_join(configs_out, cfg.relative_path)
                ensure_within(configs_out, dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

    # Also copy all cfg files when full backup
    if not package_ids:
        for cfg in settings.config_dir.glob("*.cfg"):
            if cfg.is_file():
                shutil.copy2(cfg, configs_out / cfg.name)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "reason": reason,
        "packages": inventory,
    }
    (backup_root / "inventory.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    record = BackupRecord(
        label=label,
        reason=reason,
        path=str(backup_root),
        package_names_json=json.dumps([p.full_name for p in packages]),
        notes=notes,
        success=True,
    )
    db.add(record)
    log_activity(
        db,
        "backup",
        result="ok",
        message=f"Created backup {label}",
        details={"path": str(backup_root), "packages": len(packages)},
    )
    db.commit()
    db.refresh(record)
    return record


def list_backups(db: Session) -> list[BackupRecord]:
    return db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all()


def restore_backup(db: Session, backup_id: int) -> BackupRecord:
    """Restore package files and inventory from a backup without overwriting unrelated newer files."""
    settings = get_settings()
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise ValueError("Backup not found")
    backup_root = Path(record.path)
    inventory_path = backup_root / "inventory.json"
    if not inventory_path.is_file():
        raise ValueError("Backup inventory missing")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    for snap in inventory.get("packages") or []:
        install_hint = snap.get("install_path") or ""
        target_persistent = path_is_under_live(install_hint, settings)
        pkg = (
            db.query(InstalledPackage)
            .filter(
                InstalledPackage.source == (snap.get("source") or "local"),
                InstalledPackage.full_name == snap["full_name"],
            )
            .first()
        )
        # Move the current tree when the snapshot changes its storage class.
        if pkg is not None:
            current_persistent = path_is_under_live(pkg.install_path, settings)
            if target_persistent and not current_persistent:
                source_folder = settings.plugins_dir / Path(pkg.install_path).name
                if source_folder.is_dir():
                    make_persistent(source_folder.name, settings)
            elif current_persistent and not target_persistent:
                make_normal(Path(pkg.install_path).name, settings)

        files_root = (
            settings.persistent_dir
            if target_persistent
            else settings.plugins_dir
        )
        for fmeta in snap.get("files") or []:
            rel = str(fmeta["relative_path"]).replace("\\", "/")
            if rel.startswith("../patchers/"):
                target_root = settings.patchers_dir
                owned_rel = rel[len("../patchers/") :]
                # Older backups used plugins/../patchers/, which resolves here.
                src = safe_join(backup_root / "patchers", owned_rel)
            else:
                target_root = files_root
                owned_rel = rel
                src = safe_join(backup_root / "plugins", rel)
            if not src.is_file():
                continue
            dest = safe_join(target_root, owned_rel)
            ensure_within(target_root, dest)
            # Skip if dest exists, differs from backup, and was modified after backup
            if dest.is_file() and record.created_at:
                dest_mtime = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
                created = record.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                backup_sha = fmeta.get("sha256")
                import hashlib

                def sha(p: Path) -> str:
                    h = hashlib.sha256()
                    with p.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(65536), b""):
                            h.update(chunk)
                    return h.hexdigest()

                current_sha = sha(dest)
                if backup_sha and current_sha != backup_sha and dest_mtime > created:
                    # Unrelated change after backup — do not overwrite
                    continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            ensure_within(target_root, dest.parent)
            shutil.copy2(src, dest)

        for cfg_name in snap.get("configs") or []:
            src = safe_join(backup_root / "configs", cfg_name)
            if src.is_file():
                dest = safe_join(settings.config_dir, cfg_name)
                ensure_within(settings.config_dir, dest)
                if dest.is_file() and record.created_at:
                    dest_mtime = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
                    created = record.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if dest_mtime > created:
                        continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        # Restore DB row
        if pkg is None:
            pkg = InstalledPackage(
                source=snap.get("source") or "local",
                full_name=snap["full_name"],
                name=snap.get("name") or snap["full_name"],
                owner=snap.get("owner"),
                version=snap.get("version"),
                plugin_guid=snap.get("plugin_guid"),
                install_path=snap.get("install_path") or str(settings.plugins_dir / snap["full_name"]),
                managed=bool(snap.get("managed")),
                enabled=bool(snap.get("enabled", True)),
                pinned=bool(snap.get("pinned")),
                auto_update=bool(snap.get("auto_update", True)),
                description=snap.get("description"),
                icon_url=snap.get("icon_url"),
                package_url=snap.get("package_url"),
                dependencies_json=json.dumps(snap.get("dependencies") or []),
            )
            db.add(pkg)
            db.flush()
        else:
            pkg.source = snap.get("source") or pkg.source
            pkg.full_name = snap["full_name"]
            pkg.name = snap.get("name") or snap["full_name"]
            pkg.owner = snap.get("owner")
            pkg.version = snap.get("version")
            pkg.plugin_guid = snap.get("plugin_guid")
            pkg.install_path = install_hint or str(settings.plugins_dir / snap["full_name"])
            pkg.managed = bool(snap.get("managed"))
            pkg.enabled = bool(snap.get("enabled", True))
            pkg.pinned = bool(snap.get("pinned"))
            pkg.auto_update = bool(snap.get("auto_update", True))
            pkg.description = snap.get("description")
            pkg.icon_url = snap.get("icon_url")
            pkg.package_url = snap.get("package_url")
            pkg.dependencies_json = json.dumps(snap.get("dependencies") or [])
            pkg.files.clear()

        for fmeta in snap.get("files") or []:
            pkg.files.append(
                OwnedFile(
                    relative_path=fmeta["relative_path"],
                    sha256=fmeta.get("sha256"),
                    size=fmeta.get("size"),
                )
            )
        pkg.configs.clear()
        for cfg_name in snap.get("configs") or []:
            pkg.configs.append(ConfigAssociation(relative_path=cfg_name))

    log_activity(db, "rollback", result="ok", message=f"Restored backup {record.label}")
    set_setting(db, "restart_required", True)
    db.commit()
    db.refresh(record)
    return record
