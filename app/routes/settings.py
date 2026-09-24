from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.config import save_vast_api_key
from app.db import AdminSession
from app.deploy import DeploymentService
from app.vast import VastClient


class VastKeyRequest(BaseModel):
    api_key: str


def register_settings_routes(
    app: FastAPI,
    service: DeploymentService,
    data_dir: Path,
    require_admin: Callable[..., AdminSession],
    require_csrf: Callable[..., AdminSession],
) -> None:
    @app.get("/api/admin/settings")
    def get_settings(_session: AdminSession = Depends(require_admin)) -> dict[str, bool | str | None]:
        return {
            "vast_api_key_configured": service.vast is not None,
            "vast_api_key_saved": (data_dir / "vast_api_key").is_file(),
            "vast_api_key_hint": service.vast_key_hint,
        }

    @app.get("/api/admin/vast-credit")
    def get_vast_credit(_session: AdminSession = Depends(require_admin)) -> dict[str, float]:
        try:
            return {"credit_usd": service.account_credit()}
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.put("/api/admin/settings/vast-api-key")
    def set_vast_api_key(
        payload: VastKeyRequest, _session: AdminSession = Depends(require_csrf)
    ) -> dict[str, bool | str | None]:
        api_key = payload.api_key.strip()
        if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
            raise HTTPException(status_code=400, detail="Vast API key không hợp lệ")
        try:
            candidate = VastClient(api_key=api_key)
            service.configure_vast(candidate, api_key, lambda: save_vast_api_key(data_dir, api_key))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Không lưu được Vast API key") from exc
        return {
            "vast_api_key_configured": True,
            "vast_api_key_saved": True,
            "vast_api_key_hint": service.vast_key_hint,
        }
