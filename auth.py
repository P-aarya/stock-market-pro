"""
Accounts: password hashing, signed session cookies, sign-up rules and the users table.

Passwords are never stored, only a salted scrypt hash. A session is a cookie that carries the user's id and an expiry,
signed with a secret key, so the server can trust it without a database lookup on every request.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque

SESSION_DAYS = 14
MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_EMAIL = 254
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COMMON_PASSWORDS = {"password", "password1", "12345678", "123456789", "1234567890", "qwertyuiop", "iloveyou", "11111111", "00000000",
                    "abcd1234", "letmein1", "welcome1", "admin123", "passw0rd", "qwerty123", "football", "baseball", "trustno1"}
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


class AuthError(Exception):
    """A problem the person can act on, worded for them. `status` is the HTTP status to answer with."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


class AccountsNotSetUp(Exception):
    """The users table doesn't exist yet: `python manage_users.py migrate` has not been run."""


# ── passwords ─────────────────────────────────────────────

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


_dummy_hash = None


def _burn_time(password: str):
    """Hash once even when the email is unknown, so a reply's speed doesn't reveal whether an account exists."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("this is not anybody's password")
    verify_password(password, _dummy_hash)


# ── what a valid email and password look like ─────────────

def normalize_email(raw) -> str:
    return str(raw or "").strip().lower()


def check_email(email: str):
    if not email or len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
        raise AuthError("Please enter a valid email address.")


def check_password(password: str, email: str = ""):
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Your password needs at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Your password can be at most {MAX_PASSWORD} characters.")
    if password.lower() in COMMON_PASSWORDS or (email and password.lower() == email):
        raise AuthError("That password is too easy to guess. Please choose another.")


# ── sessions ──────────────────────────────────────────────

_secret = None
_secret_lock = threading.Lock()
SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app_secret")


def _load_secret() -> bytes:
    """APP_SECRET from the environment, else a random key kept in .app_secret so logins survive a restart."""
    configured = os.getenv("APP_SECRET")
    if configured:
        return configured.encode("utf-8")
    try:
        fd = os.open(SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    except OSError:
        return secrets.token_bytes(32)      # read-only disk: sessions then last until the server restarts
    try:
        with open(SECRET_FILE, "r") as f:
            value = f.read().strip()
        if len(value) >= 32:
            return value.encode("utf-8")
    except OSError:
        pass
    return secrets.token_bytes(32)


def session_key() -> bytes:
    global _secret
    with _secret_lock:
        if _secret is None:
            _secret = hashlib.sha256(b"smp-session|" + _load_secret()).digest()
        return _secret


def _sign(payload: str) -> str:
    return hmac.new(session_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()


def make_token(user_id: int, now=None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_DAYS * 86400)
    payload = f"v1.{int(user_id)}.{expires}"
    return f"{payload}.{_sign(payload)}"


def read_token(token, now=None):
    """The user id a valid, unexpired session cookie belongs to, or None."""
    try:
        version, user_id, expires, signature = str(token).split(".")
        payload = f"{version}.{user_id}.{expires}"
        if version != "v1" or not hmac.compare_digest(signature, _sign(payload)):
            return None
        if int(expires) <= (time.time() if now is None else now):
            return None
        return int(user_id)
    except (ValueError, TypeError, AttributeError):
        return None


# ── limits on repeated attempts ───────────────────────────

class RateLimiter:
    """At most `limit` recorded events per key in any `window` seconds."""
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key, now):
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return hits

    def blocked(self, key, now=None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            return len(self._trim(key, now)) >= self.limit

    def record(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._trim(key, now).append(now)
            if len(self._hits) > 5000:      # forget keys with no recent activity
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    del self._hits[k]

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def reset(self):
        with self._lock:
            self._hits.clear()


# ── the users table ───────────────────────────────────────

class MySQLStore:
    """Reads and writes the users table. Tests swap `auth.store` for an in-memory stand-in."""

    def _run(self, work):
        import mysql.connector
        from screener import get_db_connection
        conn = get_db_connection()
        try:
            return work(conn)
        except mysql.connector.Error as e:
            if getattr(e, "errno", None) == 1146:
                raise AccountsNotSetUp("The accounts tables are missing. Run: python manage_users.py migrate") from e
            raise
        finally:
            conn.close()

    def create(self, email, password_hash):
        import mysql.connector

        def work(conn):
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s)", (email, password_hash))
                conn.commit()
                return cur.lastrowid
            except mysql.connector.IntegrityError as e:
                conn.rollback()
                raise AuthError("An account with that email already exists. Try signing in instead.", 409) from e
        return self._run(work)

    def find_by_email(self, email):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, email, password_hash FROM users WHERE email = %s", (email,))
            return cur.fetchone()
        return self._run(work)

    def get(self, user_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()
        return self._run(work)

    def touch_login(self, user_id):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,))
            conn.commit()
        return self._run(work)


store = MySQLStore()


def register(email, password):
    """Create an account and return {"id", "email"}. Raises AuthError with a message to show the person."""
    email = normalize_email(email)
    check_email(email)
    check_password(password, email)
    user_id = store.create(email, hash_password(password))
    return {"id": user_id, "email": email}


def authenticate(email, password):
    """{"id", "email"} when the email and password match an account, else None."""
    email = normalize_email(email)
    row = store.find_by_email(email) if email and len(email) <= MAX_EMAIL else None
    if not row:
        _burn_time(password or "")
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    try:
        store.touch_login(row["id"])
    except Exception:
        pass                                # a failed timestamp update must never block a sign-in
    return {"id": row["id"], "email": row["email"]}


def get_user(user_id):
    return store.get(user_id)
