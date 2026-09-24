import json

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.inference import InferenceProxy, api_error
from app.keys import KeyStore


MAX_CHAT_BODY = 1024 * 1024


def register_v1_routes(app: FastAPI, keys: KeyStore, proxy: InferenceProxy) -> None:
    def authenticated(request: Request) -> bool:
        auth = request.headers.get("Authorization", "")
        scheme, _, token = auth.partition(" ")
        return scheme.lower() == "bearer" and bool(token) and keys.authenticate(token)

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        if not authenticated(request):
            return api_error(401, "API key không hợp lệ", "invalid_api_key")
        return await proxy.forward("GET", "/v1/models")

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        if not authenticated(request):
            return api_error(401, "API key không hợp lệ", "invalid_api_key")
        if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_CHAT_BODY:
            return api_error(413, "Request quá lớn", "request_too_large")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_CHAT_BODY:
                return api_error(413, "Request quá lớn", "request_too_large")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return api_error(400, "JSON không hợp lệ", "invalid_json")
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
            return api_error(400, "Thiếu model", "invalid_request")
        if "stream" in payload and not isinstance(payload["stream"], bool):
            return api_error(400, "stream phải là boolean", "invalid_request")
        if "n" in payload and (type(payload["n"]) is not int or payload["n"] != 1):
            return api_error(400, "Chỉ hỗ trợ n=1 để xử lý tuần tự", "invalid_request")
        target = proxy.target()
        if target is None:
            return api_error(503, "Model chưa sẵn sàng", "model_unavailable")
        if payload["model"] != target[1]:
            return api_error(400, "Model không khớp deployment", "model_mismatch")
        return await proxy.forward(
            "POST", "/v1/chat/completions", body=bytes(body), stream=payload.get("stream") is True
        )
