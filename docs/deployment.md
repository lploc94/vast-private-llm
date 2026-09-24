# Deployment, costs, and recovery

## Before renting

1. Create a Vast.ai account and API key. Paste it into **Deploy** after starting the local dashboard, or export `VAST_API_KEY` before starting it. A saved dashboard key takes precedence, followed by the environment variable and the Vast CLI key file.
2. The app prepares an Ed25519 key pair in `<data-dir>/ssh/` and registers only its **public** key with Vast before renting. No manual SSH-key upload is needed. Set `VASTLLM_SSH_KEY_PATH` only if you want to use an existing private key instead; the app will register its matching public key without changing your files.
3. Search offers in the dashboard. The only supported deployment model is [`huihui-ai/Huihui-Qwen3.8-27B-abliterated`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated), called `qwen-3.8` by the local API. The model files download to the rented GPU instance and are not stored in this repository.

The dashboard starts with **80 GB GPU VRAM** and **120 GB disk** as minimums and searches for one on-demand GPU with CUDA 13.0 or newer. More VRAM gives the runtime additional room. Model weights are roughly 55.6 GB. These are starting thresholds, not a guarantee that every Vast host will run the model successfully.

The managed private key stays in the local data directory with owner-only permissions. Keep it when backing up or moving the dashboard if you want to reconnect to an existing instance. The corresponding account-level public key remains on Vast after an instance is destroyed; you can remove it in the Vast console when no instance or future deployment needs it. Scoped Vast API keys need `user_read` to list SSH keys and `user_write` to register a new one. A registration failure stops before the GPU rental begins.

After every instance is gone, **Revoke managed SSH key** in Deploy removes the matching public key from the currently selected Vast account, confirms it is absent, then deletes the app-managed pair under `<data-dir>/ssh/`. The button is unavailable while a deployment is active or its outcome is unresolved, when Vast reports any instance in the account, or when `VASTLLM_SSH_KEY_PATH` selects your own key. A later deploy creates and registers a fresh pair. Revocation also needs `user_read` and `user_write`. Removing an account key does not prove it was detached from an already running instance, so finish those instances first. If the key cannot be found in the selected account, check whether the Vast API key was changed or the SSH key was removed manually; the app keeps its local pair and reports that account revocation cannot be confirmed.

## Comparing offers

The offer table can sort by estimated decode tokens per second, hourly price, or estimated tokens per dollar. The estimate for one BF16 request is `0.70 × advertised GPU memory bandwidth (GB/s) ÷ 55.563 GB of model weights`. Tokens per dollar assumes continuous generation for a full hour. These are comparison estimates, not benchmarks; prompt length, host performance, and workload affect real speed. Offers without a usable bandwidth figure show no speed estimate.

## What happens after Deploy

The dashboard tracks **Rent machine → Boot → SSH → Load model → Ready**. It creates a Vast instance using the `vllm/vllm-openai:qwen38` image, runs vLLM on the instance loopback port, then establishes an SSH tunnel. When Vast supplies a direct `22/tcp` mapping, the tunnel uses that public IP and port; otherwise it uses Vast's SSH proxy endpoint. The local `/v1` API becomes available only after vLLM reports the expected model.

vLLM serves text chat with a maximum configured context length of 262,144 tokens, tool calling, and explicit prefix caching. It generates for one request at a time; later requests wait in a queue. Prefix caching can save repeated prompt prefill work when requests share the same beginning, but it does not persist after vLLM restarts.

**Vast charges begin when the instance is created**, including boot and model download time. The dashboard shows the account's remaining USD credit, updates it about once per minute while Deploy is open, and labels cached values with their age in seconds. That credit is an account value accessed through the selected Vast key, not a per-key allowance. Vast may also charge for storage while an instance exists but is stopped; check the Vast console for actual billing.

## Recovery and stopping costs

- A deployment operation is labeled `vastllm-<operation-id>`. If the create response is uncertain, the app searches the account for that label before allowing another rental. Check the Vast console when the state says the outcome is unknown.
- After a dashboard restart, the app rechecks Vast status, SSH, and vLLM before marking the model ready again. **Retry setup** can reconnect without creating a second instance.
- The app stores SSH host keys in `<data-dir>/known_hosts`. If a stored key changes, SSH is refused; verify the instance identity rather than deleting the file to bypass the warning.
- **Destroy instance** stops the local tunnel after Vast confirms destruction. If Vast does not confirm, the dashboard retains the instance ID so the action can be retried. Verify the instance and billing state in the Vast console after destruction.
- Keep `data/` private. Back up `app.db` while the app is stopped or through SQLite's backup mechanism. Do not run multiple dashboard processes against the same data directory.

Use the [API guide](api.md) after the deployment reaches Ready.
