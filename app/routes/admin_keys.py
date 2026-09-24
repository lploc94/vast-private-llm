from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.db import AdminSession
from app.keys import KeyStore


class CreateKeyRequest(BaseModel):
    user: str


def register_admin_key_routes(
    app: FastAPI,
    keys: KeyStore,
    require_admin: Callable[..., AdminSession],
    require_csrf: Callable[..., AdminSession],
) -> None:
    @app.get("/api/admin/keys")
    def list_keys(_session: AdminSession = Depends(require_admin)) -> dict[str, object]:
        return {"items": keys.list()}

    @app.post("/api/admin/keys", status_code=201)
    def create_key(payload: CreateKeyRequest, _session: AdminSession = Depends(require_csrf)) -> dict[str, object]:
        try:
            return keys.create(payload.user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/admin/keys/{key_id}")
    def revoke_key(key_id: int, _session: AdminSession = Depends(require_csrf)) -> dict[str, bool]:
        if not keys.revoke(key_id):
            raise HTTPException(status_code=404, detail="API key không tồn tại")
        return {"ok": True}
