import os
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


def _vast_api_key(data_dir: Path) -> str | None:
    try:
        saved = (data_dir / "vast_api_key").read_text().strip()
        if saved:
            return saved
    except (OSError, UnicodeError):
        pass
    explicit = os.environ.get("VAST_API_KEY")
    if explicit:
        return explicit
    try:
        return (Path.home() / ".config" / "vastai" / "vast_api_key").read_text().strip() or None
    except (OSError, UnicodeError):
        return None


def mask_vast_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) < 12:
        return "••••"
    return f"{api_key[:4]}…{api_key[-4:]}"


def save_vast_api_key(data_dir: Path, api_key: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data_dir, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".vast_api_key-", dir=data_dir)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(api_key)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, data_dir / "vast_api_key")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_password: str | None = None
    vast_api_key: str | None = None
    ssh_key_path: Path = field(default_factory=lambda: Path.home() / ".ssh" / "id_ed25519")

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("VASTLLM_DATA_DIR", "data")).expanduser()
        return cls(
            data_dir=data_dir,
            admin_password=os.environ.get("VASTLLM_ADMIN_PASSWORD") or None,
            vast_api_key=_vast_api_key(data_dir),
            ssh_key_path=Path(
                os.environ.get("VASTLLM_SSH_KEY_PATH", "~/.ssh/id_ed25519")
            ).expanduser(),
        )
