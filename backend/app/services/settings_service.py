from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import ActivityEvent, Setting


DEFAULT_SETTINGS: dict[str, Any] = {
    "timezone": "UTC",
    "container_display_name": "valheim",
    "supervisor_url": "",
    "supervisor_user": "admin",
    "supervisor_password": "",
    "supervisor_program": "valheim-server",
    "thunderstore_enabled": True,
    "hexium_enabled": True,
    "package_refresh_minutes": 60,
    "update_check_minutes": 60,
    "auto_install_updates": True,
    "maintenance_enabled": True,
    "maintenance_hour": 4,
    "maintenance_minute": 0,
    "backup_before_update": True,
    "restart_after_updates": True,
    "update_pinned_packages": False,
    "update_policy": "all",  # all|selected|notify|manual
    "maintenance_lock": False,
    "force_restart_when_players_unknown": False,
    "max_deferral_hours": 24,
    "theme": "dark",
    "restart_required": False,
    "last_scan_at": None,
    "last_update_check_at": None,
}


SENSITIVE_KEYS = {"supervisor_password"}


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return row.value


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    encoded = json.dumps(value)
    if row is None:
        db.add(Setting(key=key, value=encoded))
    else:
        row.value = encoded
    db.commit()


def get_all_settings(db: Session, *, include_secrets: bool = False) -> dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    for row in db.query(Setting).all():
        try:
            result[row.key] = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            result[row.key] = row.value
    if not include_secrets:
        for key in SENSITIVE_KEYS:
            if key in result and result[key]:
                result[key] = "***"
            elif key in result:
                result[key] = ""
    return result


def update_settings(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if key in SENSITIVE_KEYS and value == "***":
            continue
        set_setting(db, key, value)
    return get_all_settings(db)


def log_activity(
    db: Session,
    action: str,
    *,
    package: str | None = None,
    source: str | None = None,
    result: str = "ok",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> ActivityEvent:
    event = ActivityEvent(
        timestamp=datetime.now(timezone.utc),
        action=action,
        package=package,
        source=source,
        result=result,
        message=message,
        details_json=json.dumps(details) if details else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
