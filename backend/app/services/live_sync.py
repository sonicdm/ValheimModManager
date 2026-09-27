from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import VALHEIM_CONFIG_BEPINEX, Settings, get_settings
from .paths import PathEscapeError, ensure_directory, ensure_within


logger = logging.getLogger(__name__)


def live_sync_available(settings: Settings | None = None) -> bool:
    """Live data/bepinex mounts are no longer used. Always false; never mkdir."""
    return False


def config_plugin_names(settings: Settings | None = None) -> set[str]:
    settings = settings or get_settings()
    if not settings.plugins_dir.is_dir():
        return set()
    return {p.name for p in settings.plugins_dir.iterdir() if not p.name.startswith(".")}


def persistent_package_names(settings: Settings | None = None) -> set[str]:
    """Packages stored under .persistent (via plugins symlink and/or the folder itself)."""
    settings = settings or get_settings()
    names: set[str] = set()
    if settings.plugins_dir.is_dir():
        for entry in settings.plugins_dir.iterdir():
            if entry.name.startswith("."):
                continue
            if is_persistent_link(entry, settings):
                names.add(entry.name)
    if settings.persistent_dir.is_dir():
        for entry in settings.persistent_dir.iterdir():
            try:
                if entry.name.startswith(".") or not entry.is_dir() or entry.is_symlink():
                    continue
            except OSError:
                continue
            names.add(entry.name)
    return names


# Back-compat alias used by installer / scanner / routes.
def live_only_plugin_names(settings: Settings | None = None) -> set[str]:
    return persistent_package_names(settings)


def _top_level(relative: str) -> str:
    return relative.replace("\\", "/").split("/", 1)[0]


def persistent_symlink_target(package_name: str) -> Path:
    """Absolute path as seen inside the Valheim container."""
    return VALHEIM_CONFIG_BEPINEX / ".persistent" / package_name


def is_persistent_link(path: Path, settings: Settings | None = None) -> bool:
    """True when path is a symlink whose target is under /config/bepinex/.persistent."""
    settings = settings or get_settings()
    try:
        if not path.is_symlink():
            return False
    except OSError:
        return False
    try:
        target = path.readlink()
    except OSError:
        return False
    target_s = target.as_posix() if isinstance(target, Path) else str(target).replace("\\", "/")
    # Accept absolute game-container path or a relative link into ../.persistent/
    if target_s.startswith("/config/bepinex/.persistent/"):
        return True
    if ".persistent/" in target_s:
        return True
    # Resolved path under host persistent dir (Windows / local dev)
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(settings.persistent_dir.resolve())
    except (OSError, ValueError, AttributeError):
        try:
            resolved = path.resolve()
            resolved.relative_to(settings.persistent_dir.resolve())
            return True
        except (ValueError, OSError):
            return False


def path_is_persistent(path: Path | str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    p = Path(path)
    try:
        if is_persistent_link(p, settings):
            return True
    except OSError:
        pass
    try:
        p.resolve().relative_to(settings.persistent_dir.resolve())
        return True
    except (ValueError, OSError):
        pass
    # install_path may be the plugins symlink path
    try:
        link = settings.plugins_dir / p.name
        if link.exists() or link.is_symlink():
            return is_persistent_link(link, settings)
    except OSError:
        pass
    return False


def path_is_under_live(path: Path | str, settings: Settings | None = None) -> bool:
    """True for persistent packages (badge / live_only API field)."""
    return path_is_persistent(path, settings)


def resolve_live_only_folder(
    settings: Settings,
    *,
    full_name: str,
    name: str | None = None,
) -> Path | None:
    """If this package is already persistent, return its .persistent folder."""
    candidates = [full_name]
    if name:
        candidates.append(name)
        candidates.append(name.replace(" ", ""))
    if "-" in full_name:
        candidates.append(full_name.split("-", 1)[-1])

    for cand in candidates:
        if not cand:
            continue
        link = settings.plugins_dir / cand
        if is_persistent_link(link, settings):
            dest = settings.persistent_dir / cand
            if dest.exists():
                return dest
            # Symlink may resolve via /config/bepinex mount
            try:
                resolved = link.resolve()
                if resolved.exists():
                    return resolved
            except OSError:
                return dest
        persistent = settings.persistent_dir / cand
        if persistent.is_dir() and not persistent.is_symlink():
            return persistent
    return None


def ensure_persistent_symlink(package_name: str, settings: Settings | None = None) -> Path:
    """Ensure plugins/<name> → /config/bepinex/.persistent/<name>. Returns persistent dir."""
    settings = settings or get_settings()
    if "/" in package_name or "\\" in package_name or package_name in (".", ".."):
        raise ValueError("Invalid package name")
    persistent = settings.persistent_dir / package_name
    settings.persistent_dir.mkdir(parents=True, exist_ok=True)
    persistent.mkdir(parents=True, exist_ok=True)
    link = settings.plugins_dir / package_name
    settings.plugins_dir.mkdir(parents=True, exist_ok=True)
    target = persistent_symlink_target(package_name)
    if link.exists() or link.is_symlink():
        if is_persistent_link(link, settings):
            return persistent
        raise FileExistsError(f"plugins/{package_name} already exists and is not a persistent symlink")
    link.symlink_to(target, target_is_directory=True)
    return persistent


def make_persistent(package_name: str, settings: Settings | None = None) -> Path:
    """Move plugins/<name> → .persistent/<name> and leave a symlink in plugins."""
    settings = settings or get_settings()
    if "/" in package_name or "\\" in package_name or package_name in (".", ".."):
        raise ValueError("Invalid package name")
    src = settings.plugins_dir / package_name
    if not src.exists() and not src.is_symlink():
        raise FileNotFoundError(f"plugins/{package_name} not found")
    if is_persistent_link(src, settings):
        return settings.persistent_dir / package_name

    settings.persistent_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.persistent_dir / package_name
    if dest.exists():
        raise FileExistsError(f".persistent/{package_name} already exists")

    # Move real tree, then create symlink with game-container absolute target.
    shutil.move(str(src), str(dest))
    target = persistent_symlink_target(package_name)
    link = settings.plugins_dir / package_name
    link.symlink_to(target, target_is_directory=True)
    logger.info("Made %s persistent → %s (link %s)", package_name, dest, target)
    return dest


def make_normal(package_name: str, settings: Settings | None = None) -> Path:
    """Move .persistent/<name> back to plugins/<name> (remove symlink)."""
    settings = settings or get_settings()
    if "/" in package_name or "\\" in package_name or package_name in (".", ".."):
        raise ValueError("Invalid package name")
    link = settings.plugins_dir / package_name
    dest = settings.persistent_dir / package_name
    if not is_persistent_link(link, settings) and not dest.is_dir():
        raise FileNotFoundError(f"{package_name} is not a persistent package")

    if link.is_symlink() or link.exists():
        link.unlink()
    if not dest.exists():
        raise FileNotFoundError(f".persistent/{package_name} not found")
    shutil.move(str(dest), str(link))
    logger.info("Made %s normal → %s", package_name, link)
    return link


def sync_paths_to_live(relative_paths: list[str], settings: Settings | None = None) -> int:
    return 0


def sync_managed_to_live(
    owned_relative_paths: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    return {"plugins": 0, "patchers": 0, "live_only_skipped": sorted(persistent_package_names(settings))}


def sync_config_tree_to_live(settings: Settings | None = None, **kwargs) -> dict[str, int]:
    return sync_managed_to_live(settings=settings)


def remove_paths_from_live(relative_plugin_paths: list[str], settings: Settings | None = None) -> int:
    return 0


def merge_tree_into(src: Path, dest: Path) -> list[tuple[str, Path]]:
    """Copy package files into dest without deleting existing dest files (keeps runtime data)."""
    ensure_directory(dest)
    copied: list[tuple[str, Path]] = []
    if src.is_file():
        target = dest / src.name
        ensure_within(dest, target.parent)
        ensure_directory(target.parent)
        shutil.copy2(src, target)
        copied.append((src.name, target))
        return copied
    for path in src.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        ensure_within(dest, target.parent)
        ensure_directory(target.parent)
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
        shutil.copy2(path, target)
        copied.append((rel, target))
    return copied
