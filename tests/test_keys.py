from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def login_admin(client: TestClient) -> str:
    response = client.post("/api/admin/login", json={"password": "a sufficiently long password"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_key_is_shown_once_and_revoke_takes_effect_immediately(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, admin_password="a sufficiently long password"))
    with TestClient(app) as client:
        assert client.get("/api/admin/keys").status_code == 401
        csrf = login_admin(client)
        assert client.post("/api/admin/keys", json={"user": "Lan"}).status_code == 403
        created = client.post(
            "/api/admin/keys", json={"user": "Lan"}, headers={"X-CSRF-Token": csrf}
        )
        assert created.status_code == 201
        key = created.json()["key"]
        assert len(key) >= 40
        key_id = created.json()["id"]
        other = client.post(
            "/api/admin/keys", json={"user": "Minh"}, headers={"X-CSRF-Token": csrf}
        )
        assert other.status_code == 201
        assert other.json()["key"] != key

        listed = client.get("/api/admin/keys").json()["items"]
        assert len(listed) == 2
        assert listed[0]["user"] == "Lan"
        assert "key" not in listed[0]
        assert key not in client.get("/api/admin/keys").text
        assert key.encode() not in (tmp_path / "app.db").read_bytes()

        # A valid key is authenticated even before an instance is deployed.
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 503
        assert client.delete(f"/api/admin/keys/{key_id}", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 401
        assert client.get("/api/admin/state").status_code == 200


def test_missing_wrong_or_revoked_key_is_rejected(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, admin_password="a sufficiently long password"))
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/api/admin/keys", json={"user": "Lan"}).status_code == 401
