from dataclasses import dataclass


QWEN38_MODEL_ID = "huihui-ai/Huihui-Qwen3.8-27B-abliterated"
QWEN38_API_MODEL_ID = "qwen-3.8"


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    min_vram_gb: int
    disk_gb: int


class ModelCatalog:
    PRESETS = ({"id": QWEN38_MODEL_ID, "min_vram_gb": 80, "disk_gb": 120},)

    def presets(self) -> list[dict[str, object]]:
        return [dict(item) for item in self.PRESETS]

    def validate(self, model_id: str, min_vram_gb: int, disk_gb: int) -> ModelSpec:
        if model_id != QWEN38_MODEL_ID:
            raise ValueError("Chỉ hỗ trợ huihui-ai/Huihui-Qwen3.8-27B-abliterated")
        if type(min_vram_gb) is not int or not 80 <= min_vram_gb <= 192:
            raise ValueError("VRAM tối thiểu phải trong khoảng 80–192 GB")
        if type(disk_gb) is not int or not 120 <= disk_gb <= 1000:
            raise ValueError("Disk phải trong khoảng 120–1000 GB")
        return ModelSpec(model_id=model_id, min_vram_gb=min_vram_gb, disk_gb=disk_gb)
