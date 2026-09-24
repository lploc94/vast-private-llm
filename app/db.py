import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SESSION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class AdminSession:
    csrf_token: str


class Database:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        self.path = data_dir / "app.db"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deployment (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    phase TEXT NOT NULL,
                    message TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO deployment (id, phase, message, updated_at)
                VALUES (1, 'idle', 'Chưa thuê máy', unixepoch());
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(deployment)")}
            additions = {
                "operation_id": "TEXT",
                "instance_id": "INTEGER",
                "offer_id": "INTEGER",
                "model_id": "TEXT",
                "min_vram_gb": "INTEGER",
                "disk_gb": "INTEGER",
                "price_hour": "REAL",
                "error": "TEXT",
            }
            for name, kind in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE deployment ADD COLUMN {name} {kind}")
        os.chmod(self.path, 0o600)

    def has_admin(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone() is not None

    def bootstrap_admin(self, password: str | None) -> None:
        if not password or self.has_admin():
            return
        if len(password) < 12:
            raise ValueError("VASTLLM_ADMIN_PASSWORD must contain at least 12 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admin (id, salt, password_hash) VALUES (1, ?, ?)",
                (salt, digest),
            )

    def verify_admin(self, password: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT salt, password_hash FROM admin WHERE id = 1").fetchone()
        if row is None:
            return False
        candidate = hashlib.scrypt(password.encode(), salt=row["salt"], n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(candidate, row["password_hash"])

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self) -> tuple[str, AdminSession]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO sessions (token_hash, csrf_token, expires_at) VALUES (?, ?, ?)",
                (self._token_hash(token), csrf, int(time.time()) + SESSION_SECONDS),
            )
        return token, AdminSession(csrf_token=csrf)

    def get_session(self, token: str | None) -> AdminSession | None:
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT csrf_token FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (self._token_hash(token), int(time.time())),
            ).fetchone()
        return AdminSession(csrf_token=row["csrf_token"]) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))

    def deployment_state(self) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM deployment WHERE id = 1").fetchone()
        return dict(row)

    def begin_deployment(
        self, operation_id: str, offer_id: int, model_id: str,
        min_vram_gb: int, disk_gb: int, price_hour: float,
    ) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT operation_id, instance_id FROM deployment WHERE id = 1").fetchone()
            if row["operation_id"] or row["instance_id"]:
                raise ValueError("Đã có deployment hoặc thao tác tạo máy đang chờ xử lý")
            conn.execute(
                """UPDATE deployment SET phase = 'creating', message = 'Đang thuê máy Vast',
                   operation_id = ?, instance_id = NULL, offer_id = ?, model_id = ?,
                   min_vram_gb = ?, disk_gb = ?, price_hour = ?, error = NULL,
                   updated_at = ? WHERE id = 1""",
                (operation_id, offer_id, model_id, min_vram_gb, disk_gb, price_hour, int(time.time())),
            )
        return self.deployment_state()

    def update_deployment(self, **changes: object) -> dict[str, object]:
        allowed = {
            "phase", "message", "operation_id", "instance_id", "offer_id", "model_id",
            "min_vram_gb", "disk_gb", "price_hour", "error",
        }
        if not changes or any(key not in allowed for key in changes):
            raise ValueError("Invalid deployment update")
        changes["updated_at"] = int(time.time())
        sql = "UPDATE deployment SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = 1"
        with self._connect() as conn:
            conn.execute(sql, tuple(changes.values()))
        return self.deployment_state()
