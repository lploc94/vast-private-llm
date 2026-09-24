import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.inference import InferenceProxy
from app.main import create_app
from tests.fake_vllm import fake_vllm


def _key(client: TestClient) -> str:
    csrf = client.post(
        "/api/admin/login", json={"password": "a sufficiently long password"}
    ).json()["csrf_token"]
    return client.post(
        "/api/admin/keys", json={"user": "Lan"}, headers={"X-CSRF-Token": csrf}
    ).json()["key"]


def test_chat_and_models_forward_to_loopback_and_keep_upstream_status(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, admin_password="a sufficiently long password"))
    with fake_vllm() as upstream, TestClient(app) as client:
        key = _key(client)
        app.state.proxy.set_availability_check(lambda: True)
        app.state.proxy.activate(upstream.port, "demo-model")
        headers = {"Authorization": f"Bearer {key}"}
        models = client.get("/v1/models", headers=headers)
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "demo-model"

        payload = {"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]}
        chat = client.post("/v1/chat/completions", headers=headers, json=payload)
        assert chat.status_code == 200
        assert chat.json()["choices"][0]["message"]["content"] == "hello"
        assert client.post(
            "/v1/chat/completions", headers=headers,
            json={**payload, "messages": [{"role": "user", "content": "rate_limit"}]},
        ).status_code == 429
        assert client.post(
            "/v1/chat/completions", headers=headers, json={**payload, "model": "other-model"}
        ).status_code == 400
        for bad_stream in (1, "true"):
            assert client.post(
                "/v1/chat/completions", headers=headers, json={**payload, "stream": bad_stream}
            ).status_code == 400

        upstream.release_stream.set()
        streamed = client.post(
            "/v1/chat/completions", headers=headers, json={**payload, "stream": True}
        )
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert b'data: {"delta":"first"}' in streamed.content
        assert b"data: [DONE]" in streamed.content


def test_stream_yields_first_chunk_before_upstream_finishes() -> None:
    async def exercise(port: int) -> bytes:
        proxy = InferenceProxy()
        proxy.activate(port, "demo-model")
        body = json.dumps({"model": "demo-model", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
        response = await proxy.forward("POST", "/v1/chat/completions", body=body, stream=True)
        try:
            first = await asyncio.wait_for(anext(response.body_iterator), timeout=2)
            return first
        finally:
            await response.body_iterator.aclose()
            await proxy.aclose()

    with fake_vllm() as upstream:
        first = asyncio.run(exercise(upstream.port))
        assert upstream.first_chunk_sent.is_set()
        assert b"first" in first
        assert not upstream.release_stream.is_set()


def test_stream_close_releases_upstream_response() -> None:
    closed = asyncio.Event()

    class RecordingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            closed.set()

    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=RecordingStream())
        )
        client = httpx.AsyncClient(transport=transport)
        proxy = InferenceProxy(client=client)
        proxy.activate(8000, "demo-model")
        body = b'{"model":"demo-model","messages":[],"stream":true}'
        response = await proxy.forward("POST", "/v1/chat/completions", body=body, stream=True)
        assert b"first" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert closed.is_set()
        await client.aclose()

    asyncio.run(exercise())


def test_disconnect_before_first_body_chunk_releases_upstream_response() -> None:
    closed = asyncio.Event()

    class RecordingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"

        async def aclose(self) -> None:
            closed.set()

    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=RecordingStream())
        )
        client = httpx.AsyncClient(transport=transport)
        proxy = InferenceProxy(client=client)
        proxy.activate(8000, "demo-model")
        response = await proxy.forward("POST", "/v1/chat/completions", body=b"{}", stream=True)

        async def send(message):
            if message["type"] == "http.response.start":
                await asyncio.Event().wait()

        async def receive():
            return {"type": "http.disconnect"}

        scope = {
            "type": "http", "asgi": {"spec_version": "2.3"}, "method": "POST",
            "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
            "root_path": "", "headers": [], "query_string": b"", "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1), "scheme": "http",
        }
        await asyncio.wait_for(response(scope, receive, send), timeout=2)
        assert closed.is_set()
        await client.aclose()

    asyncio.run(exercise())


def test_stalled_stream_has_finite_inactivity_timeout() -> None:
    async def exercise(port: int) -> None:
        proxy = InferenceProxy(stream_read_timeout=0.05)
        proxy.activate(port, "demo-model")
        body = b'{"model":"demo-model","messages":[{"role":"user","content":"hi"}],"stream":true}'
        response = await proxy.forward("POST", "/v1/chat/completions", body=body, stream=True)
        assert b"first" in await anext(response.body_iterator)
        try:
            await asyncio.wait_for(anext(response.body_iterator), timeout=1)
            raise AssertionError("stalled stream did not time out")
        except httpx.ReadTimeout:
            pass
        finally:
            await response.body_iterator.aclose()
            await proxy.aclose()

    with fake_vllm() as upstream:
        asyncio.run(exercise(upstream.port))
