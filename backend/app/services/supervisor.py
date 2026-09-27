from __future__ import annotations

import logging
import socket
import xmlrpc.client
from typing import Any
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from .settings_service import get_setting

logger = logging.getLogger(__name__)

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


def _supervisor_base(db: Session) -> tuple[str, str, str] | None:
    url = (get_setting(db, "supervisor_url", "") or "").rstrip("/")
    if not url:
        return None
    user = get_setting(db, "supervisor_user", "admin") or "admin"
    password = get_setting(db, "supervisor_password", "") or ""
    return url, user, password


def _proxy(db: Session) -> xmlrpc.client.ServerProxy | None:
    base = _supervisor_base(db)
    if base is None:
        return None
    url, user, password = base
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        scheme, rest = "http", url
    # Strip any userinfo already in the URL
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    if password:
        auth_url = f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{rest}/RPC2"
    else:
        auth_url = f"{scheme}://{rest}/RPC2"

    parsed = urlparse(f"{scheme}://{rest}")
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
        return "cannot resolve Supervisor hostname (check SUPERVISOR_URL / Docker network)"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "Supervisor timed out (wrong host, firewall, or not published)"
    if isinstance(exc, ConnectionRefusedError) or "Connection refused" in str(exc):
        return "connection refused (Supervisor not listening or wrong port)"
    msg = str(exc)
    if "Name or service not known" in msg or "getaddrinfo" in msg:
        return "cannot resolve Supervisor hostname (check SUPERVISOR_URL / Docker network)"
    if "401" in msg or "Unauthorized" in msg:
        return "unauthorized (check SUPERVISOR_USER / SUPERVISOR_PASSWORD; Valheim wants SUPERVISOR_HTTP_PASS)"
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


def diagnose(db: Session) -> dict[str, Any]:
    """Return a step-by-step connection diagnosis for the UI / ops."""
    base = _supervisor_base(db)
    result: dict[str, Any] = {
        "configured": base is not None,
        "url": None,
        "host": None,
        "port": None,
        "dns_ok": None,
        "tcp_ok": None,
        "rpc_ok": None,
        "status": None,
        "error": None,
        "hint": None,
    }
    if base is None:
        result["error"] = "SUPERVISOR_URL is not set"
        result["hint"] = "Set SUPERVISOR_URL=http://BabyGotBoar:9001 and join the Valheim Docker network"
        return result

    url, user, password = base
    result["url"] = url
    result["auth_configured"] = bool(password)
    result["user"] = user

    raw = url
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname
    port = parsed.port or 9001
    result["host"] = host
    result["port"] = port

    if not host:
        result["error"] = "Could not parse host from SUPERVISOR_URL"
        return result

    try:
        socket.getaddrinfo(host, port)
        result["dns_ok"] = True
    except socket.gaierror as exc:
        result["dns_ok"] = False
        result["error"] = _friendly_error(exc)
        result["hint"] = (
            "Join the Valheim compose network and use http://BabyGotBoar:9001. "
            "Run: docker network ls"
        )
        return result

    try:
        with socket.create_connection((host, port), timeout=SUPERVISOR_TIMEOUT_SECONDS):
            result["tcp_ok"] = True
    except OSError as exc:
        result["tcp_ok"] = False
        result["error"] = _friendly_error(exc)
        if host in {"localhost", "127.0.0.1"}:
            result["hint"] = "localhost inside the container is not the host — use BabyGotBoar or host.docker.internal"
        else:
            result["hint"] = (
                "TCP failed. Prefer container DNS: attach to the Valheim network and use http://BabyGotBoar:9001"
            )
        return result

    status = get_process_status(db)
    if status and not status.startswith("error:"):
        result["rpc_ok"] = True
        result["status"] = status
    else:
        result["rpc_ok"] = False
        result["error"] = status or "RPC failed"
        result["hint"] = (
            "HTTP reached the port but XML-RPC failed. Check SUPERVISOR_HTTP=true on Valheim, "
            "and use SUPERVISOR_HTTP_PASS (not SUPERVISOR_PASS). Leave manager password empty if Valheim has no auth."
        )
    return result


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
