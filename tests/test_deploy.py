from pathlib import Path
import threading

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.deploy import DeploymentService
from app.inference import InferenceProxy
from app.main import create_app


OFFER = {"id": 123, "gpu_name": "RTX PRO 6000", "gpu_ram_gb": 96.0, "price_hour": 0.55, "disk_gb": 200}
QWEN38 = "huihui-ai/Huihui-Qwen3.8-27B-abliterated"


class FakeVast:
    def __init__(self, *, lose_response: bool = False, health_status: str = "running") -> None:
        self.lose_response = lose_response
        self.health_status = health_status
        self.create_calls = 0
        self.label = ""
        self.instances = []
        self.offers = [OFFER]
        self.destroy_error = False
        self.destroy_calls = 0
        self.search_calls = 0

    def search_offers(self, min_vram_gb: int, disk_gb: int):
        self.search_calls += 1
        return list(self.offers)

    def create_instance(self, offer_id: int, disk_gb: int, model_id: str, label: str) -> int:
        self.create_calls += 1
        self.label = label
        self.instances = [{"id": 456, "label": label}]
        if self.lose_response:
            raise TimeoutError("response lost")
        return 456

    def show_instances(self):
        return list(self.instances)

    def show_instance(self, instance_id: int):
        return {"id": instance_id, "actual_status": self.health_status, "ssh_host": "ssh.vast.ai", "ssh_port": 40000}

    def destroy_instance(self, instance_id: int) -> None:
        self.destroy_calls += 1
        if self.destroy_error:
            raise RuntimeError("Vast unavailable")
        self.instances = []


class FakeTunnel:
    def __init__(self) -> None:
        self.connected = False
        self.stopped = False

    def connect(self, host: str, port: int) -> int:
        assert (host, port) == ("ssh.vast.ai", 40000)
        self.connected = True
        self.stopped = False
        return 18000

    def stop(self) -> None:
        self.stopped = True

    def alive(self) -> bool:
        return self.connected and not self.stopped


def service(tmp_path: Path, vast: FakeVast, health_ok: bool = True) -> tuple[DeploymentService, Database, InferenceProxy, FakeTunnel]:
    db = Database(tmp_path)
    proxy = InferenceProxy()
    tunnel = FakeTunnel()
    key_path = tmp_path / "ssh_key"
    key_path.write_text("test key")
    deploy = DeploymentService(
        db=db, vast=vast, tunnel=tunnel, proxy=proxy, ssh_key_path=key_path,
        health_check=lambda _port, _model: health_ok,
        spawn=lambda target: target(), sleep=lambda _seconds: None, max_polls=2,
    )
    return deploy, db, proxy, tunnel


def test_deploy_rechecks_offer_and_only_ready_after_model_health(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, tunnel = service(tmp_path, vast)
    assert deploy.list_offers(QWEN38, 80, 120)[0]["id"] == 123
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "ready"
    assert state["instance_id"] == 456
    assert vast.create_calls == 1
    assert tunnel.connected
    assert proxy.target() == (18000, "qwen-3.8")
    assert db.deployment_state()["operation_id"]


def test_qwen38_deploy_rejects_small_resources_before_rental(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, tunnel = service(tmp_path, vast)
    for vram, disk in ((8, 120), (80, 40)):
        try:
            deploy.list_offers(QWEN38, vram, disk)
            raise AssertionError("underprovisioned model accepted")
        except ValueError:
            pass
    assert vast.create_calls == 0
    deploy.list_offers(QWEN38, 80, 120)
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "ready"
    assert vast.search_calls == 2
    assert proxy.target() == (18000, "qwen-3.8")


def test_other_model_is_rejected_before_search_or_rental(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, tunnel = service(tmp_path, vast)
    for other in ("Qwen/Qwen3-0.6B", "other/custom-model"):
        try:
            deploy.list_offers(other, 80, 120)
            raise AssertionError("other model was accepted")
        except ValueError:
            pass
    assert vast.search_calls == 0
    assert vast.create_calls == 0


def test_stale_offer_and_wrong_model_health_do_not_mark_ready(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, _tunnel = service(tmp_path, vast, health_ok=False)
    deploy.list_offers(QWEN38, 80, 120)
    vast.offers = []
    assert deploy.start_deploy(123, QWEN38, 80, 120)["phase"] == "error"
    assert vast.create_calls == 0
    assert proxy.target() is None

    vast.offers = [OFFER]
    deploy.list_offers(QWEN38, 80, 120)
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "error"
    assert state["instance_id"] == 456
    assert proxy.target() is None


def test_lost_create_response_recovers_by_label_without_second_rental(tmp_path: Path) -> None:
    vast = FakeVast(lose_response=True)
    deploy, db, proxy, _tunnel = service(tmp_path, vast)
    deploy.list_offers(QWEN38, 80, 120)
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "ready"
    assert state["instance_id"] == 456
    assert vast.create_calls == 1
    assert vast.label == "vastllm-" + state["operation_id"]


def test_uncertain_create_stays_blocked_until_label_appears(tmp_path: Path) -> None:
    vast = FakeVast(lose_response=True)
    deploy, db, proxy, _tunnel = service(tmp_path, vast)
    deploy.list_offers(QWEN38, 80, 120)
    original_show = vast.show_instances
    vast.show_instances = lambda: []
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "create_unknown"
    assert vast.create_calls == 1
    try:
        deploy.start_deploy(123, QWEN38, 80, 120)
        raise AssertionError("second create was allowed")
    except ValueError:
        pass
    assert vast.create_calls == 1
    vast.show_instances = original_show
    assert deploy.reconcile_unknown()["instance_id"] == 456
    assert vast.create_calls == 1


def test_uncertain_create_rechecks_until_label_is_visible(tmp_path: Path) -> None:
    vast = FakeVast(lose_response=True)
    deploy, db, proxy, _tunnel = service(tmp_path, vast)
    deploy.list_offers(QWEN38, 80, 120)
    original_show = vast.show_instances
    calls = 0

    def delayed_show():
        nonlocal calls
        calls += 1
        return [] if calls < 3 else original_show()

    vast.show_instances = delayed_show
    state = deploy.start_deploy(123, QWEN38, 80, 120)
    assert state["phase"] == "ready"
    assert calls == 3
    assert vast.create_calls == 1


def test_restart_before_instance_id_commit_finds_labeled_instance(tmp_path: Path) -> None:
    vast = FakeVast()
    deploy, db, proxy, _tunnel = service(tmp_path, vast)
    db.begin_deployment("op123", 123, QWEN38, 80, 120, 0.55)
    vast.instances = [{"id": 456, "label": "vastllm-op123"}]
    restarted, _db, restarted_proxy, _tunnel = service(tmp_path, vast)
    state = restarted.reconcile_unknown()
    assert state["instance_id"] == 456
    assert state["phase"] == "ready"
    assert vast.create_calls == 0


def test_deploy_tab_and_admin_routes_use_same_service(tmp_path: Path) -> None:
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
        page = client.get("/").text
        assert "deploy-model" in page
        assert "custom-model" not in page
        assert client.get("/api/admin/models").json()["presets"][0]["id"] == QWEN38
        assert "Tìm máy" in page
        offers = client.get("/api/admin/offers?model_id=huihui-ai/Huihui-Qwen3.8-27B-abliterated&min_vram_gb=80&disk_gb=120")
        assert offers.status_code == 200
        assert offers.json()["offers"][0]["id"] == 123
        assert client.get("/api/admin/offers?model_id=Qwen/Qwen3-0.6B&min_vram_gb=80&disk_gb=120").status_code == 400
        assert client.post(
            "/api/admin/deploy",
            json={"offer_id": 123, "model_id": "other/custom-model", "min_vram_gb": 80, "disk_gb": 120},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400
        assert vast.create_calls == 0
        assert client.post(
            "/api/admin/deploy",
            json={"offer_id": 123, "model_id": QWEN38, "min_vram_gb": 80, "disk_gb": 120},
            headers={"X-CSRF-Token": csrf},
        ).json()["phase"] == "ready"


def test_shutdown_during_instance_poll_does_not_open_tunnel(tmp_path: Path) -> None:
    vast = FakeVast()
    entered = threading.Event()
    release = threading.Event()
    original_show = vast.show_instance

    def delayed_show(instance_id: int):
        entered.set()
        assert release.wait(5)
        return original_show(instance_id)

    vast.show_instance = delayed_show
    deploy, _db, proxy, tunnel = service(tmp_path, vast)
    worker = []

    def spawn(target):
        thread = threading.Thread(target=target)
        worker.append(thread)
        thread.start()

    deploy.spawn = spawn
    deploy.list_offers(QWEN38, 80, 120)
    deploy.start_deploy(123, QWEN38, 80, 120)
    assert entered.wait(5)
    deploy.close()
    release.set()
    worker[0].join(5)
    assert not worker[0].is_alive()
    assert not tunnel.connected
    assert proxy.target() is None
