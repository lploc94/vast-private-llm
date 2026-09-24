import math
import shlex
from typing import Any

from app.models import QWEN38_API_MODEL_ID, QWEN38_MODEL_ID
from app.ssh_keys import SSHKeyError, public_key_material

VLLM_IMAGE = "vllm/vllm-openai:qwen38"
QWEN38_WEIGHT_GB = 55.563006776
DECODE_BANDWIDTH_FACTOR = 0.70


class VastError(RuntimeError):
    pass


class VastClient:
    def __init__(self, api_key: str | None = None, sdk: Any | None = None) -> None:
        if sdk is None:
            if not api_key:
                raise ValueError("VAST_API_KEY chưa được cấu hình")
            from vastai import VastAI

            # The SDK counts attempts, so one sends a request without retrying create.
            sdk = VastAI(api_key=api_key, quiet=True, retry=1)
        self.sdk = sdk

    def _account_ssh_keys(self) -> list[dict[str, Any]]:
        try:
            rows = self.sdk.show_ssh_keys()
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                raise VastError("Vast API key không hợp lệ hoặc đã hết hiệu lực") from exc
            if status == 403:
                raise VastError("Vast API key cần quyền user_read để kiểm tra SSH keys") from exc
            raise VastError(f"Không đọc được SSH keys từ Vast ({type(exc).__name__})") from exc
        if not isinstance(rows, list):
            raise VastError("Vast không trả danh sách SSH keys hợp lệ")
        return rows

    def _account_ssh_key_materials(self) -> set[tuple[str, bytes]]:
        materials: set[tuple[str, bytes]] = set()
        for row in self._account_ssh_keys():
            if not isinstance(row, dict) or not isinstance(row.get("public_key"), str):
                raise VastError("Vast trả SSH key không hợp lệ")
            try:
                materials.add(public_key_material(row["public_key"]))
            except SSHKeyError as exc:
                raise VastError("Vast trả SSH key không hợp lệ") from exc
        return materials

    def ensure_ssh_key(self, public_key: str) -> None:
        target = public_key_material(public_key)
        if target in self._account_ssh_key_materials():
            return
        try:
            self.sdk.create_ssh_key(ssh_key=public_key)
        except Exception as exc:
            try:
                if target in self._account_ssh_key_materials():
                    return
            except VastError:
                pass
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                raise VastError("Vast API key không hợp lệ hoặc đã hết hiệu lực") from exc
            if status == 403:
                raise VastError("Vast API key cần quyền user_write để đăng ký SSH key") from exc
            raise VastError(f"Không đăng ký được SSH key với Vast ({type(exc).__name__})") from exc
        if target not in self._account_ssh_key_materials():
            raise VastError("Vast chưa xác nhận SSH key đã được đăng ký")

    def matching_ssh_key_id(self, public_key: str) -> int | None:
        target = public_key_material(public_key)
        matches: list[int] = []
        for row in self._account_ssh_keys():
            if not isinstance(row, dict) or not isinstance(row.get("public_key"), str):
                raise VastError("Vast trả SSH key không hợp lệ")
            try:
                material = public_key_material(row["public_key"])
            except SSHKeyError as exc:
                raise VastError("Vast trả SSH key không hợp lệ") from exc
            if material != target:
                continue
            key_id = row.get("id")
            if isinstance(key_id, bool) or not isinstance(key_id, int) or key_id <= 0:
                raise VastError("Vast trả ID SSH key không hợp lệ")
            matches.append(key_id)
        if len(matches) > 1:
            raise VastError("Có nhiều SSH key trùng nhau trên Vast; hãy kiểm tra thủ công")
        return matches[0] if matches else None

    def revoke_ssh_key(self, public_key: str) -> None:
        key_id = self.matching_ssh_key_id(public_key)
        if key_id is None:
            raise VastError("Không thấy SSH key trong tài khoản Vast hiện tại; kiểm tra API key")
        delete_error: Exception | None = None
        try:
            self.sdk.delete_ssh_key(id=key_id)
        except Exception as exc:
            delete_error = exc
        try:
            remaining = self.matching_ssh_key_id(public_key)
        except VastError as exc:
            raise VastError("Chưa xác nhận được SSH key đã bị gỡ khỏi Vast; file local được giữ lại") from exc
        if remaining is None:
            return
        if delete_error is not None:
            status = getattr(getattr(delete_error, "response", None), "status_code", None)
            if status == 401:
                raise VastError("Vast API key không hợp lệ hoặc đã hết hiệu lực") from delete_error
            if status == 403:
                raise VastError("Vast API key cần quyền user_write để gỡ SSH key") from delete_error
            raise VastError(f"Không gỡ được SSH key khỏi Vast ({type(delete_error).__name__})") from delete_error
        raise VastError("Vast chưa xác nhận SSH key đã bị gỡ; file local được giữ lại")

    def search_offers(self, min_vram_gb: int, disk_gb: int) -> list[dict[str, Any]]:
        query = (
            f"gpu_ram>={min_vram_gb} num_gpus=1 verified=true rentable=true "
            "direct_port_count>=1 reliability>=0.95 cuda_vers>=13.0"
        )
        try:
            rows = self.sdk.search_offers(
                query=query, type="on-demand", order="dph_total", limit=100, storage=disk_gb
            )
        except Exception as exc:
            raise VastError(f"Không tìm được offer Vast ({type(exc).__name__})") from exc
        if not isinstance(rows, list):
            raise VastError("Vast không trả danh sách offer")
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            if float(row.get("gpu_ram", 0) or 0) < min_vram_gb * 1000:
                continue
            if int(row.get("num_gpus", 0) or 0) != 1:
                continue
            if float(row.get("cuda_vers", row.get("cuda_max_good", 0)) or 0) < 13.0:
                continue
            if int(row.get("direct_port_count", 0) or 0) < 1:
                continue
            if row.get("rentable") is False:
                continue
            if row.get("verification") != "verified" and row.get("vericode") != 1:
                continue
            reliability = float(row.get("reliability2", row.get("reliability", 0)) or 0)
            if reliability < 0.95:
                continue
            if float(row.get("disk_space", 0) or 0) < disk_gb:
                continue
            try:
                bandwidth = float(row.get("gpu_mem_bw") or 0)
            except (TypeError, ValueError):
                bandwidth = 0
            if not math.isfinite(bandwidth) or bandwidth <= 0:
                bandwidth = 0
            price_hour = float(row.get("dph_total", 0) or 0)
            if not math.isfinite(price_hour) or price_hour <= 0:
                continue
            estimated_tps = DECODE_BANDWIDTH_FACTOR * bandwidth / QWEN38_WEIGHT_GB if bandwidth else None
            tokens_per_dollar = (
                round(estimated_tps * 3600 / price_hour)
                if estimated_tps is not None
                else None
            )
            result.append(
                {
                    "id": int(row["id"]),
                    "gpu_name": str(row.get("gpu_name") or "GPU"),
                    "gpu_ram_gb": round(float(row["gpu_ram"]) / 1000, 1),
                    "price_hour": price_hour,
                    "disk_gb": float(row.get("disk_space", 0) or 0),
                    "reliability": reliability,
                    "location": str(row.get("geolocation") or ""),
                    "gpu_mem_bw": bandwidth or None,
                    "estimated_tps": round(estimated_tps, 1) if estimated_tps is not None else None,
                    "tokens_per_dollar": tokens_per_dollar,
                }
            )
        return result

    def create_instance(self, offer_id: int, disk_gb: int, model_id: str, label: str) -> int:
        if model_id != QWEN38_MODEL_ID:
            raise ValueError("Chỉ hỗ trợ Qwen3.8-27B uncensored")
        # The model ID is also validated by the service; quoting keeps it inert in onstart.
        onstart = (
            f"mkdir -p /workspace && nohup vllm serve {shlex.quote(model_id)} --host 127.0.0.1 --port 8000 "
            f"--served-model-name {shlex.quote(QWEN38_API_MODEL_ID)} --max-num-seqs 1 --enable-prefix-caching "
            "--language-model-only --max-model-len 262144 --reasoning-parser qwen3 "
            "--enable-auto-tool-choice --tool-call-parser qwen3_xml "
            "> /workspace/vllm.log 2>&1 < /dev/null &"
        )
        try:
            result = self.sdk.create_instance(
                id=offer_id,
                image=VLLM_IMAGE,
                disk=disk_gb,
                label=label,
                ssh=True,
                direct=True,
                cancel_unavail=True,
                onstart_cmd=onstart,
            )
        except Exception as exc:
            response = getattr(exc, "response", None)
            detail = ""
            if response is not None:
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    payload = None
                if isinstance(payload, dict):
                    for name in ("msg", "message", "error", "detail", "reason"):
                        value = payload.get(name)
                        if isinstance(value, str) and value:
                            detail = value
                            break
                api_key = getattr(getattr(self.sdk, "client", None), "api_key", None)
                if api_key:
                    detail = detail.replace(api_key, "[redacted]")
                status = getattr(response, "status_code", "unknown")
                detail = f"HTTP {status}" + (f": {detail[:300]}" if detail else "")
            raise VastError(
                f"Chưa rõ kết quả tạo instance Vast ({detail or type(exc).__name__})"
            ) from exc
        if not isinstance(result, dict) or not result.get("new_contract"):
            raise VastError("Vast không xác nhận instance ID")
        return int(result["new_contract"])

    def show_instance(self, instance_id: int) -> dict[str, Any]:
        try:
            result = self.sdk.show_instance(id=instance_id)
        except Exception as exc:
            raise VastError(f"Không lấy được trạng thái instance ({type(exc).__name__})") from exc
        if not isinstance(result, dict):
            raise VastError("Không lấy được trạng thái instance")
        return result

    def show_instances(self) -> list[dict[str, Any]]:
        try:
            result = self.sdk.show_instances()
        except Exception as exc:
            raise VastError(f"Không lấy được danh sách instance ({type(exc).__name__})") from exc
        if not isinstance(result, list):
            raise VastError("Không lấy được danh sách instance")
        return [item for item in result if isinstance(item, dict)]

    def show_credit(self) -> float:
        try:
            result = self.sdk.show_user()
        except Exception as exc:
            raise VastError(f"Không lấy được credit Vast ({type(exc).__name__})") from exc
        if not isinstance(result, dict):
            raise VastError("Vast không trả thông tin tài khoản")
        value = result.get("credit")
        if isinstance(value, bool):
            raise VastError("Vast không trả credit hợp lệ")
        try:
            credit = float(value)
        except (TypeError, ValueError) as exc:
            raise VastError("Vast không trả credit hợp lệ") from exc
        if not math.isfinite(credit):
            raise VastError("Vast không trả credit hợp lệ")
        return credit

    def destroy_instance(self, instance_id: int) -> None:
        try:
            result = self.sdk.destroy_instance(id=instance_id)
        except Exception as exc:
            raise VastError(f"Không destroy được instance ({type(exc).__name__})") from exc
        if isinstance(result, dict) and result.get("success") is False:
            raise VastError("Vast không xác nhận destroy instance")
