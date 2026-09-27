from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PackageCacheMeta
from .settings_service import get_setting

logger = logging.getLogger(__name__)


@dataclass
class PackageVersion:
    version_number: str
    download_url: str
    dependencies: list[str] = field(default_factory=list)
    description: str | None = None
    icon: str | None = None
    date_created: str | None = None
    downloads: int = 0
    file_size: int | None = None


@dataclass
class PackageInfo:
    source: str
    name: str
    full_name: str
    owner: str
    package_url: str | None = None
    description: str | None = None
    icon_url: str | None = None
    date_updated: str | None = None
    rating_score: int = 0
    downloads: int = 0
    categories: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    versions: list[PackageVersion] = field(default_factory=list)

    @property
    def latest(self) -> PackageVersion | None:
        return self.versions[0] if self.versions else None


# In-memory caches keyed by source
_CACHE: dict[str, dict[str, PackageInfo]] = {}
_CACHE_PATH_NAME = "package_index.json"


def source_api_url(source: str) -> str:
    settings = get_settings()
    if source == "hexium":
        return settings.hexium_api
    return settings.thunderstore_api


def cache_file(source: str) -> Path:
    return get_settings().data_dir / f"cache_{source}_{_CACHE_PATH_NAME}"


def _parse_package(source: str, raw: dict[str, Any]) -> PackageInfo:
    versions: list[PackageVersion] = []
    total_downloads = 0
    for v in raw.get("versions") or []:
        downloads = int(v.get("downloads") or 0)
        total_downloads += downloads
        versions.append(
            PackageVersion(
                version_number=v.get("version_number") or "",
                download_url=v.get("download_url") or "",
                dependencies=list(v.get("dependencies") or []),
                description=v.get("description"),
                icon=v.get("icon"),
                date_created=v.get("date_created"),
                downloads=downloads,
                file_size=v.get("file_size"),
            )
        )
    latest = versions[0] if versions else None
    return PackageInfo(
        source=source,
        name=raw.get("name") or "",
        full_name=raw.get("full_name") or "",
        owner=raw.get("owner") or "",
        package_url=raw.get("package_url"),
        description=(latest.description if latest else None),
        icon_url=(latest.icon if latest else None),
        date_updated=raw.get("date_updated"),
        rating_score=int(raw.get("rating_score") or 0),
        downloads=total_downloads,
        categories=list(raw.get("categories") or []),
        is_deprecated=bool(raw.get("is_deprecated")),
        versions=versions,
    )


def _load_disk_cache(source: str) -> dict[str, PackageInfo]:
    path = cache_file(source)
    if not path.is_file():
        return {}
    try:
        raw_list = json.loads(path.read_text(encoding="utf-8"))
        return {p["full_name"]: _parse_package(source, p) for p in raw_list if p.get("full_name")}
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return {}


def _save_disk_cache(source: str, raw_list: list[dict[str, Any]]) -> None:
    path = cache_file(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw_list), encoding="utf-8")


async def refresh_source(source: str, db: Session | None = None) -> int:
    url = source_api_url(source)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        raw_list = response.json()
    if not isinstance(raw_list, list):
        raise ValueError(f"Unexpected package index shape from {source}")
    _save_disk_cache(source, raw_list)
    parsed = {p["full_name"]: _parse_package(source, p) for p in raw_list if p.get("full_name")}
    _CACHE[source] = parsed
    if db is not None:
        meta = db.get(PackageCacheMeta, source)
        if meta is None:
            meta = PackageCacheMeta(source=source, enabled=True)
            db.add(meta)
        meta.last_refreshed_at = datetime.now(timezone.utc)
        meta.package_count = len(parsed)
        db.commit()
    logger.info("Refreshed %s packages from %s", len(parsed), source)
    return len(parsed)


def get_cached_packages(source: str) -> dict[str, PackageInfo]:
    if source not in _CACHE:
        _CACHE[source] = _load_disk_cache(source)
    return _CACHE[source]


def enabled_sources(db: Session) -> list[str]:
    sources: list[str] = []
    if get_setting(db, "thunderstore_enabled", True):
        sources.append("thunderstore")
    if get_setting(db, "hexium_enabled", True):
        sources.append("hexium")
    return sources


def search_packages(
    db: Session,
    *,
    query: str = "",
    source: str | None = None,
    category: str | None = None,
    sort: str = "downloads",
    limit: int = 50,
    offset: int = 0,
) -> list[PackageInfo]:
    sources = [source] if source else enabled_sources(db)
    packages: list[PackageInfo] = []
    q = query.strip().lower()
    for src in sources:
        for pkg in get_cached_packages(src).values():
            if pkg.is_deprecated:
                continue
            if category and category not in pkg.categories:
                continue
            if q and q not in pkg.full_name.lower() and q not in (pkg.description or "").lower() and q not in pkg.owner.lower():
                continue
            packages.append(pkg)

    reverse = True
    if sort == "name":
        packages.sort(key=lambda p: p.full_name.lower(), reverse=False)
        reverse = False
    elif sort == "updated":
        packages.sort(key=lambda p: p.date_updated or "", reverse=True)
    elif sort == "rating":
        packages.sort(key=lambda p: p.rating_score, reverse=True)
    else:
        packages.sort(key=lambda p: p.downloads, reverse=True)

    if reverse and sort == "name":
        pass
    return packages[offset : offset + limit]


def get_package(source: str, full_name: str) -> PackageInfo | None:
    return get_cached_packages(source).get(full_name)


def find_package_any_source(db: Session, dependency: str) -> tuple[str, PackageInfo, PackageVersion] | None:
    """Resolve Team-Mod-Version dependency string across enabled sources."""
    parts = dependency.rsplit("-", 2)
    if len(parts) < 3:
        # Try Team-Mod without version
        full_name = dependency
        min_version = None
    else:
        owner, name, min_version = parts[0], parts[1], parts[2]
        # Heuristic: version looks like digits
        if not (min_version and min_version[0].isdigit()):
            full_name = dependency
            min_version = None
        else:
            full_name = f"{owner}-{name}"

    for src in enabled_sources(db):
        pkg = get_package(src, full_name)
        if pkg and pkg.latest:
            return src, pkg, pkg.latest
        # Also try exact full_name matches for odd names with extra dashes
        for candidate in get_cached_packages(src).values():
            if candidate.full_name == full_name or dependency.startswith(candidate.full_name + "-"):
                if candidate.latest:
                    return src, candidate, candidate.latest
    return None


def resolve_dependencies(
    db: Session,
    source: str,
    full_name: str,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """Return install plan as list of {source, full_name, version, download_url, dependencies}."""
    pkg = get_package(source, full_name)
    if pkg is None:
        raise ValueError(f"Package not found: {source}:{full_name}")
    target = None
    if version:
        target = next((v for v in pkg.versions if v.version_number == version), None)
    if target is None:
        target = pkg.latest
    if target is None:
        raise ValueError(f"No versions for {full_name}")

    plan: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(src: str, info: PackageInfo, ver: PackageVersion) -> None:
        key = f"{src}:{info.full_name}"
        if key in seen:
            return
        # Skip BepInExPack — provided by the server image
        if "BepInExPack" in info.full_name:
            return
        seen.add(key)
        for dep in ver.dependencies:
            if "BepInExPack" in dep:
                continue
            resolved = find_package_any_source(db, dep)
            if resolved:
                dsrc, dpkg, dver = resolved
                add(dsrc, dpkg, dver)
        plan.append(
            {
                "source": src,
                "full_name": info.full_name,
                "name": info.name,
                "owner": info.owner,
                "version": ver.version_number,
                "download_url": ver.download_url,
                "dependencies": ver.dependencies,
                "icon_url": ver.icon or info.icon_url,
                "package_url": info.package_url,
                "description": ver.description or info.description,
            }
        )

    add(source, pkg, target)
    return plan
