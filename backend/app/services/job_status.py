"""In-process flags for long-running background jobs (startup scan / index refresh)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_lock = threading.Lock()
_scan_in_progress = False
_package_refresh_in_progress = False
# True from process start until the first plugin scan finishes (success or fail).
_startup_scan_pending = True


def mark_startup_scan_pending(pending: bool = True) -> None:
    global _startup_scan_pending
    with _lock:
        _startup_scan_pending = pending


def set_scan_in_progress(active: bool) -> None:
    global _scan_in_progress, _startup_scan_pending
    with _lock:
        _scan_in_progress = active
        if not active:
            _startup_scan_pending = False


def set_package_refresh_in_progress(active: bool) -> None:
    global _package_refresh_in_progress
    with _lock:
        _package_refresh_in_progress = active


def snapshot() -> dict[str, bool]:
    with _lock:
        return {
            "scan_in_progress": _scan_in_progress or _startup_scan_pending,
            "package_refresh_in_progress": _package_refresh_in_progress,
            "startup_scan_pending": _startup_scan_pending,
        }


@contextmanager
def scan_running() -> Iterator[None]:
    set_scan_in_progress(True)
    try:
        yield
    finally:
        set_scan_in_progress(False)


@contextmanager
def package_refresh_running() -> Iterator[None]:
    set_package_refresh_in_progress(True)
    try:
        yield
    finally:
        set_package_refresh_in_progress(False)
