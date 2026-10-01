from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import InstalledPackage, PendingUpdate
from .backup import create_backup
from .installer import install_packages
from .packages import enabled_sources, get_package, refresh_source
from .scanner import persist_scan
from .settings_service import get_setting, log_activity, set_setting
from .supervisor import get_process_status, restart_server, supervisor_configured

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def job_scan_plugins() -> None:
    """One-shot disk scan (used at process start)."""
    from .job_status import scan_running

    db = get_session()
    try:
        log_activity(
            db,
            "scan",
            result="ok",
            message="Startup plugin scan started (walking config/bepinex; large persistent packs can take a while)",
        )
        with scan_running():
            packages, pruned = persist_scan(db, reason="startup")
            logger.info(
                "Plugin scan finished: %s on disk%s",
                len(packages),
                f", pruned {len(pruned)}" if pruned else "",
            )
    except Exception:
        logger.exception("Plugin scan failed")
        try:
            log_activity(db, "scan", result="error", message="Startup/scheduled scan failed")
        except Exception:
            logger.exception("Could not log scan failure")
    finally:
        db.close()


async def job_refresh_packages() -> None:
    from .job_status import package_refresh_running

    db = get_session()
    try:
        with package_refresh_running():
            for source in enabled_sources(db):
                try:
                    await refresh_source(source, db)
                except Exception:
                    logger.exception("Failed refreshing %s", source)
                    log_activity(
                        db,
                        "package_refresh",
                        source=source,
                        result="error",
                        message=f"Refresh failed for {source}",
                    )
    finally:
        db.close()


async def job_startup() -> None:
    """Boot scan then package refresh — sequential so they do not fight over SQLite."""
    job_scan_plugins()
    await job_refresh_packages()


def job_status_snapshot() -> dict[str, bool]:
    from .job_status import snapshot

    return snapshot()


async def job_check_updates() -> None:
    db = get_session()
    try:
        await check_for_updates(db)
    finally:
        db.close()


async def job_maintenance_window() -> None:
    db = get_session()
    try:
        await run_maintenance(db)
    finally:
        db.close()


async def check_for_updates(db: Session) -> list[PendingUpdate]:
    found: list[PendingUpdate] = []
    # Refresh Thunderstore/Hexium indexes when empty or older than a short TTL so
    # the dashboard "Check updates" button is useful after a fresh publish.
    max_age = int(get_setting(db, "update_check_index_max_age_minutes", 5) or 5)
    max_age = max(0, max_age)
    for source in enabled_sources(db):
        if not _package_index_needs_refresh(db, source, max_age):
            continue
        try:
            await refresh_source(source, db)
        except Exception:
            logger.exception("refresh during update check failed")

    for pkg in db.query(InstalledPackage).filter(InstalledPackage.managed.is_(True)).all():
        if pkg.source not in ("thunderstore", "hexium"):
            continue
        remote = get_package(pkg.source, pkg.full_name)
        if remote is None or remote.latest is None:
            continue
        latest = remote.latest.version_number
        if not pkg.version or latest == pkg.version:
            # clear pending if any
            existing = (
                db.query(PendingUpdate)
                .filter(PendingUpdate.source == pkg.source, PendingUpdate.full_name == pkg.full_name)
                .first()
            )
            if existing and existing.status == "queued":
                db.delete(existing)
            continue
        if pkg.pinned and not get_setting(db, "update_pinned_packages", False):
            continue
        if not pkg.auto_update and get_setting(db, "update_policy", "all") == "selected":
            continue

        pending = (
            db.query(PendingUpdate)
            .filter(PendingUpdate.source == pkg.source, PendingUpdate.full_name == pkg.full_name)
            .first()
        )
        if pending is None:
            pending = PendingUpdate(
                source=pkg.source,
                full_name=pkg.full_name,
                current_version=pkg.version,
                target_version=latest,
                download_url=remote.latest.download_url,
                status="queued",
            )
            db.add(pending)
        else:
            pending.current_version = pkg.version
            pending.target_version = latest
            pending.download_url = remote.latest.download_url
            if pending.status in ("done", "failed"):
                pending.status = "queued"
        found.append(pending)

    set_setting(db, "last_update_check_at", datetime.now(timezone.utc).isoformat())
    log_activity(db, "update_check", result="ok", message=f"Found {len(found)} updates")
    db.commit()
    return found


def _package_index_needs_refresh(db: Session, source: str, max_age_minutes: int) -> bool:
    """True when the source index is missing or older than max_age_minutes."""
    from ..models import PackageCacheMeta
    from .packages import get_cached_packages

    if not get_cached_packages(source):
        return True
    if max_age_minutes <= 0:
        return True
    meta = db.get(PackageCacheMeta, source)
    if meta is None or meta.last_refreshed_at is None:
        return True
    ts = meta.last_refreshed_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
    return age >= timedelta(minutes=max_age_minutes)


def _in_maintenance_window(db: Session, now: datetime | None = None) -> bool:
    tz_name = get_setting(db, "timezone", "America/Los_Angeles") or "America/Los_Angeles"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    hour = int(get_setting(db, "maintenance_hour", 4) or 4)
    minute = int(get_setting(db, "maintenance_minute", 0) or 0)
    # Window: configured time ± 30 minutes
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return abs((now - target).total_seconds()) <= 30 * 60


def next_maintenance_window(db: Session) -> str:
    tz_name = get_setting(db, "timezone", "America/Los_Angeles") or "America/Los_Angeles"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    hour = int(get_setting(db, "maintenance_hour", 4) or 4)
    minute = int(get_setting(db, "maintenance_minute", 0) or 0)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target.isoformat()


async def run_maintenance(db: Session) -> None:
    if not get_setting(db, "maintenance_enabled", True):
        return
    if get_setting(db, "maintenance_lock", False):
        log_activity(db, "maintenance", result="deferred", message="Maintenance lock active")
        return
    if not _in_maintenance_window(db):
        return

    policy = get_setting(db, "update_policy", "all")
    if policy in ("manual", "notify"):
        log_activity(db, "maintenance", result="ok", message=f"Policy is {policy}; not auto-installing")
        return

    pending = db.query(PendingUpdate).filter(PendingUpdate.status == "queued").all()
    if not pending:
        return

    # No player probe exists for private servers — require explicit opt-in for unattended work.
    force = bool(get_setting(db, "force_restart_when_players_unknown", False))
    if not force:
        for p in pending:
            p.status = "deferred"
        log_activity(
            db,
            "maintenance",
            result="deferred",
            message="Deferred updates: unattended scheduled maintenance is off",
        )
        db.commit()
        return

    if get_setting(db, "backup_before_update", True):
        create_backup(db, label=f"pre-update-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}", reason="pre-update")

    for p in pending:
        pkg = (
            db.query(InstalledPackage)
            .filter(InstalledPackage.full_name == p.full_name, InstalledPackage.source == p.source)
            .first()
        )
        if pkg and pkg.pinned and not get_setting(db, "update_pinned_packages", False):
            continue
        if policy == "selected" and pkg and not pkg.auto_update:
            continue
        p.status = "installing"
        db.commit()
        try:
            await install_packages(db, p.source, p.full_name, p.target_version)
            p.status = "done"
            log_activity(db, "update", package=p.full_name, source=p.source, result="ok", message=f"Updated to {p.target_version}")
        except Exception as exc:
            p.status = "failed"
            log_activity(db, "update", package=p.full_name, source=p.source, result="error", message=str(exc))
        db.commit()

    if get_setting(db, "restart_after_updates", False) and supervisor_configured(db):
        result = restart_server(db)
        log_activity(
            db,
            "restart",
            result="ok" if result.get("ok") else "error",
            message=result.get("message"),
        )
        if result.get("ok"):
            set_setting(db, "restart_required", False)
    elif any(p.status == "done" for p in pending):
        set_setting(db, "restart_required", True)


def configure_scheduler(*, force: bool = False) -> None:
    """Start or refresh interval/cron jobs from current DB settings."""
    db = get_session()
    try:
        refresh_min = int(get_setting(db, "package_refresh_minutes", 60) or 60)
        update_min = int(get_setting(db, "update_check_minutes", 60) or 60)
        tz_name = get_setting(db, "timezone", "America/Los_Angeles") or "America/Los_Angeles"
        hour = int(get_setting(db, "maintenance_hour", 4) or 4)
        minute = int(get_setting(db, "maintenance_minute", 0) or 0)
    finally:
        db.close()

    try:
        ZoneInfo(tz_name)
    except Exception:
        logger.warning("Invalid timezone %r — falling back to UTC for scheduler", tz_name)
        tz_name = "UTC"

    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started (timezone=%s, maintenance=%02d:%02d)", tz_name, hour, minute)
    elif not force:
        return

    scheduler.add_job(
        job_refresh_packages,
        "interval",
        minutes=refresh_min,
        id="refresh_packages",
        replace_existing=True,
    )
    scheduler.add_job(
        job_check_updates,
        "interval",
        minutes=update_min,
        id="check_updates",
        replace_existing=True,
    )
    scheduler.add_job(
        job_maintenance_window,
        "cron",
        hour=hour,
        minute=minute,
        timezone=tz_name,
        id="maintenance",
        replace_existing=True,
    )
    # Also poll maintenance window every 15 minutes in case of missed cron edge
    scheduler.add_job(
        job_maintenance_window,
        "interval",
        minutes=15,
        id="maintenance_poll",
        replace_existing=True,
    )
    if force and scheduler.running:
        logger.info(
            "Scheduler refreshed (timezone=%s, maintenance=%02d:%02d local)",
            tz_name,
            hour,
            minute,
        )


def reschedule_scheduler() -> None:
    """Re-apply jobs after settings change (timezone / maintenance hour / intervals)."""
    configure_scheduler(force=True)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
