from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.config_editor import parse_bepinex_cfg, structured_to_raw
from app.services.paths import PathEscapeError, safe_join, validate_archive_member
from app.services.scanner import scan_plugins
from app.config import Settings


def test_safe_join_rejects_traversal(tmp_path: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        safe_join(root, "..", "etc", "passwd")
    with pytest.raises(PathEscapeError):
        safe_join(root, "foo/../../etc/passwd")


def test_safe_join_allows_nested(tmp_path: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    result = safe_join(root, "ValheimModding-Jotunn", "Jotunn.dll")
    assert result == root / "ValheimModding-Jotunn" / "Jotunn.dll"


def test_validate_archive_member_rejects_traversal(tmp_path: Path):
    with pytest.raises(PathEscapeError):
        validate_archive_member("../evil.dll", tmp_path)
    with pytest.raises(PathEscapeError):
        validate_archive_member("/abs/evil.dll", tmp_path)


def test_scanner_reads_manifest(tmp_path: Path):
    plugins = tmp_path / "plugins"
    pkg = plugins / "ValheimModding-Jotunn"
    pkg.mkdir(parents=True)
    (pkg / "manifest.json").write_text(
        json.dumps(
            {
                "name": "Jotunn",
                "version_number": "2.30.2",
                "description": "Library",
                "dependencies": ["denikson-BepInExPack_Valheim-5.4.2333"],
            }
        ),
        encoding="utf-8",
    )
    (pkg / "Jotunn.dll").write_bytes(b"MZ")  # not a real DLL; metadata will be empty
    (tmp_path / "BepInEx.cfg").write_text("[Core]\n", encoding="utf-8")

    settings = Settings(
        data_dir=tmp_path / "data",
        bepinex_root=tmp_path,
        live_plugins_root=None,
    )
    results = scan_plugins(settings)
    assert len(results) == 1
    assert results[0].name == "Jotunn"
    assert results[0].version == "2.30.2"
    assert results[0].managed is True
    assert "denikson-BepInExPack_Valheim-5.4.2333" in results[0].dependencies


def test_scanner_loose_dll(tmp_path: Path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "ServerDevcommands.dll").write_bytes(b"MZ")
    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)
    results = scan_plugins(settings)
    assert any(r.full_name == "ServerDevcommands" and r.managed is False for r in results)


def test_match_installed_to_remote_by_full_name(tmp_path, monkeypatch):
    from app.services import packages as pkgmod
    from app.services.packages import PackageInfo, PackageVersion, match_installed_to_remote

    jotunn = PackageInfo(
        source="thunderstore",
        name="Jotunn",
        full_name="ValheimModding-Jotunn",
        owner="ValheimModding",
        versions=[PackageVersion(version_number="2.30.2", download_url="http://x")],
    )
    monkeypatch.setattr(pkgmod, "enabled_sources", lambda db: ["thunderstore", "hexium"])
    monkeypatch.setattr(
        pkgmod,
        "get_cached_packages",
        lambda source: {"ValheimModding-Jotunn": jotunn} if source == "thunderstore" else {},
    )
    matched = match_installed_to_remote(
        db=None,  # type: ignore[arg-type]
        full_name="ValheimModding-Jotunn",
        name="Jotunn",
        version="2.30.2",
    )
    assert matched is not None
    assert matched[0] == "thunderstore"
    assert matched[1].full_name == "ValheimModding-Jotunn"


def test_config_parse_and_roundtrip():
    raw = """## Settings file
[General]

## Enable feature
# Setting type: Boolean
# Default value: true
Enabled = true

## Max distance
# Setting type: Single
Distance = 25
"""
    structured = parse_bepinex_cfg(raw)
    assert structured["sections"][0]["name"] == "General"
    entries = structured["sections"][0]["entries"]
    assert entries[0]["key"] == "Enabled"
    assert entries[0]["value_type"] == "bool"
    assert entries[1]["value_type"] == "float"
    structured["sections"][0]["entries"][0]["value"] = "false"
    updated = structured_to_raw(raw, structured)
    assert "Enabled = false" in updated
    assert "Distance = 25" in updated


def test_live_sync_available_never_creates_dirs(tmp_path: Path):
    from app.services.live_sync import live_sync_available

    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)
    assert live_sync_available(settings) is False
    assert not (tmp_path / "plugins").exists()
    assert not (tmp_path / ".persistent").exists()


def test_make_persistent_creates_symlink(tmp_path: Path):
    from app.config import VALHEIM_CONFIG_BEPINEX
    from app.services.live_sync import is_persistent_link, make_normal, make_persistent

    plugins = tmp_path / "plugins"
    pkg = plugins / "HugeMapMod"
    pkg.mkdir(parents=True)
    (pkg / "Map.dll").write_bytes(b"MZ")
    (pkg / "tiles").mkdir()
    (pkg / "tiles" / "a.png").write_bytes(b"png")

    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)
    try:
        dest = make_persistent("HugeMapMod", settings)
    except OSError as exc:
        pytest.skip(f"symlinks not allowed on this host: {exc}")

    link = plugins / "HugeMapMod"
    assert dest == tmp_path / ".persistent" / "HugeMapMod"
    assert dest.is_dir()
    assert (dest / "Map.dll").is_file()
    assert link.is_symlink()
    assert is_persistent_link(link, settings)
    assert link.readlink() == VALHEIM_CONFIG_BEPINEX / ".persistent" / "HugeMapMod"

    restored = make_normal("HugeMapMod", settings)
    assert restored == plugins / "HugeMapMod"
    assert restored.is_dir()
    assert not restored.is_symlink()
    assert (restored / "Map.dll").is_file()
    assert not (tmp_path / ".persistent" / "HugeMapMod").exists()


def test_restart_server_stop_bootstrap_start(monkeypatch):
    from app.services import supervisor as sup

    calls: list[tuple] = []
    states = {
        "valheim-server": "RUNNING",
        "valheim-bootstrap": "EXITED",
    }

    class FakeProxy:
        class supervisor:  # noqa: N801
            @staticmethod
            def getProcessInfo(program: str):
                return {"statename": states[program]}

            @staticmethod
            def stopProcess(program: str):
                calls.append(("stop", program))
                states[program] = "STOPPED"

            @staticmethod
            def startProcess(program: str):
                calls.append(("start", program))
                if program == "valheim-bootstrap":
                    states[program] = "EXITED"
                else:
                    states[program] = "RUNNING"

    monkeypatch.setattr(sup, "_proxy", lambda db: FakeProxy())
    monkeypatch.setattr(sup, "get_setting", lambda db, key, default=None: default)

    result = sup.restart_server(db=None)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["synced"] is True
    assert calls == [
        ("stop", "valheim-server"),
        ("start", "valheim-bootstrap"),
        ("start", "valheim-server"),
    ]


def test_category_include_exclude_filters():
    from app.services.packages import PackageInfo, _matches_category_filters

    server = PackageInfo(
        source="thunderstore",
        name="WebMap",
        full_name="a-WebMap",
        owner="a",
        categories=["Server-side", "Mods"],
    )
    client = PackageInfo(
        source="thunderstore",
        name="UI",
        full_name="a-UI",
        owner="a",
        categories=["Client-side"],
    )
    both = PackageInfo(
        source="hexium",
        name="QoL",
        full_name="a-QoL",
        owner="a",
        categories=["Client & Server", "Quality of Life"],
    )

    assert _matches_category_filters(server, include=["Server-side"], exclude=[])
    assert not _matches_category_filters(client, include=["Server-side"], exclude=[])
    assert _matches_category_filters(both, include=["Client & Server", "Server-side"], exclude=[])
    assert not _matches_category_filters(server, include=[], exclude=["Server-side"])
    assert _matches_category_filters(client, include=[], exclude=["Server-side"])
    assert _matches_category_filters(server, include=[], exclude=[])


def test_job_scan_plugins_runs_persist_scan(monkeypatch):
    from app.services import updates as updates_mod

    called: list[object] = []

    class FakeSession:
        def close(self):
            called.append("close")

    monkeypatch.setattr(updates_mod, "get_session", lambda: FakeSession())
    monkeypatch.setattr(
        updates_mod,
        "persist_scan",
        lambda db: (called.append(("scan", db)) or ([], [])),
    )

    updates_mod.job_scan_plugins()
    assert called[0][0] == "scan"
    assert called[-1] == "close"


def test_safe_extract_recovers_when_file_blocks_directory(tmp_path: Path):
    import zipfile
    from app.services.installer import _safe_extract

    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # File where a directory should be, then nested file under that path
        zf.writestr("plugins/Mod/vendor/leaflet", b"not-a-dir")
        zf.writestr("plugins/Mod/vendor/leaflet/leaflet.js", b"console.log(1)")

    dest = tmp_path / "out"
    # Pre-seed the blocking file the same way a bad prior extract would
    blocking = dest / "plugins" / "Mod" / "vendor" / "leaflet"
    blocking.parent.mkdir(parents=True)
    blocking.write_bytes(b"stale")

    extracted = _safe_extract(zip_path, dest)
    assert (dest / "plugins" / "Mod" / "vendor" / "leaflet").is_dir()
    assert (dest / "plugins" / "Mod" / "vendor" / "leaflet" / "leaflet.js").read_bytes() == b"console.log(1)"
    assert extracted
    from app.models import InstalledPackage
    from app.services.scanner import package_present_on_disk

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)

    missing = InstalledPackage(
        source="thunderstore",
        full_name="Author-WebMap",
        name="WebMap",
        install_path=str(plugins / "Author-WebMap"),
        managed=True,
    )
    assert package_present_on_disk(missing, settings) is False

    (plugins / "Author-WebMap").mkdir()
    (plugins / "Author-WebMap" / "WebMap.dll").write_bytes(b"MZ")
    assert package_present_on_disk(missing, settings) is True


def test_persist_scan_prunes_missing_managed(tmp_path: Path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import InstalledPackage
    from app.services import scanner as scanner_mod

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path, live_plugins_root=None)
    settings.data_dir.mkdir(parents=True)

    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()

    stale = InstalledPackage(
        source="thunderstore",
        full_name="Author-WebMap",
        name="WebMap",
        install_path=str(plugins / "Author-WebMap"),
        managed=True,
        enabled=True,
    )
    present_dir = plugins / "KeepMe"
    present_dir.mkdir()
    (present_dir / "manifest.json").write_text(
        json.dumps({"name": "KeepMe", "version_number": "1.0.0"}),
        encoding="utf-8",
    )
    (present_dir / "KeepMe.dll").write_bytes(b"MZ")
    keep = InstalledPackage(
        source="thunderstore",
        full_name="KeepMe",
        name="KeepMe",
        install_path=str(present_dir),
        managed=True,
        enabled=True,
    )
    db.add_all([stale, keep])
    db.commit()

    monkeypatch.setattr(scanner_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(scanner_mod, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(scanner_mod, "log_activity", lambda *a, **k: None)

    result, pruned = scanner_mod.persist_scan(db, settings)
    names = {p.full_name for p in db.query(InstalledPackage).all()}
    assert "Author-WebMap" not in names
    assert "KeepMe" in names
    assert "Author-WebMap" in pruned
    assert any(p.full_name == "KeepMe" for p in result)
    db.close()
