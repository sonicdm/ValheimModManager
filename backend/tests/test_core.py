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
