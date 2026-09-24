from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_dashboard_starts_without_vast_credentials_and_creates_local_session(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, admin_password="correct horse battery staple"))

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/admin/state").status_code == 401
        page = client.get("/")
        assert page.status_code == 200
        assert "Deploy" in page.text
        assert "API Keys" in page.text
        assert "Settings" in page.text
        assert client.get("/login", follow_redirects=False).status_code == 303
        csrf = client.get("/api/admin/session").json()["csrf_token"]
        assert client.get("/api/admin/state").json()["phase"] == "idle"
        presets = client.get("/api/admin/models").json()["presets"]
        assert presets == [{
            "id": "huihui-ai/Huihui-Qwen3.8-27B-abliterated",
            "min_vram_gb": 80,
            "disk_gb": 120,
        }]
        assert "custom-model" not in page.text
        assert "Qwen3-0.6B" not in page.text
        js = client.get("/static/dashboard.js").text
        assert 'fetch("/api/admin/models")' in js
        assert "min_vram_gb" in js and "disk_gb" in js

        assert client.post("/api/admin/logout").status_code == 403
        assert client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/admin/state").status_code == 401


def test_admin_and_session_survive_restart_without_bootstrap_secret(tmp_path: Path) -> None:
    first = create_app(Settings(data_dir=tmp_path, admin_password="a strong one-time password"))
    with TestClient(first) as client:
        assert client.get("/").status_code == 200
        cookies = dict(client.cookies)

    restarted = create_app(Settings(data_dir=tmp_path, admin_password=None))
    with TestClient(restarted) as client:
        client.cookies.update(cookies)
        assert client.get("/").status_code == 200
        assert client.get("/api/admin/state").json()["phase"] == "idle"
        assert client.get("/api/admin/session").status_code == 200

    assert b"a strong one-time password" not in (tmp_path / "app.db").read_bytes()


def test_missing_bootstrap_password_still_opens_local_dashboard(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, admin_password=None))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/admin/login", json={"password": "admin"}).status_code == 503
