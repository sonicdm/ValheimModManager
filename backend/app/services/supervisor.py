from __future__ import annotations

import logging
import socket
import xmlrpc.client
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .settings_service import get_setting

logger = logging.getLogger(__name__)

# Keep dashboard/status snappy even when Supervisor DNS/host is wrong.
SUPERVISOR_TIMEOUT_SECONDS = 2.0


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = SUPERVISOR_TIMEOUT_SECONDS):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):  # noqa: ANN001
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float = SUPERVISOR_TIMEOUT_SECONDS):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):  # noqa: ANN001
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def _proxy(db: Session) -> xmlrpc.client.ServerProxy | None:
    url = (get_setting(db, "supervisor_url", "") or "").rstrip("/")
    if not url:
        return None
    user = get_setting(db, "supervisor_user", "admin") or "admin"
    password = get_setting(db, "supervisor_password", "") or ""
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        scheme, rest = "http", url
    if password:
        auth_url = f"{scheme}://{user}:{password}@{rest}/RPC2"
    else:
        auth_url = f"{scheme}://{rest}/RPC2"

    parsed = urlparse(auth_url)
    transport: xmlrpc.client.Transport
    if parsed.scheme == "https":
        transport = _TimeoutSafeTransport(SUPERVISOR_TIMEOUT_SECONDS)
    else:
        transport = _TimeoutTransport(SUPERVISOR_TIMEOUT_SECONDS)
    return xmlrpc.client.ServerProxy(auth_url, allow_none=True, transport=transport)


def supervisor_configured(db: Session) -> bool:
    return bool(get_setting(db, "supervisor_url", ""))


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "cannot resolve Supervisor hostname (check SUPERVISOR_URL)"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "Supervisor timed out"
    msg = str(exc)
    if "Name or service not known" in msg or "getaddrinfo" in msg:
        return "cannot resolve Supervisor hostname (check SUPERVISOR_URL)"
    return msg


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
        return f"error: {_friendly_error(exc)}"


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
        return {"ok": False, "message": _friendly_error(exc)}


def get_tail_log(db: Session, bytes_count: int = 4096) -> str | None:
    proxy = _proxy(db)
    if proxy is None:
        return None
    program = get_setting(db, "supervisor_program", "valheim-server") or "valheim-server"
    try:
        result = proxy.supervisor.tailProcessStdoutLog(program, 0, bytes_count)
        if isinstance(result, (list, tuple)) and result:
            return str(result[0])
        return str(result)
    except Exception as exc:
        logger.warning("Supervisor log tail failed: %s", exc)
        return None
