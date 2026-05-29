from __future__ import annotations

import pytest

import io
import json
import sqlite3
import zipfile
from pathlib import Path

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from app.ui import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "jobs.sqlite"))


def test_settings_page_exposes_contextual_profile_and_preset_editors(tmp_path: Path) -> None:
    client = _client(tmp_path)

    profiles = client.get("/settings", params={"tab": "profiles"})
    presets = client.get("/settings", params={"tab": "presets"})
    data = client.get("/settings", params={"tab": "data"})
    storage = client.get("/settings", params={"tab": "storage"})

    assert profiles.status_code == 200
    assert "Profile editor" in profiles.text
    assert "Create profile" in profiles.text
    assert "Profile import / export" in profiles.text
    assert presets.status_code == 200
    assert "Preset editor" in presets.text
    assert "Create preset" in presets.text
    assert "Preset import / export" in presets.text
    assert data.status_code == 200
    assert "Save / Load" in data.text
    assert "Load database snapshot" in data.text
    assert storage.status_code == 200
    assert "Storage" in storage.text
    assert "Database path" in storage.text


def test_settings_create_profile_and_preset_in_runtime_config(tmp_path: Path) -> None:
    client = _client(tmp_path)

    profile_response = client.post(
        "/settings/profiles/new",
        data={"profile_id": "backend_test", "name": "Backend Test", "source_profile": "profiles/default.json"},
        follow_redirects=True,
    )
    preset_response = client.post(
        "/settings/presets/new",
        data={"preset_id": "strict_test", "name": "Strict Test", "source_preset": "balanced"},
        follow_redirects=True,
    )

    assert profile_response.status_code == 200
    assert "Profile editor" in profile_response.text
    assert Path("profiles/backend_test.json").exists()
    assert preset_response.status_code == 200
    assert "Preset editor" in preset_response.text
    assert Path("config/scoring_presets/strict_test.json").exists()


def test_profile_import_accepts_profile_zip(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = {
        "name": "Imported Profile",
        "search_queries": {"cpp": ["C++ engineer"]},
        "signals": {"interests": {"items": [{"term": "C++", "weight": 1.0}]}},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("profiles/imported_profile.json", json.dumps(payload))
    buffer.seek(0)

    response = client.post(
        "/settings/profiles/import",
        files={"profile_config_file": ("profiles.zip", buffer, "application/zip")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Profiles imported" in response.text
    assert Path("profiles/imported_profile.json").exists()


def test_preset_import_accepts_preset_zip(tmp_path: Path) -> None:
    client = _client(tmp_path)
    source = json.loads(Path("config/scoring_presets/balanced.json").read_text(encoding="utf-8"))
    source["id"] = "imported_preset"
    source["name"] = "Imported Preset"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("config/scoring_presets/imported_preset.json", json.dumps(source))
    buffer.seek(0)

    response = client.post(
        "/settings/presets/import",
        files={"preset_config_file": ("presets.zip", buffer, "application/zip")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Presets imported" in response.text
    assert Path("config/scoring_presets/imported_preset.json").exists()


def test_database_export_and_import_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('saved')")
    client = TestClient(create_app(db_path))

    export_response = client.get("/settings/database/export")
    assert export_response.status_code == 200

    imported = tmp_path / "imported.sqlite"
    imported.write_bytes(export_response.content)
    response = client.post(
        "/settings/database/import",
        files={"database_file": ("snapshot.sqlite", imported.read_bytes(), "application/octet-stream")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Database loaded" in response.text
