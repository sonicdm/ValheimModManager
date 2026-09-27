from __future__ import annotations

from pathlib import Path, PurePosixPath


class PathEscapeError(ValueError):
    """Raised when a path would escape an allowed root."""


def ensure_within(root: Path, candidate: Path) -> Path:
    """Resolve candidate and ensure it stays under root."""
    root_resolved = root.resolve()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    # Resolve existing parents for symlink safety
    probe = candidate
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    resolved_probe = probe.resolve()
    try:
        resolved_probe.relative_to(root_resolved)
    except ValueError as exc:
        raise PathEscapeError(f"Path escapes allowed root: {candidate}") from exc
    # Rebuild full path under root for missing files
    try:
        return candidate.resolve()
    except OSError:
        rel = candidate.relative_to(root_resolved) if str(candidate).startswith(str(root_resolved)) else None
        if rel is None:
            raise PathEscapeError(f"Path escapes allowed root: {candidate}")
        return root_resolved.joinpath(*rel.parts)


def safe_join(root: Path, *parts: str) -> Path:
    """Join path parts under root, rejecting traversal and absolute segments."""
    root_resolved = root.resolve()
    pure_parts: list[str] = []
    for part in parts:
        if not part or part == ".":
            continue
        pure = PurePosixPath(str(part).replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise PathEscapeError(f"Unsafe path segment: {part}")
        for segment in pure.parts:
            if segment in ("", "."):
                continue
            if segment == "..":
                raise PathEscapeError(f"Unsafe path segment: {part}")
            pure_parts.append(segment)
    current = root_resolved.joinpath(*pure_parts)
    try:
        current.relative_to(root_resolved)
    except ValueError as exc:
        raise PathEscapeError(f"Path escapes allowed root: {current}") from exc
    return current


def ensure_directory(path: Path) -> None:
    """Create path as a directory, removing any file that blocks the path.

    Some Thunderstore zips (or leftover staging) can leave a file where a directory
    is required; mkdir(parents=True) then raises NotADirectoryError (Errno 20).
    """
    path = Path(path)
    if path.is_dir():
        return
    if path.exists() or path.is_symlink():
        path.unlink()
    to_create: list[Path] = []
    current = path
    while True:
        parent = current.parent
        if parent == current:
            break
        if parent.is_dir():
            break
        if parent.exists() or parent.is_symlink():
            parent.unlink()
        to_create.append(parent)
        current = parent
    for parent in reversed(to_create):
        parent.mkdir(exist_ok=True)
    path.mkdir(parents=True, exist_ok=True)


def validate_archive_member(member_name: str, dest_root: Path) -> Path:
    """Validate a zip member name and return the destination path under dest_root."""
    name = member_name.replace("\\", "/")
    if name.startswith("/") or name.startswith("../") or "/../" in f"/{name}/" or name == "..":
        raise PathEscapeError(f"Archive member escapes destination: {member_name}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise PathEscapeError(f"Archive member escapes destination: {member_name}")
    return safe_join(dest_root, *pure.parts)
