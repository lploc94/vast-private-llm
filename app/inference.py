import threading

from collections.abc import Awaitable, Callable

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse


def api_error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "api_error", "code": code}},
    )


class ClosingStreamingResponse(StreamingResponse):
    def __init__(self, *args, on_close: Callable[[], Awaitable[None]], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.on_close()


class InferenceProxy:
    def __init__(self, client: httpx.AsyncClient | None = None, stream_read_timeout: float = 300) -> None:
        self.client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None
        self.stream_read_timeout = stream_read_timeout
        self._lock = threading.Lock()
        self._port: int | None = None
        self._model_id: str | None = None
        self._availability_check: Callable[[], bool] = lambda: True

    def set_availability_check(self, check: Callable[[], bool]) -> None:
        self._availability_check = check

    def activate(self, port: int, model_id: str) -> None:
        if not 1 <= port <= 65535 or not model_id:
            raise ValueError("Invalid local tunnel endpoint")
        with self._lock:
            self._port = port
            self._model_id = model_id

    def deactivate(self) -> None:
        with self._lock:
            self._port = None
            self._model_id = None

    def target(self) -> tuple[int, str] | None:
        with self._lock:
            if self._port is None or self._model_id is None:
                return None
            if not self._availability_check():
                return None
            return self._port, self._model_id

    async def forward(self, method: str, path: str, body: bytes = b"", stream: bool = False) -> Response:
        target = self.target()
        if target is None:
            return api_error(503, "Model chưa sẵn sàng", "model_unavailable")
        port, _ = target
        if path not in ("/v1/models", "/v1/chat/completions"):
            raise ValueError("Unsupported inference path")
        headers = {"Accept": "text/event-stream" if stream else "application/json"}
        if body:
            headers["Content-Type"] = "application/json"
        request = self.client.build_request(
            method,
            f"http://127.0.0.1:{port}{path}",
            content=body,
            headers=headers,
            timeout=httpx.Timeout(connect=5, read=self.stream_read_timeout if stream else 120, write=10, pool=5),
        )
        try:
            upstream = await self.client.send(request, stream=stream)
        except httpx.TimeoutException:
            return api_error(504, "Model không phản hồi kịp", "upstream_timeout")
        except httpx.HTTPError:
            return api_error(503, "Không kết nối được model", "upstream_unavailable")
        content_type = upstream.headers.get("content-type", "application/json")
        if not stream:
            return Response(content=upstream.content, status_code=upstream.status_code, headers={"Content-Type": content_type})

        async def body_chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return ClosingStreamingResponse(
            body_chunks(),
            status_code=upstream.status_code,
            headers={"Content-Type": content_type, "Cache-Control": "no-cache"},
            on_close=upstream.aclose,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
