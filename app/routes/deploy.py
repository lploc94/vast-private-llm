from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.db import AdminSession
from app.deploy import DeploymentService


class DeployRequest(BaseModel):
    offer_id: int
    model_id: str
    min_vram_gb: int
    disk_gb: int


class DestroyRequest(BaseModel):
    confirm: bool


def register_deploy_routes(
    app: FastAPI,
    service: DeploymentService,
    require_admin: Callable[..., AdminSession],
    require_csrf: Callable[..., AdminSession],
) -> None:
    @app.get("/api/admin/models")
    def models(_session: AdminSession = Depends(require_admin)) -> dict[str, object]:
        return {"presets": service.catalog.presets()}

    @app.get("/api/admin/instance-runtime")
    def instance_runtime(_session: AdminSession = Depends(require_admin)) -> dict[str, int | None]:
        try:
            return service.instance_runtime()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/admin/offers")
    def offers(
        model_id: str, min_vram_gb: int, disk_gb: int,
        _session: AdminSession = Depends(require_admin),
    ) -> dict[str, object]:
        try:
            return {"offers": service.list_offers(model_id, min_vram_gb, disk_gb)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/admin/deploy", status_code=202)
    def deploy(payload: DeployRequest, _session: AdminSession = Depends(require_csrf)) -> dict[str, object]:
        try:
            return service.start_deploy(
                payload.offer_id, payload.model_id, payload.min_vram_gb, payload.disk_gb
            )
        except ValueError as exc:
            status = 409 if "Đã có deployment" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/admin/retry", status_code=202)
    def retry(_session: AdminSession = Depends(require_csrf)) -> dict[str, object]:
        try:
            return service.retry_setup()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/admin/destroy")
    def destroy(payload: DestroyRequest, _session: AdminSession = Depends(require_csrf)) -> dict[str, object]:
        if not payload.confirm:
            raise HTTPException(status_code=400, detail="Cần xác nhận destroy instance")
        try:
            return service.destroy()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
