from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

PASSWORD_ITERATIONS = 600_000
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


class UsernameAlreadyExistsError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DemoUser:
    user_id: str
    username: str

    @property
    def tenant_id(self) -> str:
        return f"demo_user_{self.user_id}"


def _password_hash(password: str, *, salt: bytes | None = None) -> str:
    resolved_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt,
        PASSWORD_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${resolved_salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (TypeError, ValueError):
        return False


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DemoAuthStore:
    """Small SQLite-backed account store for the public Playground."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._dummy_password_hash = _password_hash("not-a-real-password")

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("DemoAuthStore.initialize() must be called first")
        return self._connection

    def initialize(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS demo_users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                normalized_username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS demo_sessions (
                token_digest TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES demo_users(user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_demo_sessions_expiry
                ON demo_sessions (expires_at);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def register(self, username: str, password: str) -> tuple[DemoUser, str]:
        normalized = username.casefold()
        password_hash = _password_hash(password)
        user = DemoUser(user_id=uuid.uuid4().hex, username=username)
        now = int(time.time())
        with self._lock:
            try:
                self.connection.execute(
                    """INSERT INTO demo_users
                    (user_id, username, normalized_username, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (user.user_id, user.username, normalized, password_hash, now),
                )
            except sqlite3.IntegrityError as error:
                self.connection.rollback()
                raise UsernameAlreadyExistsError from error
            token = self._create_session(user.user_id, now=now)
            self.connection.commit()
        return user, token

    def authenticate(self, username: str, password: str) -> tuple[DemoUser, str] | None:
        normalized = username.casefold()
        with self._lock:
            row = self.connection.execute(
                """SELECT user_id, username, password_hash
                FROM demo_users WHERE normalized_username=?""",
                (normalized,),
            ).fetchone()
            encoded = row["password_hash"] if row is not None else self._dummy_password_hash
            if not _verify_password(password, encoded) or row is None:
                return None
            user = DemoUser(user_id=row["user_id"], username=row["username"])
            now = int(time.time())
            token = self._create_session(user.user_id, now=now)
            self.connection.commit()
            return user, token

    def resolve_session(self, token: str) -> DemoUser | None:
        if not token:
            return None
        now = int(time.time())
        with self._lock:
            row = self.connection.execute(
                """SELECT u.user_id, u.username
                FROM demo_sessions AS s
                JOIN demo_users AS u ON u.user_id = s.user_id
                WHERE s.token_digest=? AND s.expires_at>?""",
                (_session_digest(token), now),
            ).fetchone()
            if row is None:
                return None
            return DemoUser(user_id=row["user_id"], username=row["username"])

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self.connection.execute(
                "DELETE FROM demo_sessions WHERE token_digest=?",
                (_session_digest(token),),
            )
            self.connection.commit()

    def _create_session(self, user_id: str, *, now: int) -> str:
        self.connection.execute("DELETE FROM demo_sessions WHERE expires_at<=?", (now,))
        token = secrets.token_urlsafe(32)
        self.connection.execute(
            """INSERT INTO demo_sessions (token_digest, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)""",
            (_session_digest(token), user_id, now + SESSION_MAX_AGE_SECONDS, now),
        )
        return token
