"""Profile / r2z / share-code regression tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.services import profiles as profile_service
from app.services import r2_profile
from app.services.r2_profile import ProfileModSpec

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", bepinex_root=tmp_path / "bepinex", live_plugins_root=None)
    settings.data_dir.mkdir(parents=True)
    settings.bepinex_root.mkdir(parents=True)
    (settings.bepinex_root / "plugins").mkdir(parents=True, exist_ok=True)
    (settings.bepinex_root / "config").mkdir(parents=True, exist_ok=True)
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _patch_settings(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr("app.services.profiles.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)


def test_parse_gale_default_r2z():
    data = (FIXTURES / "gale_default.r2z").read_bytes()
    parsed = r2_profile.parse_r2z_bytes(data)
    assert parsed.name == "Default"
    assert len(parsed.mods) == 21
    assert any(m.source == "hexium" for m in parsed.mods)
    assert any(m.source == "thunderstore" for m in parsed.mods)
    assert parsed.config_files
    assert all(not p.startswith("cache/") for p in parsed.config_files)
    assert all("/plugins/" not in p for p in parsed.config_files)


def test_parse_r2modman_default_r2z():
    data = (FIXTURES / "r2modman_default.r2z").read_bytes()
    parsed = r2_profile.parse_r2z_bytes(data)
    assert parsed.name == "Default"
    assert len(parsed.mods) == 11
    assert all(m.source == "thunderstore" for m in parsed.mods)
    assert parsed.config_files
    assert any(p.startswith("config/") for p in parsed.config_files)
    # Directory zip members must not become fake file keys
    assert "config/RecipePinner_Data" not in parsed.config_files
    assert r2_profile.normalize_config_member("config/RecipePinner_Data/") is None


def test_sample_thunderstore_and_hexium_roundtrip():
    for name in ("sample_thunderstore.r2z", "sample_hexium.r2z"):
        parsed = r2_profile.parse_r2z_bytes((FIXTURES / name).read_bytes())
        assert parsed.mods
        rebuilt = r2_profile.build_r2z_bytes(
            name=parsed.name,
            mods=parsed.mods,
            config_files=parsed.config_files,
            mode="full" if "hexium" in name else "thunderstore",
        )
        again = r2_profile.parse_r2z_bytes(rebuilt)
        assert [m.full_name for m in again.mods] == [m.full_name for m in parsed.mods]


def test_sample_code_txt_decodes_to_same_zip():
    for stem in ("sample_thunderstore", "sample_hexium"):
        blob = (FIXTURES / f"{stem}.code.txt").read_text(encoding="utf-8")
        decoded = r2_profile.decode_share_blob(blob)
        assert decoded[:2] == b"PK"
        from_file = (FIXTURES / f"{stem}.r2z").read_bytes()
        assert decoded == from_file


@pytest.mark.asyncio
async def test_resolve_import_bytes_from_code_blob():
    blob = (FIXTURES / "sample_hexium.code.txt").read_text(encoding="utf-8")
    raw = await r2_profile.resolve_import_bytes(code=blob)
    parsed = r2_profile.parse_r2z_bytes(raw)
    assert any(m.source == "hexium" for m in parsed.mods)


@pytest.mark.asyncio
async def test_fetch_share_code_tries_both_hosts(monkeypatch):
    calls: list[str] = []

    class FakeResp:
        def __init__(self, status_code: int, text: str = ""):
            self.status_code = status_code
            self.text = text

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url: str):
            calls.append(url)
            if "thunderstore.io" in url:
                return FakeResp(403)
            if "hexium.gg" in url:
                zip_bytes = (FIXTURES / "sample_hexium.r2z").read_bytes()
                return FakeResp(200, r2_profile.encode_share_blob(zip_bytes))
            return FakeResp(404)

    monkeypatch.setattr(r2_profile.httpx, "AsyncClient", FakeClient)
    data, backend = await r2_profile.fetch_share_code("dd75b6e4-2483-b55d-4b1b-f96ac943eb48")
    assert backend == "hexium"
    assert data[:2] == b"PK"
    assert any("thunderstore.io" in u for u in calls)


def test_has_hexium_exclusive():
    mods = [
        ProfileModSpec("SonicDM-NearbyCraftingForked", "1.5.3", True, "hexium"),
        ProfileModSpec("ValheimModding-Jotunn", "2.30.2", True, "thunderstore"),
    ]
    assert r2_profile.has_hexium_exclusive(
        mods, thunderstore_has_version=lambda fn, ver: False
    )
    assert not r2_profile.has_hexium_exclusive(
        mods, thunderstore_has_version=lambda fn, ver: True
    )


def test_normalize_config_ignores_plugins_cache_and_dirs():
    assert r2_profile.normalize_config_member("BepInEx/config/foo.cfg") == "config/foo.cfg"
    assert r2_profile.normalize_config_member("config/foo.cfg") == "config/foo.cfg"
    assert r2_profile.normalize_config_member("BepInEx/cache/x.json") is None
    assert r2_profile.normalize_config_member("BepInEx/plugins/A/lang.json") is None
    assert r2_profile.normalize_config_member("config/RecipePinner_Data/") is None


def _r2z_with_dir_entry() -> bytes:
    """Minimal r2modman-style zip: empty dir member + child file (the 500 bug)."""
    manifest = """profileName: DirBug
mods:
  - name: ValheimModding-Jotunn
    version: { major: 2, minor: 30, patch: 2 }
    enabled: true
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("export.r2x", manifest)
        zf.writestr("config/RecipePinner_Data/", b"")
        zf.writestr("config/RecipePinner_Data/player.txt", b"portal=1\n")
        zf.writestr("config/foo.cfg", b"[General]\nEnabled=true\n")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_r2z_with_directory_entries_stores_configs(tmp_path: Path, monkeypatch):
    """Regression: writing zip dir members as files broke mkdir for nested configs."""
    settings = _settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    db = _session(tmp_path)

    row = await profile_service.import_profile(
        db,
        file_bytes=_r2z_with_dir_entry(),
        name="DirBugImport",
        include_configs=True,
    )
    assert row.name == "DirBugImport"
    stored = profile_service._load_stored_configs(row.id)
    assert "config/foo.cfg" in stored
    assert "config/RecipePinner_Data/player.txt" in stored
    assert "config/RecipePinner_Data" not in stored
    assert not (settings.data_dir / "profiles" / str(row.id) / "configs" / "config" / "RecipePinner_Data").is_file()
    assert (
        settings.data_dir / "profiles" / str(row.id) / "configs" / "config" / "RecipePinner_Data" / "player.txt"
    ).is_file()


@pytest.mark.asyncio
async def test_import_r2modman_fixture_persists_configs(tmp_path: Path, monkeypatch):
    """Full import path for the real r2modman Default.r2z (must not 500)."""
    settings = _settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    db = _session(tmp_path)

    data = (FIXTURES / "r2modman_default.r2z").read_bytes()
    row = await profile_service.import_profile(
        db, file_bytes=data, name="R2FixtureImport", include_configs=True
    )
    mods = profile_service._mods_from_json(row.mods_json)
    assert len(mods) == 10  # BepInExPack skipped
    assert all(m.source == "thunderstore" for m in mods)
    stored = profile_service._load_stored_configs(row.id)
    assert stored
    assert "config/RecipePinner_Data" not in stored
    assert any(p.startswith("config/") for p in stored)


@pytest.mark.asyncio
async def test_import_gale_fixture_persists_configs(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_settings(monkeypatch, settings)
    db = _session(tmp_path)

    data = (FIXTURES / "gale_default.r2z").read_bytes()
    row = await profile_service.import_profile(
        db, file_bytes=data, name="GaleFixtureImport", include_configs=True
    )
    mods = profile_service._mods_from_json(row.mods_json)
    assert len(mods) == 20  # BepInExPack skipped
    assert any(m.source == "hexium" for m in mods)
    stored = profile_service._load_stored_configs(row.id)
    assert stored
    assert all(not p.startswith("cache/") for p in stored)
    assert all("/plugins/" not in p for p in stored)
