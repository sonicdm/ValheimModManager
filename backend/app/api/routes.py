from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from ..auth.security import (
    check_rate_limit,
    clear_login_attempts,
    clear_session_cookie,
    create_csrf_token,
    ensure_admin_user,
    get_current_user,
    hash_password,
    record_login_attempt,
    require_csrf,
    set_session_cookie,
    verify_password,
)
from ..config import get_settings
from ..database import get_db
from ..models import ActivityEvent, AdminUser, InstalledPackage, PendingUpdate
from ..schemas import (
    ActivityOut,
    ApplyUpdatesRequest,
    AuthStatus,
    BackupOut,
    ChangePasswordRequest,
    ConfigContentOut,
    ConfigFileOut,
    ConfigSaveRequest,
    CreateBackupRequest,
    DashboardStats,
    HealthOut,
    InstallPreview,
    InstallRequest,
    InstalledPackageOut,
    LinkPackageRequest,
    LoginRequest,
    PackageActionRequest,
    PackageOut,
    PackageVersionOut,
    PendingUpdateOut,
    ScanResult,
    SettingUpdate,
    SettingsResponse,
)
from .. import __version__
from ..services import backup as backup_service
from ..services import config_editor
from ..services import installer
from ..services import packages as package_service
from ..services import scanner
from ..services import supervisor
from ..services import updates as update_service
from ..services.settings_service import get_all_settings, get_setting, log_activity, update_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _pkg_out(pkg: InstalledPackage, pending_map: dict[str, str] | None = None) -> InstalledPackageOut:
    pending_map = pending_map or {}
    deps = []
    try:
        deps = json.loads(pkg.dependencies_json or "[]")
    except json.JSONDecodeError:
        deps = []
    from ..config import get_settings
    from ..services.live_sync import path_is_under_live

    settings = get_settings()
    return InstalledPackageOut(
        id=pkg.id,
        source=pkg.source,
        full_name=pkg.full_name,
        name=pkg.name,
        owner=pkg.owner,
        version=pkg.version,
        plugin_guid=pkg.plugin_guid,
        install_path=pkg.install_path,
        managed=pkg.managed,
        enabled=pkg.enabled,
        pinned=pkg.pinned,
        auto_update=pkg.auto_update,
        description=pkg.description,
        icon_url=pkg.icon_url,
        package_url=pkg.package_url,
        dependencies=deps,
        config_files=[c.relative_path for c in pkg.configs],
        owned_files=[f.relative_path for f in pkg.files],
        update_available=pending_map.get(f"{pkg.source}:{pkg.full_name}"),
        last_scanned_at=pkg.last_scanned_at,
        live_only=path_is_under_live(pkg.install_path, settings),
    )


def _auth_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AdminUser:
    user = get_current_user(request, db)
    require_csrf(request, user)
    return user


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        status="ok",
        version=__version__,
        bepinex_root=str(settings.bepinex_root),
        plugins_exist=settings.plugins_dir.is_dir(),
    )


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthStatus:
    ensure_admin_user(db)
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return AuthStatus(authenticated=False)
    from ..auth.security import read_session_token

    username = read_session_token(token)
    if not username:
        return AuthStatus(authenticated=False)
    return AuthStatus(authenticated=True, username=username, csrf_token=create_csrf_token(username))


@router.post("/auth/login", response_model=AuthStatus)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthStatus:
    client = request.client.host if request.client else "unknown"
    check_rate_limit(client)
    user = ensure_admin_user(db)
    if body.username != user.username or not verify_password(user.password_hash, body.password):
        record_login_attempt(client)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    clear_login_attempts(client)
    csrf = set_session_cookie(response, user.username)
    log_activity(db, "login", result="ok", message=f"User {user.username} logged in")
    return AuthStatus(authenticated=True, username=user.username, csrf_token=csrf)


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, str]:
    clear_session_cookie(response)
    return {"status": "ok"}


@router.post("/auth/password")
def change_password(
    body: ChangePasswordRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"status": "ok"}


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DashboardStats:
    packages = db.query(InstalledPackage).all()
    pending = db.query(PendingUpdate).filter(PendingUpdate.status.in_(["queued", "deferred"])).all()
    activity = db.query(ActivityEvent).order_by(ActivityEvent.timestamp.desc()).limit(20).all()
    last_scan = get_setting(db, "last_scan_at")
    last_check = get_setting(db, "last_update_check_at")
    jobs = update_service.job_status_snapshot()
    # Do not call Supervisor here — DNS/connect stalls made the whole page feel hung.
    # Frontend loads /api/server/status separately.
    return DashboardStats(
        installed_count=len(packages),
        unmanaged_count=sum(1 for p in packages if not p.managed),
        updates_count=len(pending),
        disabled_count=sum(1 for p in packages if not p.enabled),
        last_scan_at=datetime.fromisoformat(last_scan) if last_scan else None,
        last_update_check_at=datetime.fromisoformat(last_check) if last_check else None,
        maintenance_enabled=bool(get_setting(db, "maintenance_enabled", True)),
        next_maintenance_window=update_service.next_maintenance_window(db),
        supervisor_status=None,
        supervisor_configured=supervisor.supervisor_configured(db),
        restart_required=bool(get_setting(db, "restart_required", False)),
        scan_in_progress=bool(jobs.get("scan_in_progress")),
        package_refresh_in_progress=bool(jobs.get("package_refresh_in_progress")),
        recent_activity=[ActivityOut.model_validate(a) for a in activity],
        pending_updates=[PendingUpdateOut.model_validate(p) for p in pending],
    )


@router.get("/settings", response_model=SettingsResponse)
def read_settings(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SettingsResponse:
    return SettingsResponse(values=get_all_settings(db))


@router.put("/settings", response_model=SettingsResponse)
def write_settings(
    body: SettingUpdate,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SettingsResponse:
    if "timezone" in body.values:
        tz_name = str(body.values.get("timezone") or "").strip() or "UTC"
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz_name)
        except Exception as exc:
            raise HTTPException(400, f"Invalid timezone: {tz_name}") from exc
        body.values["timezone"] = tz_name
    values = update_settings(db, body.values)
    log_activity(db, "settings", result="ok", message="Settings updated")
    # Refresh cron/intervals so timezone + maintenance hour take effect immediately.
    try:
        update_service.reschedule_scheduler()
    except Exception:
        log_activity(db, "settings", result="error", message="Settings saved but scheduler refresh failed")
    return SettingsResponse(values=values)


@router.post("/scan", response_model=ScanResult)
def scan(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScanResult:
    from ..services.job_status import scan_running

    with scan_running():
        packages, pruned = scanner.persist_scan(db, reason="manual")
    pending = {
        f"{p.source}:{p.full_name}": p.target_version
        for p in db.query(PendingUpdate).filter(PendingUpdate.status.in_(["queued", "deferred"])).all()
    }
    outs = [_pkg_out(p, pending) for p in packages]
    return ScanResult(
        scanned=len(outs),
        managed=sum(1 for p in outs if p.managed),
        unmanaged=sum(1 for p in outs if not p.managed),
        pruned=pruned,
        packages=outs,
    )


@router.get("/plugins", response_model=list[InstalledPackageOut])
def list_plugins(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[InstalledPackageOut]:
    pending = {
        f"{p.source}:{p.full_name}": p.target_version
        for p in db.query(PendingUpdate).filter(PendingUpdate.status.in_(["queued", "deferred"])).all()
    }
    return [_pkg_out(p, pending) for p in db.query(InstalledPackage).order_by(InstalledPackage.name).all()]


@router.post("/plugins/import", response_model=InstalledPackageOut)
async def import_plugin(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    full_name: str | None = Form(default=None),
) -> InstalledPackageOut:
    """Manually import a .zip package or a .dll plugin."""
    from pathlib import Path
    import shutil

    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".zip", ".dll"}:
        raise HTTPException(400, "Upload a .zip package or a .dll plugin")

    settings = get_settings()
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name.replace("..", "_")
    dest = settings.downloads_dir / f"upload-{safe_name}"

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        if suffix == ".zip":
            pkg = installer.install_from_zip_file(db, dest, full_name_override=full_name or None)
        else:
            pkg = installer.import_dll_file(db, dest, full_name_override=full_name or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log_activity(
            db,
            "import",
            package=filename,
            source="manual",
            result="error",
            message=str(exc),
        )
        raise HTTPException(500, str(exc)) from exc
    finally:
        try:
            file.file.close()
        except Exception:
            pass

    return _pkg_out(pkg)


@router.get("/plugins/drift")
def plugin_drift(user: Annotated[AdminUser, Depends(_auth_user)]) -> dict[str, Any]:
    return {"messages": scanner.detect_drift()}


@router.post("/plugins/{package_id}/enable", response_model=InstalledPackageOut)
def enable_plugin(
    package_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    try:
        pkg = installer.set_enabled(db, package_id, True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _pkg_out(pkg)


@router.post("/plugins/{package_id}/disable", response_model=InstalledPackageOut)
def disable_plugin(
    package_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    try:
        pkg = installer.set_enabled(db, package_id, False)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _pkg_out(pkg)


@router.delete("/plugins/{package_id}")
def remove_plugin(
    package_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    try:
        installer.uninstall_package(db, package_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok"}


@router.post("/plugins/{package_id}/persistent", response_model=InstalledPackageOut)
def mark_plugin_persistent(
    package_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    try:
        pkg = installer.set_package_persistent(db, package_id, True)
    except (ValueError, FileNotFoundError, FileExistsError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _pkg_out(pkg)


@router.post("/plugins/{package_id}/normal", response_model=InstalledPackageOut)
def mark_plugin_normal(
    package_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    try:
        pkg = installer.set_package_persistent(db, package_id, False)
    except (ValueError, FileNotFoundError, FileExistsError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _pkg_out(pkg)


@router.post("/plugins/{package_id}/link", response_model=InstalledPackageOut)
def link_plugin(
    package_id: int,
    body: LinkPackageRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    try:
        pkg = installer.link_package(db, package_id, body.source, body.full_name, body.version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _pkg_out(pkg)


@router.patch("/plugins/{package_id}", response_model=InstalledPackageOut)
def patch_plugin(
    package_id: int,
    body: PackageActionRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstalledPackageOut:
    pkg = db.get(InstalledPackage, package_id)
    if pkg is None:
        raise HTTPException(404, "Not found")
    if body.pinned is not None:
        pkg.pinned = body.pinned
    if body.auto_update is not None:
        pkg.auto_update = body.auto_update
    db.commit()
    db.refresh(pkg)
    return _pkg_out(pkg)


@router.get("/packages/categories")
def package_categories(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
    source: str | None = None,
) -> dict:
    return {
        "categories": package_service.list_categories(db, source=source or None),
        "default_include": package_service.DEFAULT_SERVER_INCLUDE,
        "default_exclude": [],
    }


@router.get("/packages", response_model=list[PackageOut])
def search_packages(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str = "",
    source: str | None = None,
    category: str | None = None,
    include: Annotated[list[str] | None, Query()] = None,
    exclude: Annotated[list[str] | None, Query()] = None,
    sort: str = "downloads",
    limit: int = 50,
    offset: int = 0,
) -> list[PackageOut]:
    installed = {p.full_name: p for p in db.query(InstalledPackage).all()}
    results = package_service.search_packages(
        db,
        query=q,
        source=source,
        category=category,
        include=include,
        exclude=exclude,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    outs: list[PackageOut] = []
    for pkg in results:
        inst = installed.get(pkg.full_name)
        latest = pkg.latest
        outs.append(
            PackageOut(
                source=pkg.source,
                name=pkg.name,
                full_name=pkg.full_name,
                owner=pkg.owner,
                package_url=pkg.package_url,
                description=pkg.description,
                icon_url=pkg.icon_url,
                date_updated=pkg.date_updated,
                rating_score=pkg.rating_score,
                downloads=pkg.downloads,
                categories=pkg.categories,
                is_deprecated=pkg.is_deprecated,
                installed=inst is not None,
                installed_version=inst.version if inst else None,
                latest_version=latest.version_number if latest else None,
                dependencies=latest.dependencies if latest else [],
            )
        )
    return outs


@router.get("/packages/{source}/{full_name}/docs")
async def package_docs(
    source: str,
    full_name: str,
    user: Annotated[AdminUser, Depends(_auth_user)],
    version: str | None = None,
    kind: str = "readme",
) -> dict[str, Any]:
    pkg = package_service.get_package(source, full_name)
    if pkg is None:
        raise HTTPException(404, "Package not found — try refreshing the package index")
    ver = version or (pkg.latest.version_number if pkg.latest else None)
    if not ver:
        raise HTTPException(400, "No version available")
    try:
        return await package_service.fetch_package_doc(
            source,
            full_name,
            version=ver,
            kind=kind,
            owner=pkg.owner,
            name=pkg.name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch package {kind}: {exc}") from exc


@router.get("/packages/{source}/{full_name}", response_model=PackageOut)
def package_detail(
    source: str,
    full_name: str,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PackageOut:
    pkg = package_service.get_package(source, full_name)
    if pkg is None:
        raise HTTPException(404, "Package not found — try refreshing the package index")
    inst = db.query(InstalledPackage).filter(InstalledPackage.full_name == full_name).first()
    latest = pkg.latest
    return PackageOut(
        source=pkg.source,
        name=pkg.name,
        full_name=pkg.full_name,
        owner=pkg.owner,
        package_url=pkg.package_url,
        description=pkg.description,
        icon_url=pkg.icon_url,
        date_updated=pkg.date_updated,
        rating_score=pkg.rating_score,
        downloads=pkg.downloads,
        categories=pkg.categories,
        is_deprecated=pkg.is_deprecated,
        installed=inst is not None,
        installed_version=inst.version if inst else None,
        latest_version=latest.version_number if latest else None,
        versions=[
            PackageVersionOut(
                version_number=v.version_number,
                download_url=v.download_url,
                dependencies=v.dependencies,
                description=v.description,
                icon=v.icon,
                date_created=v.date_created,
                downloads=v.downloads,
                file_size=v.file_size,
                website_url=v.website_url,
            )
            for v in pkg.versions
        ],
        dependencies=latest.dependencies if latest else [],
    )


@router.post("/packages/refresh")
async def refresh_packages(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for source in package_service.enabled_sources(db):
        try:
            counts[source] = await package_service.refresh_source(source, db)
        except Exception as exc:
            logger.exception("Package index refresh failed for %s", source)
            errors[source] = str(exc)
            log_activity(
                db,
                "package_refresh",
                source=source,
                result="error",
                message=f"Refresh failed for {source}: {exc}",
            )
    if errors and not counts:
        raise HTTPException(502, f"Package index refresh failed: {errors}")
    return {"counts": counts, "errors": errors or None}


@router.post("/packages/preview", response_model=InstallPreview)
def preview_install(
    body: InstallRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> InstallPreview:
    try:
        result = installer.preview_install(db, body.source, body.full_name, body.version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return InstallPreview(**result)


@router.post("/packages/install", response_model=list[InstalledPackageOut])
async def install_package(
    body: InstallRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[InstalledPackageOut]:
    try:
        packages = await installer.install_packages(db, body.source, body.full_name, body.version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log_activity(db, "install", package=body.full_name, source=body.source, result="error", message=str(exc))
        raise HTTPException(500, str(exc)) from exc
    return [_pkg_out(p) for p in packages]


@router.get("/configs", response_model=list[ConfigFileOut])
def list_configs(user: Annotated[AdminUser, Depends(_auth_user)]) -> list[ConfigFileOut]:
    return [ConfigFileOut(**c) for c in config_editor.list_config_files()]


@router.get("/configs/{name}", response_model=ConfigContentOut)
def get_config(name: str, user: Annotated[AdminUser, Depends(_auth_user)]) -> ConfigContentOut:
    try:
        return ConfigContentOut(**config_editor.read_config(name))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Config not found") from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/configs/{name}", response_model=ConfigContentOut)
def put_config(
    name: str,
    body: ConfigSaveRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ConfigContentOut:
    try:
        result = config_editor.save_config(name, raw=body.raw, structured=body.structured)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    from ..services.settings_service import set_setting

    set_setting(db, "restart_required", True)
    log_activity(db, "config_edit", package=name, result="ok")
    return ConfigContentOut(**result)


@router.get("/activity", response_model=list[ActivityOut])
def activity(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
    action: str | None = None,
    q: str = "",
    limit: int = 100,
) -> list[ActivityOut]:
    query = db.query(ActivityEvent).order_by(ActivityEvent.timestamp.desc())
    if action:
        query = query.filter(ActivityEvent.action == action)
    rows = query.limit(limit).all()
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if (r.package and ql in r.package.lower())
            or (r.message and ql in r.message.lower())
            or ql in r.action.lower()
        ]
    return [ActivityOut.model_validate(r) for r in rows]


@router.get("/backups", response_model=list[BackupOut])
def list_backups(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[BackupOut]:
    outs = []
    for b in backup_service.list_backups(db):
        names = []
        try:
            names = json.loads(b.package_names_json or "[]")
        except json.JSONDecodeError:
            names = []
        outs.append(
            BackupOut(
                id=b.id,
                created_at=b.created_at,
                label=b.label,
                reason=b.reason,
                path=b.path,
                package_names=names,
                notes=b.notes,
                success=b.success,
            )
        )
    return outs


@router.post("/backups", response_model=BackupOut)
def create_backup(
    body: CreateBackupRequest,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupOut:
    record = backup_service.create_backup(
        db, label=body.label, notes=body.notes, package_ids=body.package_ids, reason="manual"
    )
    names = json.loads(record.package_names_json or "[]")
    return BackupOut(
        id=record.id,
        created_at=record.created_at,
        label=record.label,
        reason=record.reason,
        path=record.path,
        package_names=names,
        notes=record.notes,
        success=record.success,
    )


@router.post("/backups/{backup_id}/restore", response_model=BackupOut)
def restore_backup(
    backup_id: int,
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupOut:
    try:
        record = backup_service.restore_backup(db, backup_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    names = json.loads(record.package_names_json or "[]")
    return BackupOut(
        id=record.id,
        created_at=record.created_at,
        label=record.label,
        reason=record.reason,
        path=record.path,
        package_names=names,
        notes=record.notes,
        success=record.success,
    )


@router.get("/updates", response_model=list[PendingUpdateOut])
def list_updates(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PendingUpdateOut]:
    rows = db.query(PendingUpdate).order_by(PendingUpdate.detected_at.desc()).all()
    return [PendingUpdateOut.model_validate(r) for r in rows]


@router.post("/updates/check", response_model=list[PendingUpdateOut])
async def check_updates(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PendingUpdateOut]:
    rows = await update_service.check_for_updates(db)
    return [PendingUpdateOut.model_validate(r) for r in rows]


@router.post("/updates/apply")
async def apply_updates(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
    body: ApplyUpdatesRequest | None = None,
) -> dict[str, Any]:
    # Manual apply ignores player gate
    from ..services.settings_service import set_setting

    pending = db.query(PendingUpdate).filter(PendingUpdate.status.in_(["queued", "deferred"])).all()
    if body and body.full_names:
        wanted = {n.strip() for n in body.full_names if n and n.strip()}
        pending = [p for p in pending if p.full_name in wanted]
    if not pending:
        raise HTTPException(404, "No matching pending updates")
    if get_setting(db, "backup_before_update", True):
        backup_service.create_backup(db, reason="pre-update", label="manual-pre-update")
    results = []
    for p in pending:
        try:
            await installer.install_packages(db, p.source, p.full_name, p.target_version)
            p.status = "done"
            log_activity(
                db,
                "update",
                package=p.full_name,
                source=p.source,
                result="ok",
                message=f"{p.current_version or '?'} → {p.target_version}",
            )
            results.append({"full_name": p.full_name, "ok": True, "version": p.target_version})
        except Exception as exc:
            p.status = "failed"
            logger.exception("Update failed for %s@%s", p.full_name, p.source)
            log_activity(
                db,
                "update",
                package=p.full_name,
                source=p.source,
                result="error",
                message=str(exc),
            )
            results.append({"full_name": p.full_name, "ok": False, "error": str(exc)})
        db.commit()
    # Manual Update all / per-row Update never restarts — leave that to Restart + sync
    # (or the maintenance window when restart_after_updates is enabled).
    if any(r.get("ok") for r in results):
        set_setting(db, "restart_required", True)
    return {"results": results, "restart": None}


@router.post("/server/restart")
def server_restart(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    result = supervisor.restart_server(db)
    log_activity(db, "restart", result="ok" if result.get("ok") else "error", message=result.get("message"))
    if result.get("ok"):
        from ..services.settings_service import set_setting

        set_setting(db, "restart_required", False)
    return result


@router.get("/server/status")
def server_status(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return {
        "configured": supervisor.supervisor_configured(db),
        "status": supervisor.get_process_status(db),
        "display_name": get_setting(db, "container_display_name", "valheim"),
    }


@router.get("/server/diagnose")
def server_diagnose(
    user: Annotated[AdminUser, Depends(_auth_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return supervisor.diagnose(db)
