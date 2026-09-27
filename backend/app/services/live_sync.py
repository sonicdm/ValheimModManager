from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import Settings, get_settings
from .paths import PathEscapeError, ensure_within

logger = logging.getLogger(__name__)


def live_sync_available(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    live = settings.live_plugins_root
    if live is None:
        return False
    try:
        live.mkdir(parents=True, exist_ok=True)
        probe = live / ".vmm_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def sync_config_tree_to_live(settings: Settings | None = None) -> dict[str, int]:
    """Mirror config plugins/patchers into the live BepInEx install dirs.

    Does not delete unknown live files (runtime-generated data may live there).
    Returns counts of copied files.
    """
    settings = settings or get_settings()
    copied = {"plugins": 0, "patchers": 0}
    if not live_sync_available(settings):
        logger.warning("Live plugins path missing or not writable — skip sync")
        return copied

    assert settings.live_plugins_root is not None
    copied["plugins"] = _mirror_dir(settings.plugins_dir, settings.live_plugins_root)

    live_patchers = _live_patchers_root(settings)
    if live_patchers is not None and settings.patchers_dir.is_dir():
        try:
            live_patchers.mkdir(parents=True, exist_ok=True)
            copied["patchers"] = _mirror_dir(settings.patchers_dir, live_patchers)
        except OSError:
            logger.warning("Live patchers path not writable — skip patcher sync")

    logger.info(
        "Synced to live install: %s plugin files, %s patcher files",
        copied["plugins"],
        copied["patchers"],
    )
    return copied


def remove_paths_from_live(relative_plugin_paths: list[str], settings: Settings | None = None) -> int:
    """Remove plugin-relative paths from the live plugins tree (best-effort)."""
    settings = settings or get_settings()
    if not live_sync_available(settings):
        return 0
    assert settings.live_plugins_root is not None
    removed = 0
    for rel in relative_plugin_paths:
        target = settings.live_plugins_root / rel
        try:
            ensure_within(settings.live_plugins_root, target if target.exists() else target.parent)
        except PathEscapeError:
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
                removed += 1
            elif target.is_dir():
                shutil.rmtree(target)
                removed += 1
        except OSError as exc:
            logger.warning("Could not remove live path %s: %s", target, exc)
    return removed


def _live_patchers_root(settings: Settings) -> Path | None:
    mounted = Path("/valheim/live-patchers")
    if mounted.parent.exists():
        return mounted
    live = settings.live_plugins_root
    if live is not None and live.name == "plugins":
        return live.parent / "patchers"
    return None


def _mirror_dir(src: Path, dest: Path) -> int:
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in src.rglob("*"):
        if path.is_dir() or path.is_symlink():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        try:
            ensure_within(dest, target.parent)
        except PathEscapeError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists() and target.stat().st_size == path.stat().st_size:
                # Cheap skip when size matches; still copy if mtime newer
                if target.stat().st_mtime >= path.stat().st_mtime:
                    continue
            shutil.copy2(path, target)
            count += 1
        except OSError as exc:
            logger.warning("Failed syncing %s -> %s: %s", path, target, exc)
    return count
