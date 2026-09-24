import base64
import binascii
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class SSHKeyError(RuntimeError):
    pass


def public_key_material(value: str) -> tuple[str, bytes]:
    if "\n" in value.strip() or "\r" in value.strip():
        raise SSHKeyError("SSH public key không hợp lệ")
    parts = value.strip().split()
    if len(parts) < 2 or len(value) > 8192:
        raise SSHKeyError("SSH public key không hợp lệ")
    algorithm = parts[0]
    if not (algorithm.startswith(("ssh-", "ecdsa-sha2-", "sk-")) and
            all(character.isalnum() or character in "@._+-" for character in algorithm)):
        raise SSHKeyError("SSH public key không hợp lệ")
    try:
        material = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SSHKeyError("SSH public key không hợp lệ") from exc
    if not material:
        raise SSHKeyError("SSH public key không hợp lệ")
    return algorithm, material


@dataclass(frozen=True)
class SSHIdentity:
    private_path: Path
    public_key: str


class SSHKeyManager:
    def __init__(self, data_dir: Path, override_path: Path | None = None) -> None:
        self.data_dir = data_dir.expanduser()
        self.override_path = override_path.expanduser() if override_path else None
        self.managed_dir = self.data_dir / "ssh"
        self.managed_path = self.managed_dir / "id_ed25519"

    @staticmethod
    def _derive_public(private_path: Path) -> str:
        try:
            result = subprocess.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(private_path)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=15, check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SSHKeyError("Không đọc được SSH public key từ private key") from exc
        public_key_material(result.stdout)
        return result.stdout.strip()

    @classmethod
    def _read_pair(cls, private_path: Path, managed: bool) -> SSHIdentity:
        try:
            info = private_path.lstat()
        except OSError as exc:
            raise SSHKeyError("Thiếu SSH private key") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise SSHKeyError("SSH private key không an toàn; cần file riêng với quyền 0600")
        if managed and stat.S_IMODE(info.st_mode) != 0o600:
            raise SSHKeyError("SSH private key do ứng dụng quản lý cần quyền 0600")

        derived = cls._derive_public(private_path)
        public_path = Path(f"{private_path}.pub")
        if os.path.lexists(public_path):
            try:
                if not stat.S_ISREG(public_path.lstat().st_mode):
                    raise SSHKeyError("SSH public key không phải file thường")
                public = public_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError) as exc:
                raise SSHKeyError("Không đọc được SSH public key") from exc
            if public_key_material(public) != public_key_material(derived):
                raise SSHKeyError("SSH public key không khớp private key")
        elif managed:
            raise SSHKeyError("Thiếu SSH public key do ứng dụng quản lý")
        else:
            public = derived
        if managed and public_key_material(public)[0] != "ssh-ed25519":
            raise SSHKeyError("SSH key do ứng dụng quản lý phải là Ed25519")
        return SSHIdentity(private_path, public)

    def ensure_local_key(self) -> SSHIdentity:
        if self.override_path is not None:
            return self._read_pair(self.override_path, managed=False)

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not stat.S_ISDIR(self.data_dir.lstat().st_mode):
                raise SSHKeyError("Thư mục dữ liệu không an toàn")
            os.chmod(self.data_dir, 0o700)
        except OSError as exc:
            raise SSHKeyError("Không tạo được thư mục dữ liệu cho SSH key") from exc

        if os.path.lexists(self.managed_dir):
            try:
                info = self.managed_dir.lstat()
            except OSError as exc:
                raise SSHKeyError("Không đọc được thư mục SSH key") from exc
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise SSHKeyError("Thư mục SSH key không an toàn; cần quyền 0700")
            return self._read_pair(self.managed_path, managed=True)

        try:
            temporary = Path(tempfile.mkdtemp(prefix=".ssh-", dir=self.data_dir))
        except OSError as exc:
            raise SSHKeyError("Không tạo được thư mục tạm cho SSH key") from exc
        try:
            temp_key = temporary / "id_ed25519"
            try:
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "vast-private-llm", "-f", str(temp_key)],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=20, check=True,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise SSHKeyError("Không tạo được SSH key Ed25519") from exc
            os.chmod(temp_key, 0o600)
            self._read_pair(temp_key, managed=True)
            if os.path.lexists(self.managed_dir):
                raise SSHKeyError("Thư mục SSH key đã xuất hiện; hãy kiểm tra trước khi thử lại")
            try:
                os.rename(temporary, self.managed_dir)
            except OSError as exc:
                raise SSHKeyError("Không lưu được SSH key do ứng dụng quản lý") from exc
            return self._read_pair(self.managed_path, managed=True)
        finally:
            if os.path.lexists(temporary):
                shutil.rmtree(temporary)
