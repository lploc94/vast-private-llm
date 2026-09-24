import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.config import mask_vast_api_key
from app.db import Database
from app.inference import InferenceProxy
from app.models import ModelCatalog, QWEN38_API_MODEL_ID, QWEN38_MODEL_ID
from app.tunnel import TunnelManager
from app.vast import VastClient


def _health_check(port: int, model_id: str) -> bool:
    try:
        response = httpx.get(
            f"http://127.0.0.1:{port}/v1/models", timeout=5, trust_env=False
        )
        if response.status_code != 200:
            return False
        return any(item.get("id") == model_id for item in response.json().get("data", []))
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return False


class DeploymentService:
    def __init__(
        self,
        db: Database,
        vast: VastClient | None,
        tunnel: TunnelManager,
        proxy: InferenceProxy,
        ssh_key_path: Path,
        health_check: Callable[[int, str], bool] = _health_check,
        spawn: Callable[[Callable[[], None]], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_polls: int = 180,
        vast_api_key: str | None = None,
    ) -> None:
        self.db = db
        self.vast = vast
        self.vast_key_hint = mask_vast_api_key(vast_api_key)
        self.tunnel = tunnel
        self.proxy = proxy
        self.ssh_key_path = ssh_key_path.expanduser()
        self.health_check = health_check
        self.spawn = spawn or self._spawn_thread
        self.sleep = sleep
        self.max_polls = max_polls
        self.catalog = ModelCatalog()
        self._lock = threading.RLock()
        self._offers: dict[int, dict[str, Any]] = {}
        self._offer_context: tuple[str, int, int] | None = None
        self._stopping = threading.Event()
        self._cancel = threading.Event()
        self._worker_active = False
        self._destroy_active = False
        self._recovery_attempts = 0
        self._supervisor: threading.Thread | None = None
        self.proxy.set_availability_check(self.tunnel.alive)

    @staticmethod
    def _spawn_thread(target: Callable[[], None]) -> None:
        threading.Thread(target=target, daemon=True).start()

    def _start_worker(self, target: Callable[[], None]) -> bool:
        with self._lock:
            if self._worker_active or self._stopping.is_set():
                return False
            self._worker_active = True

        def run() -> None:
            try:
                target()
            finally:
                with self._lock:
                    self._worker_active = False

        try:
            self.spawn(run)
        except Exception:
            with self._lock:
                self._worker_active = False
            raise
        return True

    def _require_vast(self) -> VastClient:
        if self.vast is None:
            raise RuntimeError("VAST_API_KEY chưa được cấu hình")
        return self.vast

    def account_credit(self) -> float:
        return self._require_vast().show_credit()

    def configure_vast(
        self, candidate: VastClient, api_key: str, persist: Callable[[], None]
    ) -> None:
        with self._lock:
            state = self.db.deployment_state()
            busy = {"creating", "create_unknown", "provisioning", "connecting", "loading_model", "recovering", "destroying", "destroy_unknown"}
            if self._worker_active or self._destroy_active or state["phase"] in busy:
                raise ValueError("Hãy chờ thao tác với instance hoàn tất trước khi đổi Vast API key")
            instances = candidate.show_instances()
            instance_id = state["instance_id"]
            if instance_id and not any(int(item.get("id") or 0) == int(instance_id) for item in instances):
                raise ValueError("Vast API key mới không truy cập được instance đang thuê")
            persist()
            self.vast = candidate
            self.vast_key_hint = mask_vast_api_key(api_key)

    def instance_runtime(self) -> dict[str, int | None]:
        state = self.db.deployment_state()
        instance_id = state["instance_id"]
        if not instance_id:
            return {"instance_id": None, "started_at": None}
        instance = self._require_vast().show_instance(int(instance_id))
        started_at = instance.get("start_date")
        return {
            "instance_id": int(instance_id),
            "started_at": int(started_at) if started_at is not None else None,
        }

    def list_offers(self, model_id: str, min_vram_gb: int, disk_gb: int) -> list[dict[str, Any]]:
        self.catalog.validate(model_id, min_vram_gb, disk_gb)
        vast = self._require_vast()
        rows = vast.search_offers(min_vram_gb, disk_gb)
        with self._lock:
            self._offers = {int(row["id"]): row for row in rows}
            self._offer_context = (model_id, min_vram_gb, disk_gb)
        return rows

    def start_deploy(
        self, offer_id: int, model_id: str, min_vram_gb: int, disk_gb: int
    ) -> dict[str, object]:
        self.catalog.validate(model_id, min_vram_gb, disk_gb)
        self._require_vast()
        if not self.ssh_key_path.is_file():
            raise ValueError("Không tìm thấy SSH private key; cấu hình VASTLLM_SSH_KEY_PATH")
        with self._lock:
            if self._worker_active:
                raise ValueError("Worker của deployment trước chưa kết thúc")
            if self._stopping.is_set():
                raise RuntimeError("Ứng dụng đang dừng")
            if self._offer_context != (model_id, min_vram_gb, disk_gb) or offer_id not in self._offers:
                raise ValueError("Offer không thuộc danh sách vừa tải; hãy tìm máy lại")
            selected = self._offers[offer_id]
            operation_id = uuid.uuid4().hex[:16]
            state = self.db.begin_deployment(
                operation_id, offer_id, model_id, min_vram_gb, disk_gb,
                float(selected["price_hour"]),
            )
            self._cancel.clear()
            self._recovery_attempts = 0
            started = self._start_worker(
                lambda: self._create_worker(
                    offer_id, model_id, min_vram_gb, disk_gb, operation_id,
                    float(selected["price_hour"])
                )
            )
            if not started:
                self._clear_deployment("Không khởi động được worker; chưa thuê máy")
                raise RuntimeError("Không khởi động được worker deploy")
        return self.db.deployment_state()

    def _create_worker(
        self, offer_id: int, model_id: str, min_vram_gb: int,
        disk_gb: int, operation_id: str, price_hour: float,
    ) -> None:
        vast = self._require_vast()
        try:
            fresh = vast.search_offers(min_vram_gb, disk_gb)
            current = next((row for row in fresh if int(row["id"]) == offer_id), None)
            if current is None or float(current["price_hour"]) != price_hour:
                self.db.update_deployment(
                    phase="error", message="Offer đã thay đổi; hãy tìm máy lại",
                    error="stale_offer", operation_id=None,
                )
                return
            if self._stopping.is_set() or self._cancel.is_set():
                return
        except Exception as exc:
            self.db.update_deployment(
                phase="error", message="Không kiểm tra lại được offer", error=str(exc), operation_id=None
            )
            return

        try:
            instance_id = vast.create_instance(
                offer_id, disk_gb, model_id, f"vastllm-{operation_id}"
            )
        except Exception as exc:
            self.db.update_deployment(
                phase="create_unknown", message="Đang đối soát kết quả thuê máy",
                error=str(exc),
            )
            for attempt in range(6):
                if self._stopping.is_set():
                    return
                state = self.reconcile_unknown()
                if state["instance_id"] or state["error"] == "duplicate_operation_label":
                    return
                if attempt < 5:
                    self.sleep(min(2 ** (attempt + 1), 30))
            return
        self.db.update_deployment(
            phase="provisioning", instance_id=instance_id,
            message=f"Instance {instance_id} đã được tạo; chờ máy chạy", error=None,
        )
        if not self._stopping.is_set() and not self._cancel.is_set():
            self._finish_instance(instance_id, model_id)

    def reconcile_unknown(self) -> dict[str, object]:
        state = self.db.deployment_state()
        if state["instance_id"] or not state["operation_id"]:
            return state
        label = f"vastllm-{state['operation_id']}"
        try:
            matches = [
                item for item in self._require_vast().show_instances()
                if item.get("label") == label and item.get("id")
            ]
        except Exception as exc:
            return self.db.update_deployment(
                phase="create_unknown", message="Chưa đối soát được instance với Vast", error=str(exc)
            )
        if not matches:
            return self.db.update_deployment(
                phase="create_unknown", message="Chưa tìm thấy instance; sẽ đối soát lại",
            )
        if len(matches) != 1:
            return self.db.update_deployment(
                phase="create_unknown", message="Có nhiều instance cùng operation label; cần kiểm tra Vast",
                error="duplicate_operation_label",
            )
        instance_id = int(matches[0]["id"])
        self.db.update_deployment(
            phase="provisioning", instance_id=instance_id,
            message=f"Đã tìm lại instance {instance_id} theo label", error=None,
        )
        if not self._stopping.is_set() and not self._cancel.is_set():
            self._finish_instance(instance_id, str(state["model_id"]))
        return self.db.deployment_state()

    @staticmethod
    def _ssh_endpoint(instance: dict[str, Any]) -> tuple[str, int]:
        host = instance.get("ssh_host") or instance.get("public_ipaddr")
        port = instance.get("ssh_port")
        if not host or not port:
            raise RuntimeError("Vast chưa cung cấp SSH endpoint")
        return str(host), int(port)

    def _finish_instance(self, instance_id: int, model_id: str) -> None:
        try:
            if model_id != QWEN38_MODEL_ID:
                raise RuntimeError("Deployment model không hợp lệ")
            instance = None
            for _ in range(self.max_polls):
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                instance = self._require_vast().show_instance(instance_id)
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                status = str(instance.get("actual_status") or "unknown")
                if status == "running":
                    break
                if status in {"exited", "error", "destroyed"}:
                    raise RuntimeError(f"Instance dừng trong lúc khởi động: {status}")
                self.db.update_deployment(message=f"Instance {instance_id}: {status}")
                self.sleep(5)
            else:
                raise TimeoutError("Chờ instance running quá lâu")

            with self._lock:
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                self.db.update_deployment(phase="connecting", message="Đang mở SSH tunnel")
                host, ssh_port = self._ssh_endpoint(instance)
                local_port = self.tunnel.connect(host, ssh_port)
                if self._stopping.is_set() or self._cancel.is_set():
                    return
            with self._lock:
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                self.db.update_deployment(phase="loading_model", message="Đang chờ vLLM tải model")
            for _ in range(self.max_polls * 2):
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                if not self.tunnel.alive():
                    raise RuntimeError("SSH tunnel đã ngắt")
                if self.health_check(local_port, QWEN38_API_MODEL_ID):
                    with self._lock:
                        if self._stopping.is_set() or self._cancel.is_set():
                            return
                        self.proxy.activate(local_port, QWEN38_API_MODEL_ID)
                        self.db.update_deployment(phase="ready", message="Model đã sẵn sàng", error=None)
                        self._recovery_attempts = 0
                    return
                self.sleep(5)
            raise TimeoutError("vLLM chưa trả đúng model sau thời gian chờ")
        except Exception as exc:
            with self._lock:
                if self._stopping.is_set() or self._cancel.is_set():
                    return
                self.proxy.deactivate()
                self.tunnel.stop()
                self.db.update_deployment(phase="error", message="Deploy thất bại", error=str(exc))

    def _recover_worker(self) -> None:
        state = self.db.deployment_state()
        if state["instance_id"]:
            self._finish_instance(int(state["instance_id"]), str(state["model_id"]))
        elif state["operation_id"]:
            self.reconcile_unknown()

    def _clear_deployment(self, message: str = "Đã destroy instance; chưa thuê máy") -> dict[str, object]:
        return self.db.update_deployment(
            phase="idle", message=message, operation_id=None,
            instance_id=None, offer_id=None, model_id=None, min_vram_gb=None,
            disk_gb=None, price_hour=None, error=None,
        )

    def _reconcile_destroy(self) -> dict[str, object]:
        state = self.db.deployment_state()
        instance_id = int(state["instance_id"])
        try:
            instances = self._require_vast().show_instances()
        except Exception as exc:
            return self.db.update_deployment(
                phase="destroy_unknown", message="Chưa xác định được kết quả destroy trên Vast",
                error=str(exc),
            )
        if any(int(item.get("id") or 0) == instance_id for item in instances):
            return self.db.update_deployment(
                phase="destroy_error", message="Instance vẫn còn trên Vast; có thể destroy lại",
                error=None,
            )
        return self._clear_deployment()

    def resume(self) -> dict[str, object]:
        with self._lock:
            state = self.db.deployment_state()
            if state["phase"] in {"destroying", "destroy_unknown"}:
                return self._reconcile_destroy()
            if state["phase"] == "destroy_error":
                return state
            if not state["operation_id"]:
                return state
            if self._worker_active or self._stopping.is_set():
                return state
            self.proxy.deactivate()
            if state["instance_id"]:
                self.db.update_deployment(phase="recovering", message="Đang kiểm tra lại Vast và model")
            else:
                self.db.update_deployment(phase="create_unknown", message="Đang tìm instance theo operation label")
            self._recovery_attempts += 1
            self._start_worker(self._recover_worker)
        return self.db.deployment_state()

    def tick(self) -> dict[str, object]:
        with self._lock:
            state = self.db.deployment_state()
            if self._stopping.is_set() or self._cancel.is_set():
                return state
            if state["phase"] == "destroy_unknown":
                return self._reconcile_destroy()
            if state["phase"] in {"destroying", "destroy_error"}:
                return state
            if state["phase"] == "ready":
                target = self.proxy.target()
                if target and self.health_check(target[0], QWEN38_API_MODEL_ID):
                    return state
                self.proxy.deactivate()
                self.tunnel.stop()
                state = self.db.update_deployment(phase="offline", message="Mất kết nối tới model; đang thử nối lại")
            if state["operation_id"] and not self._worker_active and self._recovery_attempts < 6:
                return self.resume()
            return state

    def start_supervisor(self) -> None:
        self.resume()
        if self._supervisor is not None:
            return

        def monitor() -> None:
            while not self._stopping.wait(10):
                self.tick()

        self._supervisor = threading.Thread(target=monitor, daemon=True)
        self._supervisor.start()

    def retry_setup(self) -> dict[str, object]:
        with self._lock:
            state = self.db.deployment_state()
            if not state["operation_id"]:
                raise ValueError("Không có deployment để thiết lập lại")
            if state["phase"] in {"ready", "destroying", "destroy_unknown", "destroy_error"} or self._worker_active:
                raise ValueError("Deployment đang hoạt động hoặc đang xử lý")
            self._cancel.clear()
            self._recovery_attempts = 0
            return self.resume()

    def destroy(self) -> dict[str, object]:
        with self._lock:
            state = self.db.deployment_state()
            if not state["instance_id"]:
                raise ValueError("Chưa xác định được instance ID; hãy đối soát trên Vast")
            if self._destroy_active or state["phase"] == "destroying":
                raise ValueError("Đang destroy instance")
            instance_id = int(state["instance_id"])
            self._destroy_active = True
            self._cancel.set()
            self.proxy.deactivate()
            self.tunnel.stop()
            self.db.update_deployment(phase="destroying", message=f"Đang destroy instance {instance_id}")
        try:
            self._require_vast().destroy_instance(instance_id)
        except Exception as exc:
            with self._lock:
                self.db.update_deployment(phase="destroy_error", message="Destroy chưa được Vast xác nhận", error=str(exc))
            raise RuntimeError("Destroy chưa được Vast xác nhận; instance ID được giữ lại") from exc
        else:
            with self._lock:
                return self._clear_deployment()
        finally:
            with self._lock:
                self._destroy_active = False

    def close(self) -> None:
        self._stopping.set()
        with self._lock:
            self.proxy.deactivate()
            self.tunnel.stop()
