from pathlib import Path
import threading

import pytest

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.deploy import DeploymentService
from app.inference import InferenceProxy
from app.main import create_app
from tests.test_deploy import QWEN38, FakeTunnel, FakeVast, service


MODEL = QWEN38


def test_resume_unknown_create_waits_for_label_then_recovers_without_create(tmp_path: Path) -> None:
    vast = FakeVast()
    db = Database(tmp_path)
    db.begin_deployment("lostop", 123, MODEL, 80, 120, 0.55)
    deploy, _db, proxy, tunnel = service(tmp_path, vast)
    deploy.resume()
    assert db.deployment_state()["phase"] == "create_unknown"
    assert proxy.target() is None
    assert vast.create_calls == 0

    vast.instances = [{"id": 456, "label": "vastllm-lostop"}]
    deploy.tick()
    assert db.deployment_state()["phase"] == "ready"
    assert tunnel.connected
    assert vast.create_calls == 0


def test_restart_rechecks_tunnel_and_model_before_ready(tmp_path: Path) -> None:
    vast = FakeVast()
    db = Database(tmp_path)
    db.begin_deployment("existing", 123, MODEL, 80, 120, 0.55)
    db.update_deployment(phase="ready", instance_id=456)
    deploy, _db, proxy, tunnel = service(tmp_path, vast, health_ok=False)
    deploy.resume()
    assert db.deployment_state()["phase"] != "ready"
    assert proxy.target() is None
    assert tunnel.stopped
    assert vast.create_calls == 0


def test_app_startup_resumes_saved_instance_without_new_rental(tmp_path: Path) -> None:
    vast = FakeVast()
    db = Database(tmp_path)
    db.begin_deployment("saved", 123, MODEL, 80, 120, 0.55)
    db.update_deployment(phase="ready", instance_id=456)
    key_path = tmp_path / "ssh_key"
    key_path.write_text("test key")
    tunnel = FakeTunnel()
    app = create_app(
        Settings(data_dir=tmp_path, admin_password="a sufficiently long password", vast_api_key="fake", ssh_key_path=key_path),
        vast_client=vast, tunnel_manager=tunnel, health_check=lambda _port, _model: True,
        spawn_worker=lambda target: target(),
    )
    assert app.state.proxy.target() is None
    with TestClient(app):
        assert db.deployment_state()["phase"] == "ready"
        assert app.state.proxy.target() == (18000, "qwen-3.8")
        assert vast.create_calls == 0


def test_vast_disconnection_keeps_api_offline_then_reconnects(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, tunnel = service(tmp_path, vast)
    deploy.list_offers(MODEL, 80, 120)
    assert deploy.start_deploy(123, MODEL, 80, 120)["phase"] == "ready"
    original_show = vast.show_instance

    def unavailable(_instance_id: int):
        raise RuntimeError("Vast unavailable")

    vast.show_instance = unavailable
    tunnel.stopped = True
    assert proxy.target() is None
    deploy.tick()
    assert db.deployment_state()["phase"] == "error"
    assert proxy.target() is None
    vast.show_instance = original_show
    deploy.tick()
    assert db.deployment_state()["phase"] == "ready"
    assert proxy.target() == (18000, "qwen-3.8")


def test_ssh_drop_returns_503_then_recovers(tmp_path: Path) -> None:
    vast = FakeVast()
    tunnel = FakeTunnel()
    key_path = tmp_path / "ssh_key"
    key_path.write_text("test key")
    app = create_app(
        Settings(data_dir=tmp_path, admin_password="a sufficiently long password", vast_api_key="fake", ssh_key_path=key_path),
        vast_client=vast, tunnel_manager=tunnel, health_check=lambda _port, _model: True,
        spawn_worker=lambda target: target(),
    )
    with TestClient(app) as client:
        csrf = client.post("/api/admin/login", json={"password": "a sufficiently long password"}).json()["csrf_token"]
        key = client.post("/api/admin/keys", json={"user": "alice"}, headers={"X-CSRF-Token": csrf}).json()["key"]
        app.state.deployment.list_offers(MODEL, 80, 120)
        app.state.deployment.start_deploy(123, MODEL, 80, 120)
        assert app.state.db.deployment_state()["phase"] == "ready"
        tunnel.stopped = True
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 503
        app.state.deployment.tick()
        assert app.state.db.deployment_state()["phase"] == "ready"
        assert tunnel.alive()
        assert app.state.proxy.target() == (18000, "qwen-3.8")


def test_destroy_requires_confirmation_and_preserves_id_on_vast_failure(tmp_path: Path) -> None:
    vast = FakeVast()
    key_path = tmp_path / "ssh_key"
    key_path.write_text("test key")
    app = create_app(
        Settings(data_dir=tmp_path, admin_password="a sufficiently long password", vast_api_key="fake", ssh_key_path=key_path),
        vast_client=vast, tunnel_manager=FakeTunnel(), health_check=lambda _port, _model: True,
        spawn_worker=lambda target: target(),
    )
    with TestClient(app) as client:
        csrf = client.post("/api/admin/login", json={"password": "a sufficiently long password"}).json()["csrf_token"]
        key = client.post("/api/admin/keys", json={"user": "alice"}, headers={"X-CSRF-Token": csrf}).json()["key"]
        app.state.deployment.list_offers(MODEL, 80, 120)
        app.state.deployment.start_deploy(123, MODEL, 80, 120)
        headers = {"X-CSRF-Token": csrf}
        assert client.post("/api/admin/destroy", json={"confirm": False}, headers=headers).status_code == 400
        assert app.state.db.deployment_state()["instance_id"] == 456

        vast.destroy_error = True
        failed = client.post("/api/admin/destroy", json={"confirm": True}, headers=headers)
        assert failed.status_code == 503
        assert app.state.db.deployment_state()["instance_id"] == 456
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 503

        vast.destroy_error = False
        succeeded = client.post("/api/admin/destroy", json={"confirm": True}, headers=headers)
        assert succeeded.status_code == 200
        assert succeeded.json()["instance_id"] is None
        assert app.state.keys.authenticate(key)
        app.state.deployment.list_offers(MODEL, 80, 120)
        app.state.deployment.start_deploy(123, MODEL, 80, 120)
        assert app.state.db.deployment_state()["phase"] == "ready"
        assert client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code != 401


def test_retry_setup_reuses_existing_instance(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, tunnel = service(tmp_path, vast, health_ok=False)
    deploy.list_offers(MODEL, 80, 120)
    assert deploy.start_deploy(123, MODEL, 80, 120)["phase"] == "error"
    assert vast.create_calls == 1
    deploy.health_check = lambda _port, _model: True
    assert deploy.retry_setup()["phase"] == "ready"
    assert vast.create_calls == 1
    assert proxy.target() == (18000, "qwen-3.8")


def test_new_deploy_waits_for_canceled_old_worker(tmp_path: Path) -> None:
    vast = FakeVast()
    entered = threading.Event()
    release = threading.Event()
    workers = []
    deploy, db, proxy, tunnel = service(tmp_path, vast)

    def health(_port: int, _model: str) -> bool:
        if not release.is_set():
            entered.set()
            assert release.wait(5)
        return True

    def spawn(target):
        worker = threading.Thread(target=target)
        workers.append(worker)
        worker.start()

    deploy.health_check = health
    deploy.spawn = spawn
    deploy.list_offers(MODEL, 80, 120)
    deploy.start_deploy(123, MODEL, 80, 120)
    assert entered.wait(5)
    assert deploy.destroy()["phase"] == "idle"
    deploy.list_offers(MODEL, 80, 120)
    with pytest.raises(ValueError, match="Worker"):
        deploy.start_deploy(123, MODEL, 80, 120)
    assert vast.create_calls == 1
    assert db.deployment_state()["operation_id"] is None
    release.set()
    workers[0].join(5)
    assert not workers[0].is_alive()
    deploy.start_deploy(123, MODEL, 80, 120)
    workers[1].join(5)
    assert vast.create_calls == 2
    assert db.deployment_state()["phase"] == "ready"


def test_restart_mid_destroy_reconciles_without_another_destroy(tmp_path: Path) -> None:
    vast = FakeVast()
    db = Database(tmp_path)
    db.begin_deployment("destroyop", 123, MODEL, 80, 120, 0.55)
    db.update_deployment(phase="destroying", instance_id=456)
    vast.instances = [{"id": 456, "label": "vastllm-destroyop"}]
    deploy, _db, proxy, tunnel = service(tmp_path, vast)
    deploy.resume()
    assert db.deployment_state()["phase"] == "destroy_error"
    assert db.deployment_state()["instance_id"] == 456
    assert vast.destroy_calls == 0
    assert proxy.target() is None
    assert deploy.destroy()["phase"] == "idle"
    assert vast.destroy_calls == 1


def test_restart_mid_destroy_clears_id_only_when_vast_lists_it_absent(tmp_path: Path) -> None:
    vast = FakeVast()
    db = Database(tmp_path)
    db.begin_deployment("destroyop", 123, MODEL, 80, 120, 0.55)
    db.update_deployment(phase="destroying", instance_id=456)
    deploy, _db, proxy, tunnel = service(tmp_path, vast)
    deploy.resume()
    assert db.deployment_state()["phase"] == "idle"
    assert db.deployment_state()["instance_id"] is None
    assert vast.destroy_calls == 0
