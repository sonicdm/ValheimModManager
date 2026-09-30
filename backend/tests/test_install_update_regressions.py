"""Regression tests for install/update/scan failures that bit production.

Covers:
- Updating Team-Mod packages must reuse an existing short-name folder (PortalAtlas)
- Scan auto-link must not UNIQUE-crash when two folders match one store package
- Manual /api/updates/apply never restarts; surfaces per-package errors
- Source-scoped package lookup (thunderstore vs hexium same full_name)
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import InstalledPackage, PendingUpdate, Setting
from app.services.packages import PackageInfo, PackageVersion


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)
    settings.data_dir.mkdir(parents=True)
    settings.plugins_dir.mkdir(parents=True, exist_ok=True)
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _write_plugin_zip(path: Path, *, dll_name: str = "PortalAtlas.dll", version: str = "1.2.6") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "PortalAtlas",
                    "version_number": version,
                    "description": "test",
                    "dependencies": [],
                }
            ),
        )
        zf.writestr(dll_name, b"MZ" + b"\x00" * 64)
        zf.writestr("README.md", f"v{version}\n")
    path.write_bytes(buf.getvalue())
    return path


def _portal_remote(*, version: str = "1.2.6") -> PackageInfo:
    return PackageInfo(
        source="thunderstore",
        name="PortalAtlas",
        full_name="SonicDM-PortalAtlas",
        owner="SonicDM",
        versions=[
            PackageVersion(
                version_number=version,
                download_url=f"https://example.test/SonicDM-PortalAtlas-{version}.zip",
                description="test",
            )
        ],
        downloads=100,
        categories=["Server-side"],
    )


def test_resolve_dest_reuses_short_name_and_persistent(tmp_path: Path):
    from app.services.installer import _resolve_dest

    settings = _settings(tmp_path)
    short = settings.plugins_dir / "PortalAtlas"
    short.mkdir()
    existing = InstalledPackage(
        source="thunderstore",
        full_name="SonicDM-PortalAtlas",
        name="PortalAtlas",
        install_path=str(short),
        managed=True,
        enabled=True,
    )
    dest, root, live = _resolve_dest(
        settings,
        {"full_name": "SonicDM-PortalAtlas", "name": "PortalAtlas"},
        existing,
    )
    assert dest == short
    assert root == settings.plugins_dir
    assert live is False

    # Fresh install (no existing) still uses Team-Mod folder name
    dest2, _, _ = _resolve_dest(
        settings,
        {"full_name": "SonicDM-PortalAtlas", "name": "PortalAtlas"},
        None,
    )
    assert dest2 == settings.plugins_dir / "SonicDM-PortalAtlas"


@pytest.mark.asyncio
async def test_install_update_does_not_create_second_portal_folder(tmp_path: Path, monkeypatch):
    """Classic bug: DB path plugins/PortalAtlas, full_name SonicDM-PortalAtlas → duplicate dir."""
    from app.services import installer as installer_mod
    from app.services import packages as pkgmod

    settings = _settings(tmp_path)
    old = settings.plugins_dir / "PortalAtlas"
    old.mkdir()
    (old / "PortalAtlas.dll").write_bytes(b"MZ-old")
    (old / "manifest.json").write_text(
        json.dumps({"name": "PortalAtlas", "version_number": "1.2.5"}),
        encoding="utf-8",
    )

    db = _session(tmp_path)
    db.add(
        InstalledPackage(
            source="thunderstore",
            full_name="SonicDM-PortalAtlas",
            name="PortalAtlas",
            owner="SonicDM",
            version="1.2.5",
            install_path=str(old),
            managed=True,
            enabled=True,
        )
    )
    db.commit()

    remote = _portal_remote(version="1.2.6")
    zip_path = settings.downloads_dir / "SonicDM-PortalAtlas-1.2.6.zip"
    _write_plugin_zip(zip_path, version="1.2.6")

    async def fake_download(url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zip_path.read_bytes())
        return dest

    monkeypatch.setattr(installer_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(installer_mod, "download_package", fake_download)
    monkeypatch.setattr(installer_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(installer_mod, "sync_config_tree_to_live", lambda **k: None)
    monkeypatch.setattr(pkgmod, "get_cached_packages", lambda source: {remote.full_name: remote})
    monkeypatch.setattr(pkgmod, "enabled_sources", lambda db: ["thunderstore"])
    monkeypatch.setattr(pkgmod, "get_package", lambda source, full_name: remote if full_name == remote.full_name else None)
    monkeypatch.setattr(pkgmod, "find_package_any_source", lambda db, dep: None)

    result = await installer_mod.install_packages(db, "thunderstore", "SonicDM-PortalAtlas", "1.2.6")
    assert len(result) == 1
    pkg = result[0]
    assert pkg.version == "1.2.6"
    assert Path(pkg.install_path) == old
    assert old.is_dir()
    assert (old / "README.md").read_text(encoding="utf-8").startswith("v1.2.6")
    # Must not leave a Team-Mod sibling folder behind
    assert not (settings.plugins_dir / "SonicDM-PortalAtlas").exists()
    assert len([p for p in settings.plugins_dir.iterdir() if p.is_dir()]) == 1
    db.close()


@pytest.mark.asyncio
async def test_install_looks_up_existing_by_source_not_just_full_name(tmp_path: Path, monkeypatch):
    """Thunderstore and Hexium rows can share a full_name; update the correct one."""
    from app.services import installer as installer_mod
    from app.services import packages as pkgmod

    settings = _settings(tmp_path)
    ts_dir = settings.plugins_dir / "PortalAtlas-TS"
    hx_dir = settings.plugins_dir / "PortalAtlas-HX"
    ts_dir.mkdir()
    hx_dir.mkdir()
    (ts_dir / "PortalAtlas.dll").write_bytes(b"MZ-ts")
    (hx_dir / "PortalAtlas.dll").write_bytes(b"MZ-hx")

    db = _session(tmp_path)
    db.add_all(
        [
            InstalledPackage(
                source="thunderstore",
                full_name="SonicDM-PortalAtlas",
                name="PortalAtlas",
                version="1.2.5",
                install_path=str(ts_dir),
                managed=True,
                enabled=True,
            ),
            InstalledPackage(
                source="hexium",
                full_name="SonicDM-PortalAtlas",
                name="PortalAtlas",
                version="1.2.5",
                install_path=str(hx_dir),
                managed=True,
                enabled=True,
            ),
        ]
    )
    db.commit()

    remote = PackageInfo(
        source="hexium",
        name="PortalAtlas",
        full_name="SonicDM-PortalAtlas",
        owner="SonicDM",
        versions=[
            PackageVersion(version_number="1.2.6", download_url="https://example.test/hx.zip"),
        ],
    )
    zip_path = settings.downloads_dir / "hx.zip"
    _write_plugin_zip(zip_path, version="1.2.6")

    async def fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(zip_path.read_bytes())
        return dest

    monkeypatch.setattr(installer_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(installer_mod, "download_package", fake_download)
    monkeypatch.setattr(installer_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(installer_mod, "sync_config_tree_to_live", lambda **k: None)
    monkeypatch.setattr(pkgmod, "get_cached_packages", lambda source: {remote.full_name: remote} if source == "hexium" else {})
    monkeypatch.setattr(pkgmod, "enabled_sources", lambda db: ["hexium"])
    monkeypatch.setattr(pkgmod, "get_package", lambda source, full_name: remote if source == "hexium" else None)
    monkeypatch.setattr(pkgmod, "find_package_any_source", lambda db, dep: None)

    result = await installer_mod.install_packages(db, "hexium", "SonicDM-PortalAtlas", "1.2.6")
    assert result[0].source == "hexium"
    assert Path(result[0].install_path) == hx_dir
    # Thunderstore row untouched
    ts = (
        db.query(InstalledPackage)
        .filter(InstalledPackage.source == "thunderstore", InstalledPackage.full_name == "SonicDM-PortalAtlas")
        .one()
    )
    assert ts.version == "1.2.5"
    assert Path(ts.install_path) == ts_dir
    assert not (settings.plugins_dir / "SonicDM-PortalAtlas").exists()
    db.close()


def test_persist_scan_two_folders_one_store_id_no_integrity_error(tmp_path: Path, monkeypatch):
    from app.services import packages as pkgmod
    from app.services import scanner as scanner_mod

    settings = _settings(tmp_path)
    for folder in ("PortalAtlas", "SonicDM-PortalAtlas"):
        d = settings.plugins_dir / folder
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "PortalAtlas", "version_number": "1.2.5"}),
            encoding="utf-8",
        )
        (d / "PortalAtlas.dll").write_bytes(b"MZ")

    remote = _portal_remote(version="1.2.6")
    db = _session(tmp_path)

    monkeypatch.setattr(scanner_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(scanner_mod, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(scanner_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(
        scanner_mod,
        "read_bepinex_plugin_metadata",
        lambda *_a, **_k: type(
            "M", (), {"guid": "sonicdm.valheimportallist", "name": "PortalAtlas", "version": "1.2.5"}
        )(),
    )
    monkeypatch.setattr(pkgmod, "match_installed_to_remote", lambda *a, **k: ("thunderstore", remote))

    result, pruned = scanner_mod.persist_scan(db, settings)
    assert pruned == []
    assert len(result) == 2
    rows = db.query(InstalledPackage).all()
    assert len(rows) == 2
    linked = [r for r in rows if r.source == "thunderstore" and r.full_name == "SonicDM-PortalAtlas"]
    assert len(linked) == 1
    other = [r for r in rows if r not in linked][0]
    assert other.source in ("local", "unmanaged")
    # Second scan must stay stable (no IntegrityError, still one linked)
    result2, _ = scanner_mod.persist_scan(db, settings)
    assert len(result2) == 2
    assert (
        db.query(InstalledPackage)
        .filter(InstalledPackage.source == "thunderstore", InstalledPackage.full_name == "SonicDM-PortalAtlas")
        .count()
        == 1
    )
    db.close()


@pytest.mark.asyncio
async def test_check_for_updates_queues_and_apply_reports_partial_failure(tmp_path: Path, monkeypatch):
    from app.services import updates as updates_mod
    from app.services import packages as pkgmod
    from app.services import installer as installer_mod
    from app.api import routes as routes_mod
    from app.models import AdminUser

    settings = _settings(tmp_path)
    db = _session(tmp_path)

    web = settings.plugins_dir / "f00d4tehg0dz-ValheimWebMap"
    portal = settings.plugins_dir / "PortalAtlas"
    web.mkdir()
    portal.mkdir()
    db.add_all(
        [
            InstalledPackage(
                source="thunderstore",
                full_name="f00d4tehg0dz-ValheimWebMap",
                name="WebMap",
                version="2.1.5",
                install_path=str(web),
                managed=True,
                enabled=True,
                auto_update=True,
            ),
            InstalledPackage(
                source="thunderstore",
                full_name="SonicDM-PortalAtlas",
                name="PortalAtlas",
                version="1.2.5",
                install_path=str(portal),
                managed=True,
                enabled=True,
                auto_update=True,
            ),
        ]
    )
    db.commit()

    web_remote = PackageInfo(
        source="thunderstore",
        name="WebMap",
        full_name="f00d4tehg0dz-ValheimWebMap",
        owner="f00d4tehg0dz",
        versions=[PackageVersion(version_number="2.1.6", download_url="https://example.test/web.zip")],
    )
    portal_remote = _portal_remote(version="1.2.6")
    cache = {
        web_remote.full_name: web_remote,
        portal_remote.full_name: portal_remote,
    }

    monkeypatch.setattr(pkgmod, "get_cached_packages", lambda source: cache if source == "thunderstore" else {})
    monkeypatch.setattr(pkgmod, "enabled_sources", lambda db: ["thunderstore"])
    monkeypatch.setattr(pkgmod, "get_package", lambda source, full_name: cache.get(full_name))
    monkeypatch.setattr(updates_mod, "_package_index_needs_refresh", lambda *a, **k: False)
    monkeypatch.setattr(updates_mod, "get_setting", lambda db, key, default=None: default)
    monkeypatch.setattr(updates_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(updates_mod, "set_setting", lambda *a, **k: None)

    found = await updates_mod.check_for_updates(db)
    assert {p.full_name for p in found} == {"f00d4tehg0dz-ValheimWebMap", "SonicDM-PortalAtlas"}
    assert all(p.status == "queued" for p in found)

    async def fake_install(db, source, full_name, version=None):
        if full_name == "SonicDM-PortalAtlas":
            raise ValueError("simulated PortalAtlas install failure")
        pkg = (
            db.query(InstalledPackage)
            .filter(InstalledPackage.full_name == full_name, InstalledPackage.source == source)
            .one()
        )
        pkg.version = version
        return [pkg]

    restart_calls: list[object] = []

    from app.services import settings_service as settings_mod

    monkeypatch.setattr(installer_mod, "install_packages", fake_install)
    monkeypatch.setattr(routes_mod.installer, "install_packages", fake_install)
    monkeypatch.setattr(routes_mod.backup_service, "create_backup", lambda *a, **k: None)
    monkeypatch.setattr(
        routes_mod,
        "get_setting",
        lambda db, key, default=None: False if key == "backup_before_update" else default,
    )
    monkeypatch.setattr(settings_mod, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(routes_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(routes_mod.supervisor, "supervisor_configured", lambda db: True)
    monkeypatch.setattr(
        routes_mod.supervisor,
        "restart_server",
        lambda db: restart_calls.append("restart") or {"ok": True},
    )

    # Call the route function directly with a fake user
    user = AdminUser(username="admin", password_hash="x")
    out = await routes_mod.apply_updates(user=user, db=db, body=None)

    assert restart_calls == []  # manual apply must never restart
    assert out["restart"] is None
    by_name = {r["full_name"]: r for r in out["results"]}
    assert by_name["f00d4tehg0dz-ValheimWebMap"]["ok"] is True
    assert by_name["SonicDM-PortalAtlas"]["ok"] is False
    assert "simulated PortalAtlas install failure" in by_name["SonicDM-PortalAtlas"]["error"]
    db.close()


def test_summarize_apply_includes_failure_reasons():
    """Mirror of frontend summarizeApply contract — failures must name the package + why."""
    # Keep this in sync with frontend/src/lib/updates.ts
    results = [
        {"full_name": "A-Mod", "ok": True, "version": "2.0.0"},
        {"full_name": "B-Mod", "ok": False, "error": "disk full"},
    ]
    ok_rows = [r for r in results if r["ok"]]
    fail_rows = [r for r in results if not r["ok"]]
    lines = [f"Updated {len(ok_rows)} package: {ok_rows[0]['full_name']}"]
    for r in fail_rows:
        lines.append(f"Failed {r['full_name']}: {r['error']}")
    lines.append("Restart + sync when ready")
    msg = "\n".join(lines)
    assert "Failed B-Mod: disk full" in msg
    assert "Updated 1 package: A-Mod" in msg
