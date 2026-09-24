import hashlib
import hmac
import secrets
import sqlite3
import time

from app.db import Database


class KeyStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        with self.db._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS api_keys_prefix ON api_keys(prefix)")

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def create(self, user: str) -> dict[str, object]:
        name = user.strip()
        if not name or len(name) > 100:
            raise ValueError("Tên user phải dài 1–100 ký tự")
        token = "vlm_" + secrets.token_urlsafe(32)
        prefix = token[:14]
        created_at = int(time.time())
        with self.db._connect() as conn:
            cur = conn.execute(
                "INSERT INTO api_keys (user, prefix, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, prefix, self._digest(token), created_at),
            )
            key_id = cur.lastrowid
        return {"id": key_id, "user": name, "prefix": prefix, "created_at": created_at, "key": token}

    def list(self) -> list[dict[str, object]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT id, user, prefix, created_at, revoked_at FROM api_keys ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, key_id: int) -> bool:
        with self.db._connect() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (int(time.time()), key_id),
            )
            return cur.rowcount > 0

    def authenticate(self, token: str) -> bool:
        if not token.startswith("vlm_") or len(token) < 40 or len(token) > 128:
            return False
        digest = self._digest(token)
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT token_hash, revoked_at FROM api_keys WHERE prefix = ?",
                (token[:14],),
            ).fetchall()
        return any(
            hmac.compare_digest(digest, row["token_hash"]) and row["revoked_at"] is None
            for row in rows
        )
