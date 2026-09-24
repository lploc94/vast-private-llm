import sys
from types import SimpleNamespace

from app.models import ModelCatalog
from app.vast import VLLM_IMAGE, VastClient


OFFER = {
    "id": 123,
    "gpu_name": "RTX PRO 6000",
    "gpu_ram": 96000,
    "dph_total": 0.55,
    "disk_space": 200,
    "reliability": 0.99,
    "direct_port_count": 2,
    "cuda_vers": 13.0,
    "verification": "verified",
    "rentable": True,
    "num_gpus": 1,
}

QWEN38 = "huihui-ai/Huihui-Qwen3.8-27B-abliterated"


class FakeSDK:
    def __init__(self) -> None:
        self.search_kwargs = None
        self.create_kwargs = None

    def search_offers(self, **kwargs):
        self.search_kwargs = kwargs
        return [
            OFFER,
            {**OFFER, "id": 124, "gpu_ram": 4096},
            {**OFFER, "id": 125, "reliability": 0.5},
            {**OFFER, "id": 126, "verification": "unverified"},
            {**OFFER, "id": 127, "disk_space": 10},
        ]

    def create_instance(self, **kwargs):
        self.create_kwargs = kwargs
        return {"new_contract": 456, "success": True}

    def show_instance(self, id):
        return {"id": id, "actual_status": "running", "ssh_host": "ssh.vast.ai", "ssh_port": 40000}

    def show_instances(self):
        return []


def test_model_catalog_validates_public_model_id_and_resources() -> None:
    catalog = ModelCatalog()
    assert catalog.presets() == [{"id": QWEN38, "min_vram_gb": 80, "disk_gb": 120}]
    spec = catalog.validate(QWEN38, 80, 120)
    assert spec.model_id == QWEN38
    for vram, disk in ((8, 120), (80, 40)):
        try:
            catalog.validate(QWEN38, vram, disk)
            raise AssertionError("accepted underprovisioned Qwen3.8")
        except ValueError:
            pass
    for bad in ("", "../evil", "foo; touch /tmp/evil", "https://example.com/model", "Qwen/Qwen3-0.6B", "other/custom-model"):
        try:
            catalog.validate(bad, 80, 120)
            raise AssertionError(f"accepted invalid model {bad!r}")
        except ValueError:
            pass


def test_vast_sdk_search_filters_and_private_vllm_create() -> None:
    sdk = FakeSDK()
    vast = VastClient(sdk=sdk)
    offers = vast.search_offers(min_vram_gb=80, disk_gb=120)
    assert [offer["id"] for offer in offers] == [123]
    assert offers[0]["price_hour"] == 0.55
    assert "gpu_ram>=80" in sdk.search_kwargs["query"]
    assert offers[0]["gpu_ram_gb"] == 96.0
    assert "cuda_vers>=13.0" in sdk.search_kwargs["query"]
    assert sdk.search_kwargs["type"] == "on-demand"

    instance_id = vast.create_instance(123, 120, QWEN38, "vastllm-op123")
    assert instance_id == 456
    assert sdk.create_kwargs["image"] == VLLM_IMAGE
    assert sdk.create_kwargs["ssh"] is True
    assert sdk.create_kwargs["direct"] is True
    assert sdk.create_kwargs["label"] == "vastllm-op123"
    assert "--host 127.0.0.1 --port 8000" in sdk.create_kwargs["onstart_cmd"]
    assert "--language-model-only" in sdk.create_kwargs["onstart_cmd"]
    assert "--max-model-len 262144" in sdk.create_kwargs["onstart_cmd"]
    assert "--served-model-name qwen-3.8 --max-num-seqs 1" in sdk.create_kwargs["onstart_cmd"]
    assert "--reasoning-parser qwen3" in sdk.create_kwargs["onstart_cmd"]
    assert "-p 8000" not in str(sdk.create_kwargs)
    assert vast.show_instance(456)["ssh_port"] == 40000


def test_vast_sdk_makes_exactly_one_request_attempt(monkeypatch) -> None:
    seen = {}

    def fake_vastai(**kwargs):
        seen.update(kwargs)
        return FakeSDK()

    monkeypatch.setitem(sys.modules, "vastai", SimpleNamespace(VastAI=fake_vastai))
    VastClient(api_key="fake")
    assert seen["retry"] == 1


def test_qwen38_filters_out_cuda129_even_when_sdk_returns_it() -> None:
    sdk = FakeSDK()

    def qwen_search(**kwargs):
        sdk.search_kwargs = kwargs
        return [
            {**OFFER, "gpu_ram": 96000, "cuda_vers": 13.0, "disk_space": 200},
            {**OFFER, "id": 124, "gpu_ram": 96000, "cuda_vers": 12.9, "disk_space": 200},
        ]

    sdk.search_offers = qwen_search
    vast = VastClient(sdk=sdk)
    offers = vast.search_offers(80, 120)
    assert [item["id"] for item in offers] == [123]
    assert "cuda_vers>=13.0" in sdk.search_kwargs["query"]
