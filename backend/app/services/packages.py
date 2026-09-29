from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bleach
import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PackageCacheMeta
from .settings_service import get_setting, log_activity

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
    website_url: str | None = None


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

# Thunderstore and Hexium use different category names for dedicated-server relevance.
SERVER_AUDIENCE_CATEGORIES = frozenset(
    {
        "Server-side",
        "Server-only",
        "Client & Server",
        "Client (& Server)",
    }
)
CLIENT_AUDIENCE_CATEGORIES = frozenset(
    {
        "Client-side",
        "Client-only",
    }
)

DEFAULT_SERVER_INCLUDE = sorted(SERVER_AUDIENCE_CATEGORIES)


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
                website_url=v.get("website_url"),
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
        log_activity(
            db,
            "package_refresh",
            source=source,
            result="ok",
            message=f"Indexed {len(parsed)} packages from {source}",
            details={"package_count": len(parsed)},
        )
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


def _matches_category_filters(
    pkg: PackageInfo,
    *,
    include: list[str] | None,
    exclude: list[str] | None,
) -> bool:
    cats = set(pkg.categories)
    include_set = {c for c in (include or []) if c}
    exclude_set = {c for c in (exclude or []) if c}
    if include_set and not (cats & include_set):
        return False
    if exclude_set and (cats & exclude_set):
        return False
    return True


def _normalize_search_text(text: str) -> str:
    """Lowercase and treat _, -, spaces as equivalent word separators."""
    t = (text or "").lower().replace("_", " ").replace("-", " ")
    return " ".join(t.split())


# "double" or 'single' quoted spans are exact (case-insensitive) substring matches.
_QUERY_QUOTE_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')


@dataclass(frozen=True)
class _ParsedQuery:
    exact: tuple[str, ...]  # lowercase phrases; _, -, spaces kept as typed
    fuzzy: tuple[str, ...]  # normalized tokens from the unquoted remainder


def _parse_query(query: str) -> _ParsedQuery:
    """Split a search string into exact quoted phrases + fuzzy unquoted tokens."""
    text = query or ""
    exact: list[str] = []
    remainder: list[str] = []
    last = 0
    for match in _QUERY_QUOTE_RE.finditer(text):
        remainder.append(text[last : match.start()])
        phrase = (match.group(1) if match.group(1) is not None else match.group(2) or "").strip()
        if phrase:
            exact.append(phrase.lower())
        last = match.end()
    remainder.append(text[last:])
    fuzzy = tuple(_normalize_search_text(" ".join(remainder)).split())
    return _ParsedQuery(exact=tuple(exact), fuzzy=fuzzy)


def _pkg_search_blob(pkg: PackageInfo) -> str:
    return " ".join([pkg.name, pkg.full_name, pkg.owner, pkg.description or ""])


def _matches_query(pkg: PackageInfo, parsed: _ParsedQuery) -> bool:
    """Quoted phrases = exact substring; remaining tokens = fuzzy (_,-/space)."""
    if not parsed.exact and not parsed.fuzzy:
        return True
    raw = _pkg_search_blob(pkg).lower()
    for phrase in parsed.exact:
        if phrase not in raw:
            return False
    if parsed.fuzzy:
        haystack = _normalize_search_text(raw)
        if not all(tok in haystack for tok in parsed.fuzzy):
            return False
    return True


def _query_relevance(pkg: PackageInfo, parsed: _ParsedQuery) -> int:
    """Lower is better. Prefer name hits over description-only matches."""
    if not parsed.exact and not parsed.fuzzy:
        return 0
    name_raw = (pkg.name or "").lower()
    full_raw = (pkg.full_name or "").lower()
    name_n = _normalize_search_text(pkg.name)
    full_n = _normalize_search_text(pkg.full_name)

    if parsed.exact and all(p in name_raw for p in parsed.exact):
        return 0
    if parsed.exact and all(p in full_raw for p in parsed.exact):
        return 1

    if parsed.fuzzy:
        phrase = " ".join(parsed.fuzzy)
        if phrase and phrase in name_n:
            return 2
        if all(tok in name_n for tok in parsed.fuzzy):
            return 3
        if phrase and phrase in full_n:
            return 4
        if all(tok in full_n for tok in parsed.fuzzy):
            return 5
    return 6


def list_categories(db: Session, *, source: str | None = None) -> list[dict[str, Any]]:
    """Return categories present in the cached indexes, with package counts."""
    sources = [source] if source else enabled_sources(db)
    counts: dict[str, int] = {}
    for src in sources:
        for pkg in get_cached_packages(src).values():
            if pkg.is_deprecated:
                continue
            for cat in pkg.categories:
                counts[cat] = counts.get(cat, 0) + 1
    return [
        {"name": name, "count": counts[name]}
        for name in sorted(counts.keys(), key=lambda n: (-counts[n], n.lower()))
    ]


def search_packages(
    db: Session,
    *,
    query: str = "",
    source: str | None = None,
    category: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    sort: str = "downloads",
    limit: int = 50,
    offset: int = 0,
) -> list[PackageInfo]:
    sources = [source] if source else enabled_sources(db)
    packages: list[PackageInfo] = []
    parsed = _parse_query(query)
    include_list = list(include or [])
    exclude_list = list(exclude or [])
    # Back-compat: single category acts as include
    if category and category.strip() and category.strip() not in include_list:
        include_list.append(category.strip())
    for src in sources:
        for pkg in get_cached_packages(src).values():
            if pkg.is_deprecated:
                continue
            if not _matches_category_filters(pkg, include=include_list, exclude=exclude_list):
                continue
            if not _matches_query(pkg, parsed):
                continue
            packages.append(pkg)

    if sort == "name":
        packages.sort(key=lambda p: p.full_name.lower())
    elif sort == "updated":
        packages.sort(key=lambda p: p.date_updated or "", reverse=True)
    elif sort == "rating":
        packages.sort(key=lambda p: p.rating_score, reverse=True)
    else:
        # downloads (default): with a query, boost name/full_name relevance first
        packages.sort(
            key=lambda p: (_query_relevance(p, parsed), -p.downloads),
        )

    return packages[offset : offset + limit]


def get_package(source: str, full_name: str) -> PackageInfo | None:
    return get_cached_packages(source).get(full_name)


def match_installed_to_remote(
    db: Session,
    *,
    full_name: str,
    name: str | None = None,
    owner: str | None = None,
    version: str | None = None,
    prefer_source: str | None = None,
) -> tuple[str, PackageInfo] | None:
    """Best-effort match of an on-disk package to Thunderstore/Hexium.

    Prefers exact Team-Mod full_name, then owner+name, then unique name match.
    If both stores have it, prefer prefer_source, else the one whose versions
    include the installed version, else the first enabled source that matches.
    """
    candidates: list[tuple[str, PackageInfo]] = []
    sources = enabled_sources(db)
    if prefer_source and prefer_source in sources:
        sources = [prefer_source] + [s for s in sources if s != prefer_source]

    for src in sources:
        cache = get_cached_packages(src)
        if full_name in cache:
            candidates.append((src, cache[full_name]))
            continue
        # Folder sometimes is Team-Mod already
        if owner and name:
            key = f"{owner}-{name}"
            if key in cache:
                candidates.append((src, cache[key]))
                continue
        # Unique match by package name
        name_matches = [p for p in cache.values() if p.name.lower() == (name or full_name).lower()]
        if len(name_matches) == 1:
            candidates.append((src, name_matches[0]))

    if not candidates:
        # Broader: name contained in full_name
        needle = (name or full_name).lower().replace(" ", "")
        for src in sources:
            fuzzy = [
                p
                for p in get_cached_packages(src).values()
                if p.name.lower().replace(" ", "") == needle
                or p.full_name.lower().endswith("-" + needle)
                or p.full_name.lower() == needle
            ]
            if len(fuzzy) == 1:
                candidates.append((src, fuzzy[0]))

    if not candidates:
        return None

    if version:
        versioned = [
            (s, p)
            for s, p in candidates
            if any(v.version_number == version for v in p.versions)
        ]
        if versioned:
            return versioned[0]

    return candidates[0]


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


EXPERIMENTAL_API_BASE = {
    "thunderstore": "https://thunderstore.io/api/experimental",
    "hexium": "https://valheim.hexium.gg/api/experimental",
}
RENDER_MARKDOWN_URL = "https://thunderstore.io/api/experimental/frontend/render-markdown/"

_DOC_ALLOWED_TAGS = list(
    {
        *bleach.sanitizer.ALLOWED_TAGS,
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "img",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
        "br",
        "p",
        "div",
        "span",
        "ul",
        "ol",
        "li",
        "blockquote",
        "strong",
        "em",
        "a",
        "del",
        "sup",
        "sub",
    }
)
_DOC_ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "img": ["src", "alt", "title", "width", "height"],
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
    "code": ["class"],
    "pre": ["class"],
    "div": ["class"],
    "span": ["class"],
}


def _docs_cache_path(source: str, full_name: str, version: str, kind: str) -> Path:
    safe_name = full_name.replace("/", "_").replace("\\", "_")
    return get_settings().data_dir / "docs_cache" / source / safe_name / version / f"{kind}.json"


def _sanitize_html(html: str) -> str:
    return bleach.clean(
        html,
        tags=_DOC_ALLOWED_TAGS,
        attributes=_DOC_ALLOWED_ATTRS,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


async def _render_markdown(markdown: str) -> str:
    if not markdown.strip():
        return ""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.post(RENDER_MARKDOWN_URL, json={"markdown": markdown})
        response.raise_for_status()
        data = response.json()
    html = data.get("html") if isinstance(data, dict) else None
    if not isinstance(html, str):
        raise ValueError("Unexpected markdown render response")
    return _sanitize_html(html)


async def fetch_package_doc(
    source: str,
    full_name: str,
    *,
    version: str,
    kind: str = "readme",
    owner: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Fetch README or changelog markdown and rendered HTML for a package version."""
    kind = kind if kind in ("readme", "changelog") else "readme"
    if source not in EXPERIMENTAL_API_BASE:
        raise ValueError(f"Unsupported source for docs: {source}")
    if not version:
        raise ValueError("version is required")

    cache = _docs_cache_path(source, full_name, version, kind)
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "html" in cached and "markdown" in cached:
                return cached
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    info = get_package(source, full_name)
    ns = owner or (info.owner if info else None)
    pkg_name = name or (info.name if info else None)
    if not ns or not pkg_name:
        # Fallback: Owner-Name (first hyphen)
        if "-" in full_name:
            ns, pkg_name = full_name.split("-", 1)
        else:
            raise ValueError("Cannot resolve package owner/name for docs")

    base = EXPERIMENTAL_API_BASE[source]
    url = f"{base}/package/{ns}/{pkg_name}/{version}/{kind}/"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
        if response.status_code == 404:
            result = {"kind": kind, "version": version, "markdown": "", "html": "", "missing": True}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(result), encoding="utf-8")
            return result
        response.raise_for_status()
        data = response.json()
    markdown = data.get("markdown") if isinstance(data, dict) else ""
    if not isinstance(markdown, str):
        markdown = ""
    html = await _render_markdown(markdown) if markdown.strip() else ""
    result = {
        "kind": kind,
        "version": version,
        "markdown": markdown,
        "html": html,
        "missing": not bool(markdown.strip()),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result
