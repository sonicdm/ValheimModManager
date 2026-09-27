from __future__ import annotations

import logging
import socket
import time
import xmlrpc.client
from typing import Any
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from .settings_service import get_setting

logger = logging.getLogger(__name__)

# stopwaitsecs for valheim-server is 90; leave headroom for stop + bootstrap.
SUPERVISOR_TIMEOUT_SECONDS = 120.0
BOOTSTRAP_PROGRAM = "valheim-bootstrap"
BOOTSTRAP_WAIT_SECONDS = 300.0
BOOTSTRAP_POLL_SECONDS = 1.0


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


def _process_state(proxy: xmlrpc.client.ServerProxy, program: str) -> str:
    info = proxy.supervisor.getProcessInfo(program)
    return str(info.get("statename") or "")


def _stop_process(proxy: xmlrpc.client.ServerProxy, program: str) -> None:
    state = _process_state(proxy, program)
    if state in {"STOPPED", "EXITED", "FATAL", "UNKNOWN"}:
        return
    try:
        proxy.supervisor.stopProcess(program)
    except xmlrpc.client.Fault as exc:
        # Already stopped is fine
        if "NOT_RUNNING" not in str(exc) and "NOTRUNNING" not in str(exc):
            raise


def _wait_bootstrap_exited(proxy: xmlrpc.client.ServerProxy) -> str:
    deadline = time.monotonic() + BOOTSTRAP_WAIT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        last = _process_state(proxy, BOOTSTRAP_PROGRAM)
        if last in {"EXITED", "FATAL", "STOPPED"}:
            return last
        time.sleep(BOOTSTRAP_POLL_SECONDS)
    raise TimeoutError(f"{BOOTSTRAP_PROGRAM} did not exit within {int(BOOTSTRAP_WAIT_SECONDS)}s (last={last})")


def diagnose_supervisor(db: Session) -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured": supervisor_configured(db),
        "tcp_ok": False,
        "rpc_ok": False,
        "status": None,
        "error": None,
        "hint": None,
    }
    base = _supervisor_base(db)
    if base is None:
        result["hint"] = "Set SUPERVISOR_URL (e.g. http://valheim:9001 on the shared Docker network)"
        return result
    url, _user, _password = base
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9001
        with socket.create_connection((host, port), timeout=SUPERVISOR_TIMEOUT_SECONDS):
            result["tcp_ok"] = True
    except OSError as exc:
        result["error"] = _friendly_error(exc)
        result["hint"] = (
            "TCP failed. Prefer container DNS: attach to the Valheim network "
            "and use http://<valheim-container>:9001"
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
    """Stop valheim-server, run valheim-bootstrap (config→live sync), then start the server.

    Bootstrap is the only image path that rsyncs config/bepinex into the live tree.
    Restarting valheim-server alone does not sync plugins.
    """
    proxy = _proxy(db)
    if proxy is None:
        return {"ok": False, "message": "Supervisor not configured", "synced": False}

    program = get_setting(db, "supervisor_program", "valheim-server") or "valheim-server"
    try:
        _stop_process(proxy, program)

        # Bootstrap is oneshot (autorestart=false). Start it even if already EXITED.
        try:
            proxy.supervisor.startProcess(BOOTSTRAP_PROGRAM)
        except xmlrpc.client.Fault as exc:
            # If still RUNNING from a prior start, wait it out
            if "ALREADY_STARTED" not in str(exc) and "ALREADYSTARTED" not in str(exc):
                raise

        bootstrap_state = _wait_bootstrap_exited(proxy)
        if bootstrap_state == "FATAL":
            return {
                "ok": False,
                "message": f"{BOOTSTRAP_PROGRAM} exited FATAL",
                "synced": False,
                "bootstrap": bootstrap_state,
            }

        proxy.supervisor.startProcess(program)
        info = proxy.supervisor.getProcessInfo(program)
        return {
            "ok": True,
            "status": info.get("statename"),
            "message": f"Ran {BOOTSTRAP_PROGRAM} then started {program}",
            "synced": True,
            "bootstrap": bootstrap_state,
        }
    except Exception as exc:
        logger.exception("Supervisor restart/bootstrap failed")
        return {"ok": False, "message": _friendly_error(exc), "synced": False}


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
