"""
Accounts: password hashing, signed session cookies, sign-up rules and the users table.

Passwords are never stored, only a salted scrypt hash. A session is a cookie that carries the user's id and an expiry,
signed with a secret key. The cookie proves who someone is; whether they are still allowed in (an administrator may have
blocked them) is checked against the database, remembered for a few seconds so a page's many requests share one lookup.

Every account has a status: "pending" (registered, waiting for an administrator), "approved" or "blocked". Only approved
accounts can sign in. Administrators are accounts with is_admin set. manage_users.py can grant it, and so can an
administrator on the admin page after typing their own password again.

Changing or resetting a password signs the account out everywhere else: users.sessions_after holds the time of the change,
and a session cookie made before it no longer counts. Password reset links are single use, last RESET_MINUTES, and only a
hash of each one is stored.
"""
import base64
import hashlib
import json
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
STATUSES = ("pending", "approved", "blocked")
ACCESS_TTL = 15          # seconds an account's status is remembered before the database is asked again
ACCESS_STALE_OK = 600    # if the database can't be reached, a status seen this recently is still trusted
RESET_MINUTES = 30       # how long a password reset link works
EMAIL_CHANGE_MINUTES = 60   # how long the link that confirms a new email address works
MAX_NAME = 80


class AuthError(Exception):
    """A problem the person can act on, worded for them. `status` is the HTTP status to answer with."""
    keeps_connection = True          # not a database fault: the reused connection stays usable (dbpool.py)

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


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


def token_issued(token):
    """When a session cookie was made (seconds since 1970), or None if it can't be read. Cookies carry their expiry, and
    every cookie lasts SESSION_DAYS, so the moment it was made follows from that."""
    try:
        return int(str(token).split(".")[2]) - SESSION_DAYS * 86400
    except (ValueError, TypeError, IndexError, AttributeError):
        return None


def session_current(token, user) -> bool:
    """False when the account's password was changed (or a reset was used) after this cookie was made."""
    after = (user or {}).get("sessions_after")
    if not after:
        return True
    issued = token_issued(token)
    return issued is not None and issued >= int(after)


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

# Added for profiles, password changes and the admin page. Created automatically the first time the app needs them
# (and by `python manage_users.py migrate`), so nothing has to be run by hand. Every table has a primary key: managed
# MySQL services such as Aiven refuse tables without one. Times are whole seconds since 1970, so they are right in any
# time zone.
USERS_EXTRA_COLUMNS = (
    ("full_name", "VARCHAR(80) NULL DEFAULT NULL"),
    ("sessions_after", "INT NULL DEFAULT NULL"),
)
PROFILE_EXTRA_COLUMNS = (
    ("invest_range", "VARCHAR(20) NULL DEFAULT NULL"),
    ("interests", "TEXT NULL"),                       # JSON: purposes, follows, sectors, commodities
    ("recent_stocks", "TEXT NULL"),                   # JSON list of the stocks they opened last, newest first
)
EXTRA_TABLES = {
    "password_resets": """CREATE TABLE IF NOT EXISTS password_resets (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  token_hash CHAR(64) NOT NULL,
  created_at INT NOT NULL,
  expires_at INT NOT NULL,
  used_at INT NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_password_resets_token (token_hash),
  KEY idx_password_resets_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "user_profiles": """CREATE TABLE IF NOT EXISTS user_profiles (
  user_id INT NOT NULL,
  city VARCHAR(80) NULL DEFAULT NULL,
  birth_date DATE NULL DEFAULT NULL,
  gender VARCHAR(20) NULL DEFAULT NULL,
  profession VARCHAR(60) NULL DEFAULT NULL,
  notify_prefs TEXT NULL,
  updated_at INT NOT NULL,
  PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "email_changes": """CREATE TABLE IF NOT EXISTS email_changes (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  new_email VARCHAR(254) NOT NULL,
  token_hash CHAR(64) NOT NULL,
  created_at INT NOT NULL,
  expires_at INT NOT NULL,
  used_at INT NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_email_changes_token (token_hash),
  KEY idx_email_changes_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "account_events": """CREATE TABLE IF NOT EXISTS account_events (
  id INT NOT NULL AUTO_INCREMENT,
  at INT NOT NULL,
  kind VARCHAR(30) NOT NULL,
  user_id INT NULL DEFAULT NULL,
  actor_id INT NULL DEFAULT NULL,
  detail VARCHAR(255) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY idx_account_events_at (at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}


def _column_exists(cur, table, column):
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (table, column))
    row = cur.fetchone()
    return bool(row and row[0])


def ensure_schema(conn):
    """Add the profile columns to users and create the password-reset and activity tables. Safe to run any number of
    times. Returns what it did, for manage_users.py to print."""
    cur = conn.cursor()
    done = []
    for column, ddl in USERS_EXTRA_COLUMNS:
        if _column_exists(cur, "users", column):
            done.append(f"users: {column} column already there")
        else:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
            done.append(f"users: added {column} column")
    for name, ddl in EXTRA_TABLES.items():
        cur.execute(ddl)
        done.append(f"{name} table ready")
    for column, ddl in PROFILE_EXTRA_COLUMNS:
        if _column_exists(cur, "user_profiles", column):
            done.append(f"user_profiles: {column} column already there")
        else:
            cur.execute(f"ALTER TABLE user_profiles ADD COLUMN {column} {ddl}")
            done.append(f"user_profiles: added {column} column")
    conn.commit()
    return done



class MySQLStore:
    """Reads and writes the users table. Tests swap `auth.store` for an in-memory stand-in."""

    SCHEMA_RETRY = 60        # seconds before trying again to add the new columns if it failed

    def __init__(self):
        self._schema_ok = False
        self._schema_tried = 0.0
        self._schema_lock = threading.Lock()

    def _extras(self):
        """True once the profile columns and tables exist. Adds them the first time; if that fails (the database is
        down, or the account may not change tables) the app carries on exactly as before and tries again later."""
        if self._schema_ok:
            return True
        with self._schema_lock:
            if self._schema_ok or time.time() - self._schema_tried < self.SCHEMA_RETRY:
                return self._schema_ok
            self._schema_tried = time.time()
            try:
                self._run(ensure_schema)
                self._schema_ok = True
            except Exception as e:                       # noqa: BLE001 - never let this stop a sign-in
                import logging
                logging.getLogger("auth").warning("Could not add the account profile columns yet: %s", e)
        return self._schema_ok

    def _run(self, work):
        """Run `work(conn)` on a reused connection (dbpool.py): opening a fresh one to the database costs about half a second."""
        import mysql.connector
        import dbpool
        try:
            with dbpool.borrow() as conn:
                return work(conn)
        except mysql.connector.Error as e:
            if getattr(e, "errno", None) in (1146, 1054):      # no such table / no such column
                raise AccountsNotSetUp("The accounts tables are missing or out of date. Run: python manage_users.py migrate") from e
            raise

    @staticmethod
    def _user(row):
        if row is None:
            return None
        return {**row, "is_admin": bool(row.get("is_admin"))}

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
            cur.execute("SELECT id, email, password_hash, status, is_admin FROM users WHERE email = %s", (email,))
            return self._user(cur.fetchone())
        return self._run(work)

    def get(self, user_id):
        extras = self._extras()

        def work(conn):
            cur = conn.cursor(dictionary=True)
            if extras:
                cur.execute("SELECT id, email, status, is_admin, full_name, sessions_after FROM users WHERE id = %s", (user_id,))
            else:
                cur.execute("SELECT id, email, status, is_admin FROM users WHERE id = %s", (user_id,))
            return self._user(cur.fetchone())
        return self._run(work)

    def profile(self, user_id):
        """The account as the Settings page shows it."""
        extras = self._extras()

        def work(conn):
            cur = conn.cursor(dictionary=True)
            name = "full_name" if extras else "NULL AS full_name"
            cur.execute(f"SELECT id, email, status, is_admin, {name}, UNIX_TIMESTAMP(created_at) AS created_at, "
                        "UNIX_TIMESTAMP(last_login_at) AS last_login_at FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {**self._user(row), **{k: (int(row[k]) if row[k] is not None else None) for k in ("created_at", "last_login_at")}}
        return self._run(work)

    def set_name(self, user_id, name):
        if not self._extras():
            raise AuthError("Names can't be saved just now. Please try again in a minute.", 503)

        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET full_name = %s WHERE id = %s", (name or None, user_id))
            conn.commit()
        return self._run(work)

    def password_hash(self, user_id):
        def work(conn):
            cur = conn.cursor()
            cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None
        return self._run(work)

    def set_password(self, user_id, password_hash, sessions_after):
        """Save a new password and end every session made before `sessions_after`. Unused reset links stop working."""
        if not self._extras():
            raise AuthError("Passwords can't be changed just now. Please try again in a minute.", 503)

        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET password_hash = %s, sessions_after = %s WHERE id = %s", (password_hash, sessions_after, user_id))
            cur.execute("UPDATE password_resets SET used_at = %s WHERE user_id = %s AND used_at IS NULL", (sessions_after, user_id))
            conn.commit()
        return self._run(work)

    def set_admin(self, user_id, is_admin):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_admin = %s WHERE id = %s", (1 if is_admin else 0, user_id))
            conn.commit()
        return self._run(work)

    def add_reset(self, user_id, token_hash, now, expires_at):
        """Store a new reset link for the account. Any earlier unused link for it stops working."""
        if not self._extras():
            raise AuthError("Password resets aren't available just now. Please try again in a minute.", 503)

        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE password_resets SET used_at = %s WHERE user_id = %s AND used_at IS NULL", (now, user_id))
            cur.execute("DELETE FROM password_resets WHERE expires_at < %s", (now - 7 * 86400,))     # old ones aren't needed
            cur.execute("INSERT INTO password_resets (user_id, token_hash, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                        (user_id, token_hash, now, expires_at))
            conn.commit()
        return self._run(work)

    def find_reset(self, token_hash):
        if not self._extras():
            return None

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, user_id, expires_at, used_at FROM password_resets WHERE token_hash = %s", (token_hash,))
            return cur.fetchone()
        return self._run(work)

    def use_reset(self, reset_id, now):
        """Mark a reset link used. Returns False if it was already used (two tabs submitting at once)."""
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE password_resets SET used_at = %s WHERE id = %s AND used_at IS NULL", (now, reset_id))
            conn.commit()
            return cur.rowcount == 1
        return self._run(work)

    def add_event(self, kind, user_id, actor_id, detail, now):
        if not self._extras():
            return

        def work(conn):
            cur = conn.cursor()
            cur.execute("INSERT INTO account_events (at, kind, user_id, actor_id, detail) VALUES (%s, %s, %s, %s, %s)",
                        (now, kind, user_id, actor_id, detail[:255]))
            conn.commit()
        return self._run(work)

    def recent_stocks(self, user_id):
        if not self._extras():
            return None

        def work(conn):
            cur = conn.cursor()
            cur.execute("SELECT recent_stocks FROM user_profiles WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None
        return self._run(work)

    def details(self, user_id):
        """The optional profile details and notification choices (None when never saved)."""
        if not self._extras():
            return None

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT city, birth_date, gender, profession, notify_prefs, invest_range, interests FROM user_profiles WHERE user_id = %s", (user_id,))
            return cur.fetchone()
        return self._run(work)

    def all_details(self):
        """Profile details of every account that has saved any (for the admin's download)."""
        if not self._extras():
            return {}

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT user_id, city, birth_date, gender, profession, invest_range, interests FROM user_profiles")
            return {r["user_id"]: r for r in cur.fetchall()}
        return self._run(work)

    def save_details(self, user_id, fields, now):
        """Insert or update some of: city, birth_date, gender, profession, notify_prefs, invest_range, interests."""
        if not self._extras():
            raise AuthError("Your details can't be saved just now. Please try again in a minute.", 503)
        allowed = ("city", "birth_date", "gender", "profession", "notify_prefs", "invest_range", "interests", "recent_stocks")
        cols = [c for c in allowed if c in fields]

        def work(conn):
            cur = conn.cursor()
            sets = ", ".join(f"{c} = VALUES({c})" for c in cols) + ", updated_at = VALUES(updated_at)"
            cur.execute(f"INSERT INTO user_profiles (user_id, {', '.join(cols)}, updated_at) VALUES (%s, {', '.join(['%s'] * len(cols))}, %s) "
                        f"ON DUPLICATE KEY UPDATE {sets}", (user_id, *[fields[c] for c in cols], now))
            conn.commit()
        return self._run(work)

    def set_email(self, user_id, email):
        import mysql.connector

        def work(conn):
            cur = conn.cursor()
            try:
                cur.execute("UPDATE users SET email = %s WHERE id = %s", (email, user_id))
                cur.execute("UPDATE email_changes SET used_at = UNIX_TIMESTAMP() WHERE user_id = %s AND used_at IS NULL", (user_id,))
                conn.commit()
            except mysql.connector.IntegrityError as e:
                conn.rollback()
                raise AuthError("Another account already uses that email.", 409) from e
        return self._run(work)

    def add_email_change(self, user_id, new_email, token_hash, now, expires_at):
        """Store a link that confirms a new email. Any earlier unused link for the account stops working."""
        if not self._extras():
            raise AuthError("Emails can't be changed just now. Please try again in a minute.", 503)

        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE email_changes SET used_at = %s WHERE user_id = %s AND used_at IS NULL", (now, user_id))
            cur.execute("DELETE FROM email_changes WHERE expires_at < %s", (now - 7 * 86400,))
            cur.execute("INSERT INTO email_changes (user_id, new_email, token_hash, created_at, expires_at) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, new_email, token_hash, now, expires_at))
            conn.commit()
        return self._run(work)

    def find_email_change(self, token_hash):
        if not self._extras():
            return None

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, user_id, new_email, expires_at, used_at FROM email_changes WHERE token_hash = %s", (token_hash,))
            return cur.fetchone()
        return self._run(work)

    def use_email_change(self, change_id, now):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE email_changes SET used_at = %s WHERE id = %s AND used_at IS NULL", (now, change_id))
            conn.commit()
            return cur.rowcount == 1
        return self._run(work)

    def delete_account(self, user_id):
        """Remove the account and all of its data in one go (the same steps as `manage_users.py delete-user`)."""
        def work(conn):
            import manage_users
            manage_users.delete_account(conn, user_id)
        return self._run(work)

    def list_events(self, limit):
        if not self._extras():
            return []

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT e.id, e.at, e.kind, e.user_id, e.actor_id, e.detail, u.email AS user_email, a.email AS actor_email "
                        "FROM account_events e LEFT JOIN users u ON u.id = e.user_id LEFT JOIN users a ON a.id = e.actor_id "
                        "ORDER BY e.id DESC LIMIT %s", (int(limit),))
            return cur.fetchall()
        return self._run(work)

    def list_users(self):
        """Every account, newest first. Times are seconds since 1970 (or None): the database returns dates without a time
        zone, which a browser would misread, whereas UNIX_TIMESTAMP is exact."""
        name = "full_name" if self._extras() else "NULL AS full_name"

        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT id, email, status, is_admin, {name}, UNIX_TIMESTAMP(created_at) AS created_at, "
                        "UNIX_TIMESTAMP(last_login_at) AS last_login_at, UNIX_TIMESTAMP(reviewed_at) AS reviewed_at FROM users ORDER BY id DESC")
            return [{**self._user(r), **{k: (int(r[k]) if r[k] is not None else None) for k in ("created_at", "last_login_at", "reviewed_at")}} for r in cur.fetchall()]
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
    """{"id", "email", "status", "is_admin"} for the account, or None if it no longer exists. Remembered briefly."""
    return access_cache.get(user_id, lambda: store.get(user_id))


def register(email, password, status="pending", is_admin=False):
    """Create an account and return {"id", "email", "status"}. Raises AuthError with a message to show the person."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    email = normalize_email(email)
    check_email(email)
    check_password(password, email)
    user_id = store.create(email, hash_password(password), status, is_admin)
    return {"id": user_id, "email": email, "status": status}


def record_signup(user):
    """Note a new sign-up in the activity log (kept out of register() so command-line account creation isn't logged as one)."""
    log_event("registered", user["id"], user["id"], user["status"])


def authenticate(email, password):
    """{"id", "email", "status", "is_admin"} when the email and password match an account, else None.
    The caller decides what to do about a pending or blocked account."""
    email = normalize_email(email)
    row = store.find_by_email(email) if email and len(email) <= MAX_EMAIL else None
    if not row:
        _burn_time(password or "")
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    if row.get("status", "approved") == "approved":
        try:
            store.touch_login(row["id"])
        except Exception:
            pass                            # a failed timestamp update must never block a sign-in
    return {"id": row["id"], "email": row["email"], "status": row.get("status", "approved"), "is_admin": bool(row.get("is_admin"))}


def get_user(user_id):
    """The account as it is right now (not the remembered copy)."""
    return store.get(user_id)


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
    if target.get("is_admin") and status == "blocked":
        raise AuthError("Administrators can't be blocked here. Remove their admin rights first with manage_users.py.", 409)
    store.set_status(user_id, status, admin_id)
    access_cache.invalidate(user_id)
    log_event(status, user_id, admin_id)
    return {"id": user_id, "email": target["email"], "status": status}


def set_admin_rights(admin_id, user_id, make_admin, password):
    """Give or take away administrator rights from the admin page. The administrator types their own password again, so a
    browser left signed in can't be used to hand out admin rights. Nobody can remove their own rights here, and the last
    administrator can't be removed, so the app is never left without one."""
    if not verify_password(password or "", store.password_hash(admin_id) or ""):
        raise AuthError("That isn't your password. Nothing was changed.", 403)
    target = store.get(user_id)
    if not target:
        raise AuthError("That account no longer exists.", 404)
    if make_admin:
        if target["status"] != "approved":
            raise AuthError("Only an approved account can be an administrator. Approve it first.", 409)
        if target.get("is_admin"):
            raise AuthError("That account is already an administrator.", 409)
    else:
        if user_id == admin_id:
            raise AuthError("You can't remove your own administrator rights. Another administrator can.", 409)
        if not target.get("is_admin"):
            raise AuthError("That account isn't an administrator.", 409)
        if not any(u["is_admin"] and u["status"] == "approved" and u["id"] != user_id for u in store.list_users()):
            raise AuthError("That is the only administrator, so their rights can't be removed.", 409)
    store.set_admin(user_id, make_admin)
    access_cache.invalidate(user_id)
    log_event("admin_granted" if make_admin else "admin_removed", user_id, admin_id)
    return {"id": user_id, "email": target["email"], "is_admin": bool(make_admin)}


# ── activity log (shown on the admin page) ──

EVENT_KINDS = {
    "registered": "Created an account",
    "approved": "Approved",
    "blocked": "Blocked",
    "admin_granted": "Made an administrator",
    "admin_removed": "Administrator rights removed",
    "password_changed": "Changed their password",
    "password_reset": "Reset their password with an email link",
    "reset_sent": "Password reset email sent",
    "ticket_opened": "Raised a ticket",
    "ticket_status": "Ticket status changed",
    "account_deleted": "Deleted their own account",       # older entries in the activity log
    "account_restored": "Account restored",
    "account_purged": "Account removed for good",
    "learn_changed": "Learn tab changed",
    "email_change_sent": "Asked to change their email",
    "email_changed": "Email changed",
}


def log_event(kind, user_id=None, actor_id=None, detail=""):
    """Record something for the admin page's Activity list. Best effort: a failure here never stops the action itself."""
    try:
        store.add_event(kind, user_id, actor_id, str(detail or ""), int(time.time()))
    except Exception as e:                                   # noqa: BLE001
        import logging
        logging.getLogger("auth").warning("Could not record %s in the activity log: %s", kind, e)


def recent_events(limit=100):
    limit = max(1, min(int(limit), 500))
    out = []
    for e in store.list_events(limit):
        out.append({"id": e["id"], "at": int(e["at"]), "kind": e["kind"], "label": EVENT_KINDS.get(e["kind"], e["kind"]),
                    "user_email": e.get("user_email"), "actor_email": e.get("actor_email"),
                    "by_someone_else": bool(e.get("actor_id")) and e.get("actor_id") != e.get("user_id"), "detail": e.get("detail") or ""})
    return out


# ── profile and password ──

def clean_name(raw) -> str:
    """A display name: printable, single-spaced, at most MAX_NAME characters. Empty means 'no name'."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(raw or ""))
    text = " ".join(text.split())
    if len(text) > MAX_NAME:
        raise AuthError(f"Your name can be at most {MAX_NAME} characters.")
    return text


def get_profile(user_id):
    p = store.profile(user_id)
    if not p:
        return None
    return {"id": p["id"], "email": p["email"], "name": p.get("full_name") or "", "status": p["status"], "is_admin": bool(p["is_admin"]),
            "created_at": p.get("created_at"), "last_login_at": p.get("last_login_at")}


def set_name(user_id, raw):
    name = clean_name(raw)
    store.set_name(user_id, name)
    access_cache.invalidate(user_id)
    return name


def change_password(user_id, current, new, confirm, now=None):
    """Change a signed-in person's password. Every other session of theirs ends; the caller gives this browser a fresh
    cookie. Returns the time of the change."""
    now = int(time.time() if now is None else now)
    if not verify_password(current or "", store.password_hash(user_id) or ""):
        raise AuthError("Your current password isn't right.", 403)
    if (new or "") != (confirm or ""):
        raise AuthError("The two new passwords don't match.")
    user = store.get(user_id)
    if not user:
        raise AuthError("That account no longer exists.", 404)
    check_password(new or "", user["email"])
    if new == current:
        raise AuthError("Your new password must be different from the current one.")
    store.set_password(user_id, hash_password(new), now)
    access_cache.invalidate(user_id)
    log_event("password_changed", user_id, user_id)
    return now


def _reset_hash(token: str) -> str:
    return hashlib.sha256(("reset|" + str(token)).encode("utf-8")).hexdigest()


def start_password_reset(email, actor_id=None, now=None):
    """Make a reset link for the account with this email. Returns (account, token), or None when there is no such
    account. Only the hash of the token is stored, so the database alone can't be used to reset anyone's password."""
    now = int(time.time() if now is None else now)
    email = normalize_email(email)
    if not email or len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
        return None
    row = store.find_by_email(email)
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    store.add_reset(row["id"], _reset_hash(token), now, now + RESET_MINUTES * 60)
    log_event("reset_sent", row["id"], actor_id)
    return {"id": row["id"], "email": row["email"], "status": row.get("status", "approved")}, token


def check_reset_token(token, now=None):
    """The reset record when the link is valid (known, unused, not expired), else None."""
    now = int(time.time() if now is None else now)
    if not token or len(str(token)) > 200:
        return None
    rec = store.find_reset(_reset_hash(token))
    if not rec or rec.get("used_at") or int(rec["expires_at"]) < now:
        return None
    return rec


def finish_password_reset(token, new, confirm, now=None):
    """Set a new password from a reset link. Returns the account's email. The link then stops working, and every
    session made before now ends."""
    now = int(time.time() if now is None else now)
    rec = check_reset_token(token, now)
    if not rec:
        raise AuthError("This reset link has expired or was already used. Please ask for a new one.", 410)
    if (new or "") != (confirm or ""):
        raise AuthError("The two passwords don't match.")
    user = store.get(rec["user_id"])
    if not user:
        raise AuthError("This reset link has expired or was already used. Please ask for a new one.", 410)
    check_password(new or "", user["email"])
    if not store.use_reset(rec["id"], now):
        raise AuthError("This reset link has expired or was already used. Please ask for a new one.", 410)
    store.set_password(rec["user_id"], hash_password(new), now)
    access_cache.invalidate(rec["user_id"])
    log_event("password_reset", rec["user_id"], rec["user_id"])
    return user["email"]


# ── optional profile details (Settings) ──

GENDERS = {"male": "Male", "female": "Female", "other": "Other", "prefer_not": "Prefer not to say"}
PROFESSIONS = ["Student", "Salaried", "Self-employed / Business", "Finance professional", "Retired"]
MAX_CITY, MAX_PROFESSION = 80, 60

# Investing profile (all optional). Amounts are in US dollars, like the prices on the dashboard.
INVEST_RANGES = {
    "under_1k": "Under $1,000", "1k_10k": "$1,000 – $10,000", "10k_50k": "$10,000 – $50,000", "50k_100k": "$50,000 – $100,000",
    "100k_500k": "$100,000 – $500,000", "over_500k": "Over $500,000", "prefer_not": "Prefer not to say",
}
PURPOSES = {
    "learning": "Learning about markets", "long_term": "Long-term investing", "trading": "Short-term trading",
    "tracking": "Tracking my portfolio", "research": "Research or work", "exploring": "Just exploring",
}
FOLLOWS = {"stocks": "Stocks", "commodities": "Commodities", "both": "Both"}
INTEREST_SECTORS = ["Technology", "Communication Services", "Healthcare", "Financials", "Consumer Discretionary", "Consumer Staples",
                    "Energy", "Industrials", "Materials", "Real Estate", "Utilities"]
INTEREST_COMMODITIES = ["Gold", "Silver", "Platinum & palladium", "Copper & industrial metals", "Oil", "Natural gas",
                        "Grains (corn, wheat, soybeans)", "Coffee, sugar, cotton & cocoa", "Livestock"]


def _pick_many(raw, allowed, what):
    """A list of choices from `allowed`, in the standard order, without repeats."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, (list, tuple)):
        raise AuthError(f"Please choose {what} from the list.")
    keys = list(allowed)
    chosen = {str(x) for x in raw}
    if chosen - set(keys):
        raise AuthError(f"Please choose {what} from the list.")
    return [k for k in keys if k in chosen]


MAX_OTHER = 60
OTHER_KEYS = ("purposes_other", "sectors_other", "commodities_other")


def clean_interests(invest_range, purposes, follows, sectors, commodities, others=None):
    """(invest_range or None, interests dict) after checking every choice is one of ours. `others` holds what the
    person typed into the "Other" boxes ({"purposes_other": "...", ...})."""
    others = others or {}
    typed = {k: _clean_text(others.get(k), MAX_OTHER, "Your own answer") for k in OTHER_KEYS}
    invest_range = str(invest_range or "").strip()
    if invest_range and invest_range not in INVEST_RANGES:
        raise AuthError("Please choose an investing range from the list.")
    follows = str(follows or "").strip()
    if follows and follows not in FOLLOWS:
        raise AuthError("Please choose Stocks, Commodities or Both.")
    return invest_range or None, {"purposes": _pick_many(purposes, PURPOSES, "your purpose"), "follows": follows,
                                  "sectors": _pick_many(sectors, INTEREST_SECTORS, "sectors"),
                                  "commodities": _pick_many(commodities, INTEREST_COMMODITIES, "commodities"), **typed}


def interests_from(raw):
    """The saved interests (JSON) as a dict with every key present."""
    out = {"purposes": [], "follows": "", "sectors": [], "commodities": [], **{k: "" for k in OTHER_KEYS}}
    if raw:
        try:
            saved = json.loads(raw)
            if isinstance(saved, dict):
                out["purposes"] = [p for p in saved.get("purposes") or [] if p in PURPOSES]
                out["follows"] = saved.get("follows") if saved.get("follows") in FOLLOWS else ""
                out["sectors"] = [x for x in saved.get("sectors") or [] if x in INTEREST_SECTORS]
                out["commodities"] = [x for x in saved.get("commodities") or [] if x in INTEREST_COMMODITIES]
                for k in OTHER_KEYS:
                    out[k] = str(saved.get(k) or "")[:MAX_OTHER]
        except (ValueError, TypeError, AttributeError):
            pass
    return out

# Notification choices. Everything is on until someone turns it off.
NOTIFY_DEFAULTS = {
    "email_ticket_replies": True,    # email when the Aymetrix team replies to a ticket or changes its status
    "bell_price_alerts": True,       # bell: a price alert was reached
    "bell_news": True,               # bell: important market news
    "bell_events": True,             # bell: economic events and their results
    "bell_support": True,            # bell: Help Desk replies
    "desktop": True,                 # pop-ups outside the browser (when the browser allows them)
}


def age_on(birth, today=None):
    """Whole years from `birth` (a date) to today."""
    import datetime as _dt
    today = today or _dt.date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _clean_text(raw, most, what):
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(raw or "")).split())
    if len(text) > most:
        raise AuthError(f"{what} can be at most {most} characters.")
    return text


def clean_birth_date(raw, today=None):
    """'' for none, else a date in the past (after 1900). Accepts YYYY-MM-DD, which is what the date box sends."""
    import datetime as _dt
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        d = _dt.date.fromisoformat(text)
    except ValueError:
        raise AuthError("Please enter your date of birth as a date (day, month and year).")
    today = today or _dt.date.today()
    if d >= today:
        raise AuthError("Your date of birth must be in the past.")
    if d.year < 1900:
        raise AuthError("Please check the year of your date of birth.")
    return d


def clean_profession(raw):
    text = _clean_text(raw, MAX_PROFESSION, "Profession")
    for p in PROFESSIONS:                          # keep the standard spelling for the standard choices
        if text.lower() == p.lower():
            return p
    return text


def _prefs_from(raw):
    prefs = dict(NOTIFY_DEFAULTS)
    if raw:
        try:
            saved = json.loads(raw)
            prefs.update({k: bool(v) for k, v in saved.items() if k in NOTIFY_DEFAULTS})
        except (ValueError, TypeError, AttributeError):
            pass
    return prefs


def get_details(user_id, today=None):
    """City, date of birth (and the age from it), gender, profession and notification choices."""
    row = store.details(user_id) or {}
    birth = row.get("birth_date")
    return {"city": row.get("city") or "", "birth_date": birth.isoformat() if birth else "",
            "age": age_on(birth, today) if birth else None, "gender": row.get("gender") or "",
            "profession": row.get("profession") or "", "invest_range": row.get("invest_range") or "",
            **interests_from(row.get("interests")), "notifications": _prefs_from(row.get("notify_prefs"))}


def save_profile(user_id, name, city, birth_date, gender, profession, now=None, invest_range=None, purposes=None, follows=None,
                 sectors=None, commodities=None, others=None):
    """Save the Profile card: name plus the optional details and investing profile. Empty means 'not given'."""
    now = int(time.time() if now is None else now)
    name = clean_name(name)
    city = _clean_text(city, MAX_CITY, "Region / city")
    birth = clean_birth_date(birth_date)
    gender = str(gender or "").strip()
    if gender and gender not in GENDERS:
        raise AuthError("Please choose a gender from the list.")
    profession = clean_profession(profession)
    invest_range, interests = clean_interests(invest_range, purposes, follows, sectors, commodities, others)
    store.set_name(user_id, name)
    store.save_details(user_id, {"city": city or None, "birth_date": birth, "gender": gender or None, "profession": profession or None,
                                 "invest_range": invest_range, "interests": json.dumps(interests, sort_keys=True)}, now)
    access_cache.invalidate(user_id)
    return {"name": name, **{k: v for k, v in get_details(user_id).items() if k != "notifications"}}


def save_notifications(user_id, choices, now=None):
    """Save the notification switches. Unknown keys are ignored; missing ones keep their current value."""
    now = int(time.time() if now is None else now)
    prefs = get_details(user_id)["notifications"]
    for k, v in (choices or {}).items():
        if k in NOTIFY_DEFAULTS and isinstance(v, bool):
            prefs[k] = v
    store.save_details(user_id, {"notify_prefs": json.dumps(prefs, sort_keys=True)}, now)
    return prefs


def wants(user_id, key) -> bool:
    """Whether this person wants a kind of notification. If their choices can't be read, the default (on) is used."""
    try:
        return bool(get_details(user_id)["notifications"].get(key, NOTIFY_DEFAULTS.get(key, True)))
    except Exception:                                        # noqa: BLE001
        return NOTIFY_DEFAULTS.get(key, True)


# ── recently viewed stocks (the strip on the Market page) ──

MAX_RECENT = 15
_TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")


def recent_stocks(user_id):
    """The stocks this person opened last, newest first."""
    raw = store.recent_stocks(user_id)
    try:
        items = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        items = []
    return [t for t in items if isinstance(t, str) and _TICKER_RE.match(t)][:MAX_RECENT]


def add_recent_stock(user_id, ticker, now=None):
    """Put a stock at the front of the list (moving it if it's already there). Returns the new list."""
    t = str(ticker or "").strip().upper()
    if not _TICKER_RE.match(t):
        raise AuthError("That isn't a stock symbol.")
    items = [t] + [x for x in recent_stocks(user_id) if x != t]
    items = items[:MAX_RECENT]
    store.save_details(user_id, {"recent_stocks": json.dumps(items)}, int(time.time() if now is None else now))
    return items


def remove_recent_stock(user_id, ticker, now=None):
    """Take one stock off the list (nothing changes if it isn't there). Returns the new list."""
    t = str(ticker or "").strip().upper()
    if not _TICKER_RE.match(t):
        raise AuthError("That isn't a stock symbol.")
    old = recent_stocks(user_id)
    items = [x for x in old if x != t]
    if items != old:
        store.save_details(user_id, {"recent_stocks": json.dumps(items) if items else None}, int(time.time() if now is None else now))
    return items


def clear_recent_stocks(user_id, now=None):
    store.save_details(user_id, {"recent_stocks": None}, int(time.time() if now is None else now))
    return []


# ── changing the email address ──

def _email_change_hash(token: str) -> str:
    return hashlib.sha256(("email|" + str(token)).encode("utf-8")).hexdigest()


def _check_new_email(user, new_email):
    """The cleaned new address, or AuthError. An address any other account uses is refused."""
    email = normalize_email(new_email)
    check_email(email)
    if email == user["email"]:
        raise AuthError("That is already your email.")
    other = store.find_by_email(email)
    if other and other["id"] != user["id"]:
        raise AuthError("Another account already uses that email.", 409)
    return email


def start_email_change(user_id, password, new_email, now=None):
    """Settings: the person types a new email and their password. Nothing changes until they click the link sent to
    the new address (which proves it is theirs). Returns (account, new_email, token)."""
    now = int(time.time() if now is None else now)
    if not verify_password(password or "", store.password_hash(user_id) or ""):
        raise AuthError("That isn't your password. Your email wasn't changed.", 403)
    user = store.get(user_id)
    if not user:
        raise AuthError("That account no longer exists.", 404)
    email = _check_new_email(user, new_email)
    token = secrets.token_urlsafe(32)
    store.add_email_change(user_id, email, _email_change_hash(token), now, now + EMAIL_CHANGE_MINUTES * 60)
    log_event("email_change_sent", user_id, user_id, email)
    return user, email, token


def check_email_change_token(token, now=None):
    now = int(time.time() if now is None else now)
    if not token or len(str(token)) > 200:
        return None
    rec = store.find_email_change(_email_change_hash(token))
    if not rec or rec.get("used_at") or int(rec["expires_at"]) < now:
        return None
    return rec


def finish_email_change(token, now=None):
    """The link in the email was clicked: switch the account to the new address. Everything else (portfolio,
    transactions, tickets, settings) stays, because it belongs to the account, not to the email.
    Returns {"id", "old_email", "new_email"}."""
    now = int(time.time() if now is None else now)
    expired = AuthError("This link has expired or was already used. You can ask for a new one in Settings.", 410)
    rec = check_email_change_token(token, now)
    if not rec:
        raise expired
    user = store.get(rec["user_id"])
    if not user:
        raise expired
    email = _check_new_email(user, rec["new_email"])
    if not store.use_email_change(rec["id"], now):
        raise expired
    store.set_email(user["id"], email)
    access_cache.invalidate(user["id"])
    log_event("email_changed", user["id"], user["id"], f"{user['email']} to {email}")
    return {"id": user["id"], "old_email": user["email"], "new_email": email}


def admin_change_email(admin_id, user_id, new_email):
    """An administrator changes someone's email (when they asked through a Help Desk ticket)."""
    user = store.get(user_id)
    if not user:
        raise AuthError("That account no longer exists.", 404)
    email = _check_new_email(user, new_email)
    store.set_email(user_id, email)
    access_cache.invalidate(user_id)
    log_event("email_changed", user_id, admin_id, f"{user['email']} to {email}")
    return {"id": user_id, "old_email": user["email"], "new_email": email}
