# Vast Private LLM

Run a local dashboard and an OpenAI-compatible chat endpoint on your own computer. The dashboard rents one GPU instance from Vast.ai, starts Qwen3.8 on vLLM, and connects to it through SSH. The model's HTTP port stays on the instance's loopback interface.

[Tiếng Việt](README.vi.md) · [Architecture](docs/architecture.md) · [Deployment and costs](docs/deployment.md) · [API guide](docs/api.md)

## What it provides

- **Deploy:** enter a Vast API key, compare one-GPU offers by estimated tokens per second, rent an instance, and follow its startup progress and account credit.
- **API Keys:** create or revoke local Bearer keys for applications. A new key is shown once; only its hash is stored.
- **Integration:** copy Markdown instructions or curl examples for another application or agent.
- **Local API:** call `GET /v1/models` and `POST /v1/chat/completions`, including streaming responses, through the SSH tunnel.

This is a **single-owner, local-only dashboard**. It does not provide a public admin login or a hosted multi-user control plane. Run it on `127.0.0.1`; if you need to use the dashboard from another computer, use SSH port forwarding. User API keys protect `/v1`, not the dashboard.

## Requirements

- Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and OpenSSH on the computer that runs the dashboard.
- A Vast.ai account and API key. You can paste the key into the dashboard after startup.
- An SSH key pair on this computer, with its **public** key added to your Vast.ai account. The default private-key path is `~/.ssh/id_ed25519`.
- A suitable Vast GPU offer when you choose to deploy. **Renting an instance costs money from the moment it is created, including model download/startup time.**

## Quick start

```sh
git clone https://github.com/lploc94/vast-private-llm.git
cd vast-private-llm
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>. The dashboard opens locally without an admin password and creates a local browser session. Keep the bind address on loopback. No Vast key or GPU rental is needed to see the dashboard.

1. Add your SSH public key to Vast.ai. If your private key is elsewhere, set `VASTLLM_SSH_KEY_PATH` before starting the dashboard.
2. In **Deploy**, paste a Vast API key, search offers, and choose a machine. The key is saved locally in `data/vast_api_key` with owner-only file permissions.
3. Wait for **Ready**, then create a user key in **API Keys**.
4. Use that user key with the local API:

```sh
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_USER_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-3.8","messages":[{"role":"user","content":"Hello"}],"max_tokens":128}'
```

The only supported deployment model is [`huihui-ai/Huihui-Qwen3.8-27B-abliterated`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated). Call it as `qwen-3.8` in `/v1` requests. Model weights are downloaded onto the rented instance; they are **not** in this repository. See [deployment requirements and cost behavior](docs/deployment.md) before renting.

## Configuration and local data

The app reads these optional environment variables at startup:

| Variable | Purpose | Default |
| --- | --- | --- |
| `VASTLLM_DATA_DIR` | SQLite database, Vast key, and SSH host-key file | `./data` |
| `VASTLLM_SSH_KEY_PATH` | SSH private key used for the tunnel | `~/.ssh/id_ed25519` |
| `VAST_API_KEY` | Alternative to entering the Vast key in Deploy | None |

The saved dashboard key takes precedence over `VAST_API_KEY`, then the Vast CLI key file. The repository does not automatically load `.env` files; [`.env.example`](.env.example) is a reference for shell configuration. Never commit real keys, the `data/` directory, or SSH private keys. Use one dashboard process per data directory.

For API methods, limits, error codes, and streaming, see the [API guide](docs/api.md). For instance recovery and stopping charges, see [deployment and operations](docs/deployment.md).

## Project layout

```text
app/        FastAPI dashboard, deployment service, Vast client, SSH tunnel, API proxy
tests/      Existing local fakes and test sources
docs/       Architecture, deployment, and API details
```

## License and contributions

This project's code and documentation are available under the [MIT License](LICENSE). The Qwen model, vLLM image, Vast.ai service, and dependencies have separate terms; this repository does not license or redistribute them. The linked model card currently identifies the model files as Apache-2.0.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributions and [SECURITY.md](SECURITY.md) for private vulnerability reports.
