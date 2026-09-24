# Local API

The dashboard and API use the same local FastAPI server. By default its base URL is `http://127.0.0.1:8080/v1`. Start it with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8080`; do not bind the admin dashboard to a public interface.

Create a user key in the **API Keys** tab and copy it when shown. It is different from the Vast API key. Send it in `Authorization: Bearer YOUR_USER_KEY` on every `/v1` request. A revoked key is rejected on the next request.

## List models

```sh
curl http://127.0.0.1:8080/v1/models \
  -H 'Authorization: Bearer YOUR_USER_KEY'
```

This request reaches vLLM through the SSH tunnel. Once the deployment is ready, its model ID is `qwen-3.8`.

## Chat completion

```sh
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_USER_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-3.8","messages":[{"role":"user","content":"Hello"}],"max_tokens":128}'
```

A successful response follows vLLM's OpenAI-compatible chat-completion format, including a `choices` array and the served model name. For streaming, add `"stream": true` to the JSON body and read the event stream. The dashboard's **Integration → Copy as Markdown** button generates examples for an agent or another application; it leaves `YOUR_USER_KEY` as a placeholder.

## Limits and errors

- The request body limit for chat is 1 MiB. The `model` field is required and must be `qwen-3.8` for the current deployment.
- Only one output choice (`n=1`) is supported. The vLLM runtime is configured for one active generation; simultaneous requests queue.
- The configured context ceiling is 262,144 tokens for input plus output. `max_tokens` can be 16,384 or 32,768 when the remaining context fits; practical capacity still depends on the rented GPU and vLLM runtime.
- Missing, invalid, or revoked user keys return HTTP 401. A model that is not ready returns HTTP 503. Oversized requests return HTTP 413; malformed JSON or model mismatch returns HTTP 400. Upstream unavailability and timeout can return HTTP 503 or 504.

The proxy supports only `GET /v1/models` and `POST /v1/chat/completions`. Other OpenAI API routes are not implemented. The `/v1` endpoint is local by default; use SSH forwarding if the calling application runs on a different computer.
