import os
import io
from pathlib import Path

import pytest

from app.tunnel import TunnelError, TunnelManager


def test_tunnel_command_binds_only_loopback_and_uses_pinned_known_hosts(tmp_path: Path) -> None:
    key = tmp_path / "ssh_key"
    key.write_text("test key")
    tunnel = TunnelManager(data_dir=tmp_path, key_path=key)
    command = tunnel.build_command("ssh.vast.ai", 40000, 18000)
    assert "127.0.0.1:18000:127.0.0.1:8000" in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert "ExitOnForwardFailure=yes" in command
    assert "BatchMode=yes" in command
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in command
    assert "-N" in command
    assert (os.stat(tmp_path / "known_hosts").st_mode & 0o777) == 0o600


def test_tunnel_does_not_erase_known_host_when_connection_fails(tmp_path: Path) -> None:
    key = tmp_path / "ssh_key"
    key.write_text("test key")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("[ssh.vast.ai]:40000 old-host-key\n")
    tunnel = TunnelManager(data_dir=tmp_path, key_path=key)
    tunnel.build_command("ssh.vast.ai", 40000, 18000)
    assert known_hosts.read_text() == "[ssh.vast.ai]:40000 old-host-key\n"


class FakeProcess:
    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        self.stderr = io.StringIO(detail)
        self.terminated = False

    def poll(self):
        return 255 if self.detail or self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()


def test_connect_bootstraps_and_reuses_host_key(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "ssh_key"
    key.write_text("test key")
    tunnel = TunnelManager(tmp_path, key)
    commands = []

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr("app.tunnel.subprocess.Popen", fake_popen)
    monkeypatch.setattr(tunnel, "_listening", lambda _port: True)
    first_port = tunnel.connect("ssh.vast.ai", 40000)
    assert first_port > 0
    known_hosts = tmp_path / "known_hosts"
    assert known_hosts.exists()
    known_hosts.write_text("[ssh.vast.ai]:40000 ssh-ed25519 original\n")
    second_port = tunnel.connect("ssh.vast.ai", 40000)
    assert second_port > 0
    assert len(commands) == 2
    assert all("StrictHostKeyChecking=accept-new" in command for command in commands)
    assert known_hosts.read_text() == "[ssh.vast.ai]:40000 ssh-ed25519 original\n"


def test_connect_rejects_changed_host_key(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "ssh_key"
    key.write_text("test key")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("[ssh.vast.ai]:40000 ssh-ed25519 original\n")
    tunnel = TunnelManager(tmp_path, key)
    monkeypatch.setattr(
        "app.tunnel.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess("REMOTE HOST IDENTIFICATION HAS CHANGED"),
    )
    with pytest.raises(TunnelError, match="host key đã thay đổi"):
        tunnel.connect("ssh.vast.ai", 40000)
    assert known_hosts.read_text() == "[ssh.vast.ai]:40000 ssh-ed25519 original\n"
