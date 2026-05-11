import base64
import hashlib
import hmac
import os
import sqlite3
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


class AuthDatabase:
    def __init__(self, db_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        default_path = os.path.join(base_dir, "data", "auth.db")
        self.db_path = str(db_path or default_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def init_database(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()
        conn.close()

    def has_any_admin(self) -> bool:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM users WHERE role='admin' AND is_active=1"
        ).fetchone()
        conn.close()
        return int((row["cnt"] if row else 0) or 0) > 0

    def create_user(self, username: str, password: str, role: str = "user") -> int:
        uname = str(username or "").strip()
        if not uname:
            raise ValueError("用户名不能为空")
        if role not in {"admin", "user"}:
            raise ValueError("角色无效")
        if len(str(password or "")) < 6:
            raise ValueError("密码至少6位")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pwd_hash = self._hash_password(password)

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (uname, pwd_hash, role, now, now),
        )
        conn.commit()
        user_id = int(cur.lastrowid or 0)
        conn.close()
        return user_id

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        uname = str(username or "").strip()
        if not uname:
            return None
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM users WHERE username=? LIMIT 1",
            (uname,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_user_by_username(username)
        if not user or int(user.get("is_active", 0) or 0) != 1:
            return None

        stored_hash = str(user.get("password_hash") or "")
        if not self._verify_password(password, stored_hash):
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        conn.execute(
            "UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
            (now, now, int(user.get("id") or 0)),
        )
        conn.commit()
        conn.close()

        return {
            "id": int(user.get("id") or 0),
            "username": str(user.get("username") or ""),
            "role": str(user.get("role") or "user"),
            "is_active": int(user.get("is_active") or 0),
        }

    def list_users(self) -> list:
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT id, username, role, is_active, created_at, updated_at, last_login_at
            FROM users
            ORDER BY role DESC, id ASC
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_session(self, user_id: int, days: int = 7) -> str:
        uid = int(user_id or 0)
        if uid <= 0:
            raise ValueError("无效用户")
        now = datetime.now()
        expires = now + timedelta(days=max(1, int(days or 7)))
        token = secrets.token_urlsafe(48)
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO user_sessions (token, user_id, created_at, expires_at, revoked)
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                token,
                uid,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                expires.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
        return token

    def get_user_by_session(self, token: str) -> Optional[Dict[str, Any]]:
        tk = str(token or "").strip()
        if not tk:
            return None
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        row = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.is_active
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
              AND s.revoked = 0
              AND s.expires_at > ?
              AND u.is_active = 1
            LIMIT 1
            """,
            (tk, now_str),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def revoke_session(self, token: str) -> None:
        tk = str(token or "").strip()
        if not tk:
            return
        conn = self._get_connection()
        conn.execute("UPDATE user_sessions SET revoked=1 WHERE token=?", (tk,))
        conn.commit()
        conn.close()

    def _hash_password(self, password: str) -> str:
        iterations = 200000
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
        return "pbkdf2_sha256${}${}${}".format(
            iterations,
            base64.b64encode(salt).decode("utf-8"),
            base64.b64encode(digest).decode("utf-8"),
        )

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            algo, iter_str, salt_b64, hash_b64 = str(stored_hash or "").split("$", 3)
            if algo != "pbkdf2_sha256":
                return False
            iterations = int(iter_str)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False


auth_db = AuthDatabase()
