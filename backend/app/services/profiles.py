"""Named mod profiles + Gale/r2modman import/export/activate."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import InstalledPackage, ModProfile
from . import backup as backup_service
from . import installer
from . import r2_profile
from .live_sync import path_is_under_live
from .packages import get_cached_packages, get_package
from .r2_profile import ExportMode, ParsedProfile, ProfileModSpec
from .settings_service import get_setting, log_activity, set_setting

logger = logging.getLogger(__name__)

BEPINEX_PACK = "denikson-BepInExPack_Valheim"


def profiles_dir() -> Path:
    path = get_settings().data_dir / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_config_dir(profile_id: int) -> Path:
    path = profiles_dir() / str(profile_id) / "configs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mods_from_json(raw: str | None) -> list[ProfileModSpec]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out: list[ProfileModSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append(
            ProfileModSpec(
                full_name=str(item.get("full_name") or ""),
                version=str(item.get("version") or "0.0.0"),
                enabled=bool(item.get("enabled", True)),
                source=str(item.get("source") or "thunderstore"),
            )
        )
    return [m for m in out if m.full_name]


def _mods_to_json(mods: list[ProfileModSpec]) -> str:
    return json.dumps(
        [
            {
                "source": m.source,
                "full_name": m.full_name,
                "version": m.version,
                "enabled": m.enabled,
            }
            for m in mods
        ]
    )


def profile_to_dict(row: ModProfile, *, active_id: int | None = None) -> dict[str, Any]:
    mods = _mods_from_json(row.mods_json)
    return {
        "id": row.id,
        "name": row.name,
        "mods": [
            {
                "source": m.source,
                "full_name": m.full_name,
                "version": m.version,
                "enabled": m.enabled,
            }
            for m in mods
        ],
        "include_configs": bool(row.include_configs),
        "community": row.community,
        "style": row.style,
        "mod_count": len(mods),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "is_active": active_id is not None and int(active_id) == row.id,
    }


def list_profiles(db: Session) -> list[dict[str, Any]]:
    active = get_setting(db, "active_profile_id", None)
    active_id = int(active) if active not in (None, "", False) else None
    rows = db.query(ModProfile).order_by(ModProfile.name.asc()).all()
    return [profile_to_dict(r, active_id=active_id) for r in rows]


def get_profile(db: Session, profile_id: int) -> ModProfile:
    row = db.get(ModProfile, profile_id)
    if row is None:
        raise ValueError("Profile not found")
    return row


def snapshot_current_mods(db: Session) -> list[ProfileModSpec]:
    mods: list[ProfileModSpec] = []
    for pkg in db.query(InstalledPackage).filter(InstalledPackage.managed.is_(True)).all():
        if pkg.source not in ("thunderstore", "hexium"):
            continue
        if pkg.full_name == BEPINEX_PACK:
            continue
        mods.append(
            ProfileModSpec(
                full_name=pkg.full_name,
                version=pkg.version or "0.0.0",
                enabled=bool(pkg.enabled),
                source=pkg.source,
            )
        )
    return mods


def _store_configs(profile_id: int, files: dict[str, bytes]) -> None:
    dest_root = profile_config_dir(profile_id)
    if dest_root.exists():
        shutil.rmtree(dest_root, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        rel = rel.replace("\\", "/").strip("/")
        if not rel or rel.endswith("/"):
            continue
        target = dest_root / rel
        # Zip dir entries can previously have been written as empty files — clear parents.
        for parent in [target.parent, *target.parents]:
            if parent == dest_root or not str(parent).startswith(str(dest_root)):
                continue
            if parent.is_file():
                parent.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _load_stored_configs(profile_id: int) -> dict[str, bytes]:
    root = profile_config_dir(profile_id)
    out: dict[str, bytes] = {}
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def create_from_current(
    db: Session, *, name: str, include_configs: bool = True
) -> ModProfile:
    name = name.strip()
    if not name:
        raise ValueError("Profile name required")
    if db.query(ModProfile).filter(ModProfile.name == name).first():
        raise ValueError(f"Profile already exists: {name}")
    mods = snapshot_current_mods(db)
    row = ModProfile(
        name=name,
        mods_json=_mods_to_json(mods),
        include_configs=include_configs,
        community="valheim",
        style="gale",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if include_configs:
        files = r2_profile.collect_bepinex_configs(get_settings().bepinex_root)
        _store_configs(row.id, files)
    log_activity(db, "profile_create", result="ok", message=f"Created profile {name} ({len(mods)} mods)")
    db.commit()
    return row


def create_from_parsed(
    db: Session,
    parsed: ParsedProfile,
    *,
    name: str | None = None,
    include_configs: bool = True,
) -> ModProfile:
    pname = (name or parsed.name or "Imported").strip()
    if db.query(ModProfile).filter(ModProfile.name == pname).first():
        base = pname
        i = 2
        while db.query(ModProfile).filter(ModProfile.name == f"{base} ({i})").first():
            i += 1
        pname = f"{base} ({i})"
    mods = [m for m in parsed.mods if m.full_name != BEPINEX_PACK]
    row = ModProfile(
        name=pname,
        mods_json=_mods_to_json(mods),
        include_configs=include_configs,
        community=parsed.community or "valheim",
        style=parsed.style,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if include_configs and parsed.config_files:
        _store_configs(row.id, parsed.config_files)
    log_activity(
        db,
        "profile_import",
        result="ok",
        message=f"Imported profile {pname} ({len(mods)} mods, style={parsed.style})",
    )
    db.commit()
    return row


def update_profile(
    db: Session,
    profile_id: int,
    *,
    name: str | None = None,
    include_configs: bool | None = None,
    refresh_from_current: bool = False,
) -> ModProfile:
    row = get_profile(db, profile_id)
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Profile name required")
        clash = db.query(ModProfile).filter(ModProfile.name == name, ModProfile.id != row.id).first()
        if clash:
            raise ValueError(f"Profile already exists: {name}")
        row.name = name
    if include_configs is not None:
        row.include_configs = include_configs
    if refresh_from_current:
        row.mods_json = _mods_to_json(snapshot_current_mods(db))
        row.style = "gale"
        if row.include_configs:
            files = r2_profile.collect_bepinex_configs(get_settings().bepinex_root)
            _store_configs(row.id, files)
    db.commit()
    db.refresh(row)
    return row


def delete_profile(db: Session, profile_id: int) -> None:
    row = get_profile(db, profile_id)
    disk = profiles_dir() / str(profile_id)
    db.delete(row)
    active = get_setting(db, "active_profile_id", None)
    if active and str(active) == str(profile_id):
        set_setting(db, "active_profile_id", "")
    db.commit()
    if disk.exists():
        shutil.rmtree(disk, ignore_errors=True)


def _thunderstore_has_version(full_name: str, version: str) -> bool:
    pkg = get_package("thunderstore", full_name)
    if pkg is None:
        return False
    return any(v.version_number == version for v in pkg.versions)


def _resolve_source(mod: ProfileModSpec) -> ProfileModSpec:
    """If source missing/wrong, prefer exact version on declared source then fallback."""
    src = mod.source if mod.source in ("thunderstore", "hexium") else "thunderstore"
    pkg = get_package(src, mod.full_name)
    if pkg and any(v.version_number == mod.version for v in pkg.versions):
        return ProfileModSpec(mod.full_name, mod.version, mod.enabled, src)
    other = "hexium" if src == "thunderstore" else "thunderstore"
    pkg2 = get_package(other, mod.full_name)
    if pkg2 and any(v.version_number == mod.version for v in pkg2.versions):
        return ProfileModSpec(mod.full_name, mod.version, mod.enabled, other)
    # Keep declared source; installer will error if missing
    return ProfileModSpec(mod.full_name, mod.version, mod.enabled, src)


def preview_thunderstore_export(mods: list[ProfileModSpec]) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    remapped: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for m in mods:
        if m.full_name == BEPINEX_PACK:
            continue
        ts = get_package("thunderstore", m.full_name)
        if ts and any(v.version_number == m.version for v in ts.versions):
            kept.append(
                {"source": "thunderstore", "full_name": m.full_name, "version": m.version, "enabled": m.enabled}
            )
            continue
        if ts and ts.latest:
            remapped.append(
                {
                    "full_name": m.full_name,
                    "from_version": m.version,
                    "to_version": ts.latest.version_number,
                    "from_source": m.source,
                    "enabled": m.enabled,
                }
            )
            continue
        dropped.append(
            {"source": m.source, "full_name": m.full_name, "version": m.version, "enabled": m.enabled}
        )
    return {"kept": kept, "remapped": remapped, "dropped": dropped}


def mods_for_export(mods: list[ProfileModSpec], mode: ExportMode) -> list[ProfileModSpec]:
    if mode == "full":
        return [m for m in mods if m.full_name != BEPINEX_PACK]
    preview = preview_thunderstore_export(mods)
    out: list[ProfileModSpec] = []
    for k in preview["kept"]:
        out.append(ProfileModSpec(k["full_name"], k["version"], k["enabled"], "thunderstore"))
    for r in preview["remapped"]:
        out.append(
            ProfileModSpec(r["full_name"], r["to_version"], r["enabled"], "thunderstore")
        )
    return out


def build_export_zip(
    db: Session,
    *,
    profile_id: int | None = None,
    mode: ExportMode = "full",
    include_configs: bool | None = None,
) -> tuple[bytes, str]:
    settings = get_settings()
    if profile_id is not None:
        row = get_profile(db, profile_id)
        name = row.name
        mods = _mods_from_json(row.mods_json)
        use_cfg = row.include_configs if include_configs is None else include_configs
        configs = _load_stored_configs(row.id) if use_cfg else {}
        community = row.community or "valheim"
    else:
        name = "Current"
        mods = snapshot_current_mods(db)
        use_cfg = True if include_configs is None else include_configs
        configs = r2_profile.collect_bepinex_configs(settings.bepinex_root) if use_cfg else {}
        community = "valheim"
    export_mods = mods_for_export(mods, mode)
    data = r2_profile.build_r2z_bytes(
        name=name, mods=export_mods, config_files=configs, mode=mode, community=community
    )
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "profile"
    return data, f"{safe}.r2z"


async def export_share_code(
    db: Session,
    *,
    profile_id: int | None = None,
    mode: ExportMode = "full",
    include_configs: bool | None = None,
) -> dict[str, str]:
    zip_bytes, _ = build_export_zip(
        db, profile_id=profile_id, mode=mode, include_configs=include_configs
    )
    if mode == "thunderstore":
        backend = "thunderstore"
    else:
        # Prefer Thunderstore so the code is a real UUID (r2modman / Gale legacy-code import).
        # Hexium create returns opaque 32-char hex that Gale Sync paste rejects.
        # Zip content can still list Hexium-sourced mods; only the paste host changes.
        backend = "thunderstore"
        mods = (
            _mods_from_json(get_profile(db, profile_id).mods_json)
            if profile_id is not None
            else snapshot_current_mods(db)
        )
        if r2_profile.has_hexium_exclusive(mods, thunderstore_has_version=_thunderstore_has_version):
            # Still upload to Thunderstore first; fall back to Hexium only if TS rejects.
            try:
                code = await r2_profile.upload_share_code(zip_bytes, backend="thunderstore")
                log_activity(
                    db,
                    "profile_export_code",
                    result="ok",
                    message="Exported profile code via thunderstore (Hexium-exclusive mods in zip)",
                    details={"code": code, "backend": "thunderstore", "mode": mode},
                )
                db.commit()
                return {"code": code, "backend": "thunderstore", "mode": mode}
            except ValueError:
                backend = "hexium"
    code = await r2_profile.upload_share_code(zip_bytes, backend=backend)
    log_activity(
        db,
        "profile_export_code",
        result="ok",
        message=f"Exported profile code via {backend}",
        details={"code": code, "backend": backend, "mode": mode},
    )
    db.commit()
    return {"code": code, "backend": backend, "mode": mode}


async def import_profile(
    db: Session,
    *,
    code: str | None = None,
    file_bytes: bytes | None = None,
    name: str | None = None,
    include_configs: bool = True,
) -> ModProfile:
    raw = await r2_profile.resolve_import_bytes(code=code, file_bytes=file_bytes)
    parsed = r2_profile.parse_r2z_bytes(raw)
    return create_from_parsed(db, parsed, name=name, include_configs=include_configs)


async def activate_profile(
    db: Session,
    profile_id: int,
    *,
    include_configs: bool | None = None,
) -> dict[str, Any]:
    row = get_profile(db, profile_id)
    desired = [_resolve_source(m) for m in _mods_from_json(row.mods_json) if m.full_name != BEPINEX_PACK]
    desired_keys = {(m.source, m.full_name) for m in desired}
    use_cfg = row.include_configs if include_configs is None else include_configs

    backup_service.create_backup(
        db, label=f"pre-profile-{row.name}", reason="pre-profile-activate"
    )

    result: dict[str, Any] = {
        "profile_id": row.id,
        "installed": [],
        "updated": [],
        "removed": [],
        "kept_persistent": [],
        "disabled": [],
        "configs_written": 0,
        "errors": [],
        "restart_required": True,
    }

    settings = get_settings()
    current = [
        p
        for p in db.query(InstalledPackage).filter(InstalledPackage.managed.is_(True)).all()
        if p.source in ("thunderstore", "hexium") and p.full_name != BEPINEX_PACK
    ]

    # Remove extras (non-persistent)
    for pkg in current:
        key = (pkg.source, pkg.full_name)
        if key in desired_keys:
            continue
        if path_is_under_live(pkg.install_path, settings):
            result["kept_persistent"].append(pkg.full_name)
            continue
        try:
            installer.uninstall_package(db, pkg.id, force=True)
            result["removed"].append(pkg.full_name)
        except Exception as exc:
            result["errors"].append(f"remove {pkg.full_name}: {exc}")
            logger.exception("profile activate uninstall failed")

    # Install / update
    for mod in desired:
        existing = (
            db.query(InstalledPackage)
            .filter(
                InstalledPackage.source == mod.source,
                InstalledPackage.full_name == mod.full_name,
            )
            .first()
        )
        try:
            if existing is None or existing.version != mod.version:
                await installer.install_packages(db, mod.source, mod.full_name, mod.version)
                if existing is None:
                    result["installed"].append(mod.full_name)
                else:
                    result["updated"].append(mod.full_name)
                existing = (
                    db.query(InstalledPackage)
                    .filter(
                        InstalledPackage.source == mod.source,
                        InstalledPackage.full_name == mod.full_name,
                    )
                    .first()
                )
            if existing is not None and bool(existing.enabled) != bool(mod.enabled):
                installer.set_enabled(db, existing.id, mod.enabled)
                if not mod.enabled:
                    result["disabled"].append(mod.full_name)
        except Exception as exc:
            result["errors"].append(f"{mod.full_name}@{mod.version}: {exc}")
            logger.exception("profile activate install failed")

    if use_cfg:
        files = _load_stored_configs(row.id)
        written = r2_profile.apply_configs_to_bepinex(settings.bepinex_root, files)
        result["configs_written"] = len(written)

    set_setting(db, "active_profile_id", str(row.id))
    set_setting(db, "restart_required", True)
    log_activity(
        db,
        "profile_activate",
        result="ok" if not result["errors"] else "error",
        message=f"Activated profile {row.name}",
        details=result,
    )
    db.commit()
    return result
