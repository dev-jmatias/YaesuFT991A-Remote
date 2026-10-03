"""Users, password hashing (scrypt), server-side sessions, login throttling, audit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

ROLES = ("viewer", "operator", "admin")
SESSION_IDLE_S = 12 * 3600
SESSION_ABSOLUTE_S = 7 * 24 * 3600
MIN_PASSWORD_LEN = 10

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            maxmem=64 * 1024 * 1024, dklen=32,
        )
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


@dataclass
class Session:
    user_id: int
    username: str
    role: str
    csrf: str
    trusted: bool = False


class AuthStore:
    def __init__(self, db_path: str | Path, clock=time.time):
        self.clock = clock
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
              pw_hash TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
              sid_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL,
              created REAL NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
              ts REAL NOT NULL, event TEXT NOT NULL, username TEXT, ip TEXT, detail TEXT);
            """
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
        if "trusted" not in cols:                       # migrate databases created before the trusted-users list
            self.db.execute("ALTER TABLE users ADD COLUMN trusted INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        self._fails: dict[tuple[str, str], tuple[int, float]] = {}

    # --- users
    def user_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str) -> int:
        if role not in ROLES:
            raise ValueError("bad role")
        if not (1 <= len(username) <= 32) or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("username must be 1-32 chars: letters, digits, . _ -")
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
        cur = self.db.execute(
            "INSERT INTO users(username,pw_hash,role,created) VALUES(?,?,?,?)",
            (username, hash_password(password), role, self.clock()),
        )
        self.db.commit()
        return cur.lastrowid

    # --- throttling
    def retry_after(self, ip: str, username: str) -> float:
        n, until = self._fails.get((ip, username.lower()), (0, 0.0))
        return max(0.0, until - self.clock())

    def _record_fail(self, ip: str, username: str) -> None:
        key = (ip, username.lower())
        n, _ = self._fails.get(key, (0, 0.0))
        n += 1
        delay = 0.0 if n < 4 else min(2 ** (n - 3), 300)
        self._fails[key] = (n, self.clock() + delay)

    # --- login
    def login(self, username: str, password: str, ip: str) -> tuple[str, Session] | None:
        row = self.db.execute(
            "SELECT id,username,pw_hash,role,trusted FROM users WHERE username=?", (username,)
        ).fetchone()
        # Always run a hash so unknown users cost the same as wrong passwords.
        ok = verify_password(password, row[2] if row else hash_password("dummy-password"))
        if not (row and ok):
            self._record_fail(ip, username)
            self.audit("login_failed", username, ip)
            return None
        self._fails.pop((ip, username.lower()), None)
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self.clock()
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?)", (self._h(sid), row[0], csrf, now, now)
        )
        self.db.commit()
        self.audit("login", row[1], ip)
        return sid, Session(row[0], row[1], row[3], csrf, bool(row[4]))

    @staticmethod
    def _h(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def get_session(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        row = self.db.execute(
            "SELECT s.user_id,u.username,u.role,s.csrf,s.created,s.last_seen,u.trusted FROM sessions s "
            "JOIN users u ON u.id=s.user_id WHERE s.sid_hash=?",
            (self._h(sid),),
        ).fetchone()
        if not row:
            return None
        now = self.clock()
        if now - row[5] > SESSION_IDLE_S or now - row[4] > SESSION_ABSOLUTE_S:
            self.logout(sid)
            return None
        self.db.execute("UPDATE sessions SET last_seen=? WHERE sid_hash=?", (now, self._h(sid)))
        self.db.commit()
        return Session(row[0], row[1], row[2], row[3], bool(row[6]))

    def logout(self, sid: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE sid_hash=?", (self._h(sid),))
        self.db.commit()

    def audit(self, event: str, username: str | None = None, ip: str | None = None, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (self.clock(), event, username, ip, detail))
        self.db.commit()

    def audit_tail(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT ts,event,username,ip,detail FROM audit ORDER BY rowid DESC LIMIT ?", (max(1, min(limit, 1000)),)
        ).fetchall()
        return [{"ts": r[0], "event": r[1], "user": r[2], "ip": r[3], "detail": r[4]} for r in rows]

    # --- user management
    def list_users(self) -> list[dict]:
        rows = self.db.execute("SELECT id,username,role,created,trusted FROM users ORDER BY username").fetchall()
        return [{"id": r[0], "username": r[1], "role": r[2], "created": r[3], "trusted": bool(r[4])} for r in rows]

    def get_user(self, user_id: int) -> dict | None:
        r = self.db.execute("SELECT id,username,role FROM users WHERE id=?", (user_id,)).fetchone()
        return {"id": r[0], "username": r[1], "role": r[2]} if r else None

    def set_trusted(self, user_id: int, trusted: bool) -> None:
        """Trusted users take control immediately instead of asking the current holder (never while transmitting)."""
        if not self.get_user(user_id):
            raise ValueError("no such user")
        self.db.execute("UPDATE users SET trusted=? WHERE id=?", (1 if trusted else 0, user_id))
        self.db.commit()

    def _admin_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]

    def set_role(self, user_id: int, role: str) -> None:
        u = self.get_user(user_id)
        if not u:
            raise ValueError("no such user")
        if role not in ROLES:
            raise ValueError("bad role")
        if u["role"] == "admin" and role != "admin" and self._admin_count() <= 1:
            raise ValueError("cannot demote the last administrator")
        self.db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        self.db.commit()

    def set_password(self, user_id: int, password: str, keep_sid: str | None = None) -> None:
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
        if not self.get_user(user_id):
            raise ValueError("no such user")
        self.db.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), user_id))
        self.db.commit()
        self.revoke_user_sessions(user_id, keep_sid)

    def check_password(self, user_id: int, password: str) -> bool:
        r = self.db.execute("SELECT pw_hash FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(r) and verify_password(password, r[0])

    def revoke_user_sessions(self, user_id: int, keep_sid: str | None = None) -> None:
        if keep_sid:
            self.db.execute("DELETE FROM sessions WHERE user_id=? AND sid_hash<>?", (user_id, self._h(keep_sid)))
        else:
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.db.commit()

    def delete_user(self, user_id: int) -> None:
        u = self.get_user(user_id)
        if not u:
            raise ValueError("no such user")
        if u["role"] == "admin" and self._admin_count() <= 1:
            raise ValueError("cannot delete the last administrator")
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM users WHERE id=?", (user_id,))
        self.db.commit()
