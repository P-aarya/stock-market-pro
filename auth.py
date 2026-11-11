"""
Accounts: password hashing, signed session cookies, sign-up rules and the users table.

Passwords are never stored, only a salted scrypt hash. A session is a cookie that carries the user's id and an expiry,
signed with a secret key. The cookie proves who someone is; whether they are still allowed in (an administrator may have
blocked them) is checked against the database, remembered for a few seconds so a page's many requests share one lookup.

Every account has a status: "pending" (registered, waiting for an administrator), "approved", "blocked" or "deleted". Only
approved accounts can sign in, so a deleted account is locked out everywhere without any other check having to know about it.
A person can delete their own account: it is kept for DELETION_GRACE_DAYS days, during which signing in offers to restore it,
and then it is removed for good together with everything it owns. Administrators are accounts with is_admin set, which only
manage_users.py can grant. Every account also has a subscription tier (see tiers.py) that decides which parts of the app it
can use.
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
STATUSES = ("pending", "approved", "blocked", "deleted")
DELETION_GRACE_DAYS = 30     # how long a deleted account can still be restored
RESTORE_LINK_MINUTES = 15    # how long the "restore my account" button stays valid once the right password was given
ACCESS_TTL = 15          # seconds an account's status is remembered before the database is asked again
ACCESS_STALE_OK = 600    # if the database can't be reached, a status seen this recently is still trusted


def now() -> float:
    """The current time. Everything that depends on the grace period asks here, so tests can move the clock."""
    return time.time()


class AuthError(Exception):
    """A problem the person can act on, worded for them. `status` is the HTTP status to answer with."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


class WrongPassword(AuthError):
    """The password given to confirm an action isn't the account's (so the caller can count it as a failed attempt)."""


class AccountsNotSetUp(Exception):
    """The accounts tables are missing or out of date: `python manage_users.py migrate` has not been run."""


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


def make_restore_token(user_id: int, at=None) -> str:
    """Proof that this person has just given the right password for a deleted account, good for RESTORE_LINK_MINUTES. It is
    signed like a session but under a different version tag, so it can never be used as a session cookie (or the reverse)."""
    expires = int((now() if at is None else at) + RESTORE_LINK_MINUTES * 60)
    payload = f"r1.{int(user_id)}.{expires}"
    return f"{payload}.{_sign(payload)}"


def read_restore_token(token, at=None):
    """The user id a valid, unexpired restore token belongs to, or None."""
    try:
        version, user_id, expires, signature = str(token).split(".")
        payload = f"{version}.{user_id}.{expires}"
        if version != "r1" or not hmac.compare_digest(signature, _sign(payload)):
            return None
        if int(expires) <= (now() if at is None else at):
            return None
        return int(user_id)
    except (ValueError, TypeError, AttributeError):
        return None


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
            if getattr(e, "errno", None) in (1146, 1054):      # no such table / no such column
                raise AccountsNotSetUp("The accounts tables are missing or out of date. Run: python manage_users.py migrate") from e
            raise
        finally:
            conn.close()

    @staticmethod
    def _user(row):
        if row is None:
            return None
        user = {**row, "is_admin": bool(row.get("is_admin"))}
        if "tier" in user:
            user["tier"] = int(user["tier"]) if user["tier"] is not None else None
        for key in ("created_at", "deleted_at"):
            if key in user:
                user[key] = int(user[key]) if user[key] is not None else None
        return user

    def create(self, email, password_hash, status="pending", is_admin=False):
        import mysql.connector

        def work(conn):
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO users (email, password_hash, status, is_admin) VALUES (%s, %s, %s, %s)",
                            (email, password_hash, status, 1 if is_admin else 0))
                conn.commit()
                return cur.lastrowid
            except mysql.connector.IntegrityError as e:
                conn.rollback()
                raise AuthError("An account with that email already exists. Try signing in instead.", 409) from e
        return self._run(work)

    def find_by_email(self, email):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, email, password_hash, status, is_admin, UNIX_TIMESTAMP(deleted_at) AS deleted_at FROM users WHERE email = %s", (email,))
            return self._user(cur.fetchone())
        return self._run(work)

    def get(self, user_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, email, status, is_admin, tier, UNIX_TIMESTAMP(created_at) AS created_at, UNIX_TIMESTAMP(deleted_at) AS deleted_at FROM users WHERE id = %s", (user_id,))
            return self._user(cur.fetchone())
        return self._run(work)

    def list_users(self):
        """Every account, newest first. Times are seconds since 1970 (or None): the database returns dates without a time
        zone, which a browser would misread, whereas UNIX_TIMESTAMP is exact."""
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, email, status, is_admin, tier, UNIX_TIMESTAMP(created_at) AS created_at, UNIX_TIMESTAMP(last_login_at) AS last_login_at, "
                        "UNIX_TIMESTAMP(reviewed_at) AS reviewed_at, UNIX_TIMESTAMP(deleted_at) AS deleted_at FROM users ORDER BY id DESC")
            return [{**self._user(r), **{k: (int(r[k]) if r[k] is not None else None) for k in ("created_at", "last_login_at", "reviewed_at", "deleted_at")}} for r in cur.fetchall()]
        return self._run(work)

    def set_status(self, user_id, status, reviewed_by):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET status = %s, reviewed_at = NOW(), reviewed_by = %s WHERE id = %s", (status, reviewed_by, user_id))
            conn.commit()
        return self._run(work)

    def touch_login(self, user_id):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,))
            conn.commit()
        return self._run(work)

    def mark_deleted(self, user_id, when):
        """Put an approved account into the deleted state, stamped `when` (seconds since 1970). True if it changed."""
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET status = 'deleted', deleted_at = FROM_UNIXTIME(%s) WHERE id = %s AND status = 'approved'", (when, user_id))
            conn.commit()
            return cur.rowcount == 1
        return self._run(work)

    def restore(self, user_id):
        """Bring a deleted account back as an approved one. True if it changed."""
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET status = 'approved', deleted_at = NULL WHERE id = %s AND status = 'deleted'", (user_id,))
            conn.commit()
            return cur.rowcount == 1
        return self._run(work)

    def other_admins(self, user_id):
        """How many approved administrators there are besides `user_id`."""
        def work(conn):
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND status = 'approved' AND id <> %s", (user_id,))
            return int(cur.fetchone()[0])
        return self._run(work)

    def purge_if_expired(self, user_id, cutoff):
        """Permanently remove one deleted account (and everything it owns) if its deletion was at or before `cutoff`."""
        import account_data
        return self._run(lambda conn: account_data.purge_one(conn, user_id, cutoff))

    def purge_expired(self, cutoff):
        """Permanently remove every deleted account whose deletion was at or before `cutoff`. Returns how many."""
        import account_data
        return self._run(lambda conn: account_data.purge_expired(conn, cutoff))


store = MySQLStore()


class AccessCache:
    """Remembers each account's status for `ttl` seconds. Many requests arriving together (a page load makes about twenty)
    share one database lookup. If the database can't be reached, a status seen recently enough is still trusted so a brief
    outage doesn't sign everyone out."""

    def __init__(self, ttl=ACCESS_TTL, stale_ok=ACCESS_STALE_OK, clock=time.time):
        self.ttl, self.stale_ok, self.clock = ttl, stale_ok, clock
        self._entries, self._locks = {}, defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def get(self, user_id, load):
        entry = self._entries.get(user_id)
        if entry and self.clock() - entry[0] < self.ttl:
            return entry[1]
        with self._guard:
            lock = self._locks[user_id]
        with lock:                                  # one lookup per account, however many requests are waiting
            entry = self._entries.get(user_id)
            if entry and self.clock() - entry[0] < self.ttl:
                return entry[1]
            try:
                value = load()
            except Exception:
                if entry and self.clock() - entry[0] < self.stale_ok:
                    return entry[1]
                raise
            self._entries[user_id] = (self.clock(), value)
            if len(self._entries) > 5000:
                for k in [k for k, (t, _) in self._entries.items() if self.clock() - t > self.stale_ok]:
                    self._entries.pop(k, None)
            return value

    def invalidate(self, user_id):
        self._entries.pop(user_id, None)

    def clear(self):
        self._entries.clear()


access_cache = AccessCache()


def access_for(user_id):
    """{"id", "email", "status", "is_admin", "tier", "created_at"} for the account, or None if it no longer exists. Remembered briefly."""
    return access_cache.get(user_id, lambda: store.get(user_id))


def register(email, password, status="pending", is_admin=False):
    """Create an account and return {"id", "email", "status"}. Raises AuthError with a message to show the person."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    email = normalize_email(email)
    check_email(email)
    check_password(password, email)
    _clear_expired_deleted_account(store.find_by_email(email))          # an address whose deleted account ran out of time is free again
    user_id = store.create(email, hash_password(password), status, is_admin)
    return {"id": user_id, "email": email, "status": status}


def authenticate(email, password):
    """{"id", "email", "status", "is_admin", "deleted_at"} when the email and password match an account, else None.
    The caller decides what to do about a pending, blocked or deleted account. A deleted account whose grace period is over
    counts as no account at all (and is removed on the spot)."""
    email = normalize_email(email)
    row = store.find_by_email(email) if email and len(email) <= MAX_EMAIL else None
    if not row:
        _burn_time(password or "")
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    if row.get("status") == "deleted" and not restorable(row.get("deleted_at")):
        _clear_expired_deleted_account(row)
        return None
    if row.get("status", "approved") == "approved":
        try:
            store.touch_login(row["id"])
        except Exception:
            pass                            # a failed timestamp update must never block a sign-in
    return {"id": row["id"], "email": row["email"], "status": row.get("status", "approved"), "is_admin": bool(row.get("is_admin")),
            "deleted_at": row.get("deleted_at")}


def get_user(user_id):
    """The account as it is right now (not the remembered copy)."""
    return store.get(user_id)


# ── deleting your own account, and getting it back ──

def grace_seconds() -> int:
    return DELETION_GRACE_DAYS * 86400


def restorable(deleted_at, at=None) -> bool:
    """Whether an account deleted at `deleted_at` can still be restored. A deleted account with no date can't be."""
    return deleted_at is not None and (now() if at is None else at) < deleted_at + grace_seconds()


def days_left(deleted_at, at=None) -> int:
    """Whole days (rounded up, at least 1) left to restore an account deleted at `deleted_at`."""
    remaining = deleted_at + grace_seconds() - (now() if at is None else at)
    return max(1, -(-int(remaining) // 86400))


def _clear_expired_deleted_account(row):
    """Remove `row`'s account for good if it is a deleted one past its grace period. Best effort: failing here must not stop a
    sign-in or sign-up, and the periodic clean-up will catch it later."""
    if not row or row.get("status") != "deleted" or restorable(row.get("deleted_at")):
        return
    try:
        store.purge_if_expired(row["id"], int(now()) - grace_seconds())
        access_cache.invalidate(row["id"])
    except Exception:
        pass


def delete_own_account(user_id, password):
    """Delete the signed-in person's account after they confirm with their password. Returns {"deleted_at", "remove_at"}.
    Raises WrongPassword, or AuthError with a message to show them. Nothing is erased yet: the account is locked and kept
    for DELETION_GRACE_DAYS days so it can be restored."""
    user = store.get(user_id)
    if not user or user["status"] != "approved":
        raise AuthError("Please sign in.", 401)
    row = store.find_by_email(user["email"])
    if not row or not verify_password(password or "", row["password_hash"]):
        raise WrongPassword("That password isn't right.", 403)
    if user["is_admin"] and store.other_admins(user_id) == 0:
        raise AuthError("You are the only administrator. Make someone else an administrator first (python manage_users.py make-admin EMAIL), or nobody could manage access.", 409)
    when = int(now())
    if not store.mark_deleted(user_id, when):
        raise AuthError("This account can't be deleted right now. Please refresh the page and try again.", 409)
    access_cache.invalidate(user_id)
    return {"deleted_at": when, "remove_at": when + grace_seconds()}


def restore_account(user_id):
    """Bring a deleted account back, if its grace period isn't over. Returns {"id", "email"}. Raises AuthError otherwise."""
    user = store.get(user_id)
    if not user:
        raise AuthError("That account no longer exists.", 404)
    if user["status"] != "deleted":
        raise AuthError("That account isn't waiting to be restored. Please sign in.", 409)
    if not restorable(user.get("deleted_at")):
        _clear_expired_deleted_account(user)
        raise AuthError(f"The {DELETION_GRACE_DAYS} days to restore this account are over, so it has been permanently deleted.", 410)
    if not store.restore(user_id):
        raise AuthError("That account can't be restored right now. Please try signing in again.", 409)
    access_cache.invalidate(user_id)
    try:
        store.touch_login(user_id)
    except Exception:
        pass
    return {"id": user_id, "email": user["email"]}


def purge_expired(at=None):
    """Permanently remove every deleted account past its grace period, with everything it owns. Returns how many."""
    return store.purge_expired(int(now() if at is None else at) - grace_seconds())


# ── what an administrator can do ──

def list_users():
    """Every account, newest first, plus how many are in each status."""
    users = store.list_users()
    counts = {s: sum(1 for u in users if u["status"] == s) for s in STATUSES}
    return {"users": users, "counts": {**counts, "total": len(users)}}


def pending_count():
    return sum(1 for u in store.list_users() if u["status"] == "pending")


def set_user_status(admin_id, user_id, status):
    """Approve or block an account. Takes effect at once on this server; on others within ACCESS_TTL seconds."""
    if status not in ("approved", "blocked"):
        raise AuthError("Status must be 'approved' or 'blocked'.", 400)
    target = store.get(user_id)
    if not target:
        raise AuthError("That account no longer exists.", 404)
    if target["status"] == "deleted":
        raise AuthError(f"That account was deleted by its owner. They can restore it by signing in within {DELETION_GRACE_DAYS} days of deleting it; after that it is removed for good.", 409)
    if target.get("is_admin") and status == "blocked":
        raise AuthError("Administrators can't be blocked here. Remove their admin rights first with manage_users.py.", 409)
    store.set_status(user_id, status, admin_id)
    access_cache.invalidate(user_id)
    return {"id": user_id, "email": target["email"], "status": status}
