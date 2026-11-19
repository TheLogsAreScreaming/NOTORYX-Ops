"""User accounts, roles and sign-in sessions.

With no accounts, the station works as before: anyone who can reach the dashboard can
look, and changes need the admin token (or come from the station computer itself).

Once an admin account exists, every page and API needs a sign-in, except the public map
(when it is turned on) and the sign-in itself. Roles:
  viewer   - look at everything except settings secrets
  operator - also change the fleet, alerts, ignore list, field reports and teams
  admin    - also change settings and accounts, and stop the station

The admin token keeps working for scripts and counts as an admin.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request

from .config import Config
from .store import Store

ROLES = ("viewer", "operator", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
COOKIE = "notoryx_ops_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 390_000
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
"""


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), want)
    except (ValueError, TypeError):
        return False


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


class Auth:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        store.db.executescript(SCHEMA)
        self._fails: dict[str, deque] = defaultdict(deque)

    # ---- accounts -------------------------------------------------------
    def accounts_on(self) -> bool:
        return bool(self.store.one("SELECT 1 x FROM users WHERE role = 'admin' AND disabled = 0 LIMIT 1"))

    def users(self) -> list[dict[str, Any]]:
        return self.store.q("SELECT id, username, role, disabled, created_at, last_login FROM users ORDER BY username")

    @staticmethod
    def _check(username: str | None, password: str | None, role: str | None) -> None:
        if username is not None and not NAME_RE.match(username):
            raise ValueError("Usernames are 2-32 letters, numbers, dots, dashes or underscores.")
        if password is not None and len(password) < 10:
            raise ValueError("Passwords need at least 10 characters.")
        if password is not None and len(password) > 200:
            raise ValueError("That password is too long.")
        if role is not None and role not in ROLES:
            raise ValueError("Role must be viewer, operator or admin.")

    def add_user(self, username: str, password: str, role: str) -> int:
        self._check(username, password, role)
        if self.store.one("SELECT 1 x FROM users WHERE username = ?", (username,)):
            raise ValueError("That username is taken.")
        return self.store.ins("INSERT INTO users(username, pw_hash, role, created_at) VALUES (?,?,?,?)",
                              (username, hash_password(password), role, time.time()))

    def update_user(self, uid: int, role: str | None = None, password: str | None = None,
                    disabled: bool | None = None) -> None:
        self._check(None, password, role)
        u = self.store.one("SELECT * FROM users WHERE id = ?", (uid,))
        if not u:
            raise HTTPException(404, "No such account.")
        demoting = (role is not None and role != "admin") or disabled
        if u["role"] == "admin" and demoting and self._admins() <= 1:
            raise ValueError("Keep at least one admin account.")
        if role is not None:
            self.store.x("UPDATE users SET role = ? WHERE id = ?", (role, uid))
        if password is not None:
            self.store.x("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), uid))
            self.store.x("DELETE FROM sessions WHERE user_id = ?", (uid,))
        if disabled is not None:
            self.store.x("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), uid))
            if disabled:
                self.store.x("DELETE FROM sessions WHERE user_id = ?", (uid,))

    def remove_user(self, uid: int) -> None:
        u = self.store.one("SELECT role FROM users WHERE id = ?", (uid,))
        if not u:
            raise HTTPException(404, "No such account.")
        if u["role"] == "admin" and self._admins() <= 1:
            raise ValueError("Keep at least one admin account.")
        self.store.x("DELETE FROM users WHERE id = ?", (uid,))

    def _admins(self) -> int:
        return self.store.one("SELECT COUNT(*) n FROM users WHERE role = 'admin' AND disabled = 0")["n"]

    # ---- sign-in ----------------------------------------------------------
    def login(self, request: Request, username: str, password: str) -> tuple[str, dict[str, Any]]:
        ip = request.client.host if request.client else "?"
        now = time.time()
        q = self._fails[ip]
        while q and q[0] < now - 300:
            q.popleft()
        if len(q) >= 8:
            raise HTTPException(429, "Too many failed sign-ins. Wait a few minutes and try again.")
        u = self.store.one("SELECT * FROM users WHERE username = ?", (username,))
        # Always run the hash so timing doesn't reveal which usernames exist.
        ok = check_password(password, u["pw_hash"] if u else hash_password("x", b"0" * 16))
        if not u or not ok or u["disabled"]:
            q.append(now)
            raise HTTPException(401, "Wrong username or password.")
        q.clear()
        tok = secrets.token_urlsafe(32)
        self.store.x("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self.store.x("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (_token_hash(tok), u["id"], now, now + SESSION_DAYS * 86400))
        self.store.x("UPDATE users SET last_login = ? WHERE id = ?", (now, u["id"]))
        return tok, {"id": u["id"], "username": u["username"], "role": u["role"]}

    def logout(self, token: str | None) -> None:
        if token:
            self.store.x("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return self.store.one(
            "SELECT u.id, u.username, u.role FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0", (_token_hash(token), time.time()))

    # ---- who is asking --------------------------------------------------------
    def principal(self, request: Request) -> dict[str, Any] | None:
        """The account behind a request, the admin token as an admin, or None."""
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        tok = self.cfg.admin_token
        if bearer and tok and hmac.compare_digest(bearer.encode(), tok.encode()):
            return {"id": 0, "username": "admin token", "role": "admin"}
        return self.session_user(request.cookies.get(COOKIE))

    def require(self, request: Request, role: str) -> dict[str, Any] | None:
        """Raise unless the request may act with this role."""
        who = self.principal(request)
        if self.accounts_on():
            if who is None:
                raise HTTPException(401, "Sign in first.")
            if RANK[who["role"]] < RANK[role]:
                raise HTTPException(403, f"This needs the {role} role.")
            return who
        # No accounts: the old rules. Looking is open; changes need the token or the station computer.
        if role == "viewer" or who is not None:
            return who
        if self.cfg.admin_token:
            raise HTTPException(401, "This change needs the station's admin token.")
        if not is_local(request):
            raise HTTPException(403, "Changes only work from the station computer unless an admin token is set.")
        return None
