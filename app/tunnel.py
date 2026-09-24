import os
import socket
import subprocess
import time
from pathlib import Path


class TunnelError(RuntimeError):
    pass


class TunnelManager:
    def __init__(self, data_dir: Path, key_path: Path) -> None:
        self.data_dir = data_dir
        self.key_path = key_path.expanduser()
        self.known_hosts = data_dir / "known_hosts"
        self.process: subprocess.Popen[str] | None = None
        self.local_port: int | None = None

    def _ensure_known_hosts(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.known_hosts, flags, 0o600)
        os.close(fd)
        os.chmod(self.known_hosts, 0o600)

    def build_command(self, host: str, ssh_port: int, local_port: int) -> list[str]:
        if not host or not 1 <= ssh_port <= 65535 or not 1 <= local_port <= 65535:
            raise ValueError("Invalid SSH endpoint")
        self._ensure_known_hosts()
        return [
            "ssh", "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=2",
            "-i", str(self.key_path),
            "-p", str(ssh_port),
            "-L", f"127.0.0.1:{local_port}:127.0.0.1:8000",
            f"root@{host}",
        ]

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _listening(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False

    def connect(self, host: str, ssh_port: int) -> int:
        if not self.key_path.is_file():
            raise TunnelError("Không tìm thấy SSH private key")
        self.stop()
        for _ in range(3):
            local_port = self._free_port()
            command = self.build_command(host, ssh_port, local_port)
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            for _ in range(100):
                if process.poll() is not None:
                    detail = (process.stderr.read() if process.stderr else "").strip()[-500:]
                    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in detail:
                        raise TunnelError("SSH host key đã thay đổi; hãy xác minh instance trong Vast")
                    if "Address already in use" in detail:
                        break
                    raise TunnelError(f"SSH tunnel không khởi động: {detail or 'unknown error'}")
                if self._listening(local_port):
                    self.process = process
                    self.local_port = local_port
                    return local_port
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        raise TunnelError("Không mở được cổng SSH tunnel cục bộ")

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        process = self.process
        self.process = None
        self.local_port = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
