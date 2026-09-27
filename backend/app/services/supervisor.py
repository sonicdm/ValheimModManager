from __future__ import annotations

import logging
import xmlrpc.client
from typing import Any

from sqlalchemy.orm import Session

from .settings_service import get_setting

logger = logging.getLogger(__name__)


def _proxy(db: Session) -> xmlrpc.client.ServerProxy | None:
    url = (get_setting(db, "supervisor_url", "") or "").rstrip("/")
    if not url:
        return None
    user = get_setting(db, "supervisor_user", "admin") or "admin"
    password = get_setting(db, "supervisor_password", "") or ""
    # Build URL with optional basic auth
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        scheme, rest = "http", url
    if password:
        auth_url = f"{scheme}://{user}:{password}@{rest}/RPC2"
    else:
        auth_url = f"{scheme}://{rest}/RPC2"
    return xmlrpc.client.ServerProxy(auth_url, allow_none=True)


def supervisor_configured(db: Session) -> bool:
    return bool(get_setting(db, "supervisor_url", ""))


def get_process_status(db: Session) -> str | None:
    proxy = _proxy(db)
    if proxy is None:
        return None
    program = get_setting(db, "supervisor_program", "valheim-server") or "valheim-server"
    try:
        info = proxy.supervisor.getProcessInfo(program)
        return str(info.get("statename") or info.get("state") or "unknown")
    except Exception as exc:
        logger.warning("Supervisor status failed: %s", exc)
        return f"error: {exc}"


def restart_server(db: Session) -> dict[str, Any]:
    proxy = _proxy(db)
    if proxy is None:
        return {"ok": False, "message": "Supervisor not configured"}
    program = get_setting(db, "supervisor_program", "valheim-server") or "valheim-server"
    try:
        proxy.supervisor.stopProcess(program)
        proxy.supervisor.startProcess(program)
        info = proxy.supervisor.getProcessInfo(program)
        return {"ok": True, "status": info.get("statename"), "message": f"Restarted {program}"}
    except Exception as exc:
        logger.exception("Supervisor restart failed")
        return {"ok": False, "message": str(exc)}


def get_tail_log(db: Session, bytes_count: int = 4096) -> str | None:
    proxy = _proxy(db)
    if proxy is None:
        return None
    program = get_setting(db, "supervisor_program", "valheim-server") or "valheim-server"
    try:
        # supervisor API: tailProcessStdoutLog(name, offset, length) -> [data, newOffset, overflow]
        result = proxy.supervisor.tailProcessStdoutLog(program, 0, bytes_count)
        if isinstance(result, (list, tuple)) and result:
            return str(result[0])
        return str(result)
    except Exception as exc:
        logger.warning("Supervisor log tail failed: %s", exc)
        return None
