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


def config_plugin_names(settings: Settings | None = None) -> set[str]:
    settings = settings or get_settings()
    if not settings.plugins_dir.is_dir():
        return set()
    return {p.name for p in settings.plugins_dir.iterdir()}


def live_only_plugin_names(settings: Settings | None = None) -> set[str]:
    """Top-level plugin entries on live that are absent from config."""
    settings = settings or get_settings()
    live = settings.live_plugins_root
    if live is None or not live.is_dir():
        return set()
    config_names = config_plugin_names(settings)
    return {
        p.name
        for p in live.iterdir()
        if not p.name.startswith(".") and p.name not in config_names
    }


def _top_level(relative: str) -> str:
    return relative.replace("\\", "/").split("/", 1)[0]


def resolve_live_only_folder(
    settings: Settings,
    *,
    full_name: str,
    name: str | None = None,
) -> Path | None:
    """If this package already lives only under data/plugins, return that folder."""
    live = settings.live_plugins_root
    if live is None or not live_sync_available(settings):
        return None
    protected = live_only_plugin_names(settings)
    candidates = [full_name]
    if name:
        candidates.append(name)
        candidates.append(name.replace(" ", ""))
    if "-" in full_name:
        candidates.append(full_name.split("-", 1)[-1])
    for cand in candidates:
        if cand and cand in protected:
            return live / cand
    # Existing live folder matching package name even if also somehow in config names set empty
    for cand in candidates:
        if not cand:
            continue
        path = live / cand
        if path.is_dir() and cand not in config_plugin_names(settings):
            return path
    return None


def path_is_under_live(path: Path | str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    live = settings.live_plugins_root
    if live is None:
        return False
    try:
        Path(path).resolve().relative_to(live.resolve())
        return True
    except (ValueError, OSError):
        return False


def sync_paths_to_live(relative_paths: list[str], settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not live_sync_available(settings):
        return 0
    assert settings.live_plugins_root is not None
    protected = live_only_plugin_names(settings)
    copied = 0
    for rel in relative_paths:
        if _top_level(rel) in protected:
            continue
        src = settings.plugins_dir / rel
        if not src.exists():
            continue
        try:
            ensure_within(settings.plugins_dir, src if src.exists() else src.parent)
        except PathEscapeError:
            continue
        if src.is_file():
            copied += _copy_file(src, settings.live_plugins_root / rel, settings.live_plugins_root)
        elif src.is_dir():
            for path in src.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                file_rel = path.relative_to(settings.plugins_dir).as_posix()
                if _top_level(file_rel) in protected:
                    continue
                copied += _copy_file(
                    path, settings.live_plugins_root / file_rel, settings.live_plugins_root
                )
    return copied


def sync_managed_to_live(
    owned_relative_paths: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Push config → live for config-managed installs only. Never touches live-only trees."""
    settings = settings or get_settings()
    result = {
        "plugins": 0,
        "patchers": 0,
        "live_only_skipped": sorted(live_only_plugin_names(settings)),
    }
    if not live_sync_available(settings):
        logger.warning("Live plugins path missing or not writable — skip sync")
        return result

    if owned_relative_paths is not None:
        result["plugins"] = sync_paths_to_live(owned_relative_paths, settings)
    else:
        protected = live_only_plugin_names(settings)
        if settings.plugins_dir.is_dir():
            rels = [
                p.name
                for p in settings.plugins_dir.iterdir()
                if p.name not in protected and not p.name.startswith(".")
            ]
            result["plugins"] = sync_paths_to_live(rels, settings)

    live_patchers = _live_patchers_root(settings)
    if live_patchers is not None and settings.patchers_dir.is_dir():
        try:
            live_patchers.mkdir(parents=True, exist_ok=True)
            result["patchers"] = _mirror_dir(settings.patchers_dir, live_patchers)
        except OSError:
            logger.warning("Live patchers path not writable — skip patcher sync")

    logger.info(
        "Live sync: %s plugin files, %s patchers; live-only left alone=%s",
        result["plugins"],
        result["patchers"],
        result["live_only_skipped"],
    )
    return result


def sync_config_tree_to_live(settings: Settings | None = None, **kwargs) -> dict[str, int]:
    owned = kwargs.get("only_relative_paths")
    return sync_managed_to_live(owned_relative_paths=owned, settings=settings)


def remove_paths_from_live(relative_plugin_paths: list[str], settings: Settings | None = None) -> int:
    """Remove from live — but never wipe a live-only root (preserves WebMap/map_data)."""
    settings = settings or get_settings()
    if not live_sync_available(settings):
        return 0
    assert settings.live_plugins_root is not None
    protected = live_only_plugin_names(settings)
    removed = 0
    for rel in relative_plugin_paths:
        top = _top_level(rel)
        # Allow removing individual package files inside live-only during update merge,
        # but refuse deleting the entire live-only root via uninstall of top-level name only
        # when rel == top-level folder name — uninstall of live-only should be explicit.
        if rel.replace("\\", "/") == top and top in protected:
            logger.info("Refusing to delete live-only plugin root: %s", top)
            continue
        target = settings.live_plugins_root / rel
        try:
            ensure_within(settings.live_plugins_root, target if target.exists() else target.parent)
        except PathEscapeError:
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
                removed += 1
            elif target.is_dir() and top not in protected:
                shutil.rmtree(target)
                removed += 1
        except OSError as exc:
            logger.warning("Could not remove live path %s: %s", target, exc)
    return removed


def merge_tree_into(src: Path, dest: Path) -> list[tuple[str, Path]]:
    """Copy package files into dest without deleting existing dest files (keeps map_data)."""
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[tuple[str, Path]] = []
    if src.is_file():
        target = dest / src.name
        ensure_within(dest, target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append((src.name, target))
        return copied
    for path in src.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        ensure_within(dest, target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append((rel, target))
    return copied


def _live_patchers_root(settings: Settings) -> Path | None:
    mounted = Path("/valheim/live-patchers")
    if mounted.parent.exists():
        return mounted
    live = settings.live_plugins_root
    if live is not None and live.name == "plugins":
        return live.parent / "patchers"
    return None


def _copy_file(src: Path, dest: Path, dest_root: Path) -> int:
    try:
        ensure_within(dest_root, dest.parent)
    except PathEscapeError:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            if dest.stat().st_mtime >= src.stat().st_mtime:
                return 0
        shutil.copy2(src, dest)
        return 1
    except OSError as exc:
        logger.warning("Failed syncing %s -> %s: %s", src, dest, exc)
        return 0


def _mirror_dir(src: Path, dest: Path) -> int:
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in src.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(src)
        count += _copy_file(path, dest / rel, dest)
    return count
