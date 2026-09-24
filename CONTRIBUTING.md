# Contributing

Thanks for helping improve Vast Private LLM. Open an issue before a large change so the intended behavior can be agreed on first. Small bug fixes and documentation improvements can go straight to a pull request.

## Local setup

1. Clone the repository and run `uv sync --locked` with Python 3.12 or newer.
2. Run the dashboard with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8080`.
3. Use local fakes or your own disposable Vast account when developing. Do not rent a GPU or spend another contributor's credit while reviewing a pull request.

The existing automated checks are in `tests/` and can be run with `uv run pytest -q`. Include the behavior affected by a code change in your pull request description. Keep examples free of real keys, SSH private keys, local database files, and model weights.

## Pull requests

- Keep changes focused and explain the user-visible effect.
- Update English and Vietnamese documentation when behavior or setup changes.
- Preserve the local-only admin boundary and the model's SSH-only connection path.
- Do not commit `.env`, `data/`, `.venv/`, credentials, or large model files.

For a security issue, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
