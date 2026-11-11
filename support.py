"""
Help Desk: people raise tickets from Settings, administrators answer them in the Admin Center.

A ticket has a category, a subject, a status (open, in progress, resolved) and a conversation. When an administrator replies
or changes the status, the person gets a notification in the dashboard's bell and an email. When someone raises a ticket or
replies to one, the support inbox (SUPPORT_EMAIL) gets an email.

The tables are created the first time they are needed. Every table has a primary key (Aiven requires it) and times are
whole seconds since 1970. Tests swap `support.store` for an in-memory stand-in.
"""
import functools
import logging
import threading
import time

log = logging.getLogger("support")

CATEGORIES = {
    "account": "Login / Account",
    "email": "Change my email",
    "data": "Data issue",
    "predictions": "Predictions",
    "portfolio": "Portfolio / Transactions",
    "bug": "Something isn't working",
    "other": "Other",
}
STATUSES = {"open": "Open", "in_progress": "In progress", "resolved": "Resolved"}
MAX_SUBJECT, MIN_SUBJECT = 120, 3
MAX_MESSAGE = 4000
NOTICE_DAYS = 30            # undelivered bell notifications older than this are dropped

TABLES = {
    "support_tickets": """CREATE TABLE IF NOT EXISTS support_tickets (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  category VARCHAR(20) NOT NULL,
  subject VARCHAR(120) NOT NULL,
  status VARCHAR(12) NOT NULL DEFAULT 'open',
  created_at INT NOT NULL,
  updated_at INT NOT NULL,
  user_unread TINYINT(1) NOT NULL DEFAULT 0,
  admin_unread TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  KEY idx_support_tickets_user (user_id),
  KEY idx_support_tickets_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "support_messages": """CREATE TABLE IF NOT EXISTS support_messages (
  id INT NOT NULL AUTO_INCREMENT,
  ticket_id INT NOT NULL,
  author_id INT NOT NULL,
  is_staff TINYINT(1) NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  created_at INT NOT NULL,
  PRIMARY KEY (id),
  KEY idx_support_messages_ticket (ticket_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "user_notifications": """CREATE TABLE IF NOT EXISTS user_notifications (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  kind VARCHAR(20) NOT NULL,
  title VARCHAR(160) NOT NULL,
  body VARCHAR(300) NOT NULL DEFAULT '',
  link VARCHAR(200) NOT NULL DEFAULT '',
  created_at INT NOT NULL,
  delivered_at INT NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_user_notifications_user (user_id, delivered_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}


class SupportError(Exception):
    """A problem the person can act on, worded for them."""
    keeps_connection = True          # not a database fault: the reused connection stays usable (dbpool.py)

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def ensure_tables(conn):
    cur = conn.cursor()
    for ddl in TABLES.values():
        cur.execute(ddl)
    conn.commit()
    return [f"{name} table ready" for name in TABLES]


class MySQLSupportStore:
    def __init__(self):
        self._ready = False
        self._lock = threading.Lock()

    def unit(self):
        """Everything inside `with store.unit():` shares one database connection (see dbpool.py)."""
        import dbpool
        return dbpool.borrow()

    def _run(self, work):
        import dbpool
        with dbpool.borrow() as conn:
            if not self._ready:
                with self._lock:
                    if not self._ready:
                        ensure_tables(conn)
                        self._ready = True
            return work(conn)

    _TICKET_COLS = "t.id, t.user_id, t.category, t.subject, t.status, t.created_at, t.updated_at, t.user_unread, t.admin_unread"

    def create_ticket(self, user_id, category, subject, body, now):
        def work(conn):
            cur = conn.cursor()
            cur.execute("INSERT INTO support_tickets (user_id, category, subject, status, created_at, updated_at, user_unread, admin_unread) "
                        "VALUES (%s, %s, %s, 'open', %s, %s, 0, 1)", (user_id, category, subject, now, now))
            ticket_id = cur.lastrowid
            cur.execute("INSERT INTO support_messages (ticket_id, author_id, is_staff, body, created_at) VALUES (%s, %s, 0, %s, %s)",
                        (ticket_id, user_id, body, now))
            conn.commit()
            return ticket_id
        return self._run(work)

    def get_ticket(self, ticket_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT {self._TICKET_COLS}, u.email AS user_email FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
                        "WHERE t.id = %s", (ticket_id,))
            return cur.fetchone()
        return self._run(work)

    def messages(self, ticket_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, author_id, is_staff, body, created_at FROM support_messages WHERE ticket_id = %s ORDER BY id", (ticket_id,))
            return cur.fetchall()
        return self._run(work)

    def tickets_for_user(self, user_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT {self._TICKET_COLS}, (SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS message_count "
                        "FROM support_tickets t WHERE t.user_id = %s ORDER BY t.updated_at DESC, t.id DESC LIMIT 200", (user_id,))
            return cur.fetchall()
        return self._run(work)

    def all_tickets(self):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT {self._TICKET_COLS}, u.email AS user_email, "
                        "(SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS message_count "
                        "FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id ORDER BY t.updated_at DESC, t.id DESC LIMIT 1000")
            return cur.fetchall()
        return self._run(work)

    def add_message(self, ticket_id, author_id, is_staff, body, now, status=None):
        """Add a message and update the ticket in one go. `status` (optional) is the ticket's new status."""
        def work(conn):
            cur = conn.cursor()
            cur.execute("INSERT INTO support_messages (ticket_id, author_id, is_staff, body, created_at) VALUES (%s, %s, %s, %s, %s)",
                        (ticket_id, author_id, 1 if is_staff else 0, body, now))
            # the other side has something new to read; the writer has obviously read it
            who = "user_unread = 1, admin_unread = 0" if is_staff else "user_unread = 0, admin_unread = 1"
            sets, params = [f"updated_at = %s, {who}"], [now]
            if status:
                sets.append("status = %s")
                params.append(status)
            cur.execute(f"UPDATE support_tickets SET {', '.join(sets)} WHERE id = %s", (*params, ticket_id))
            conn.commit()
        return self._run(work)

    def set_status(self, ticket_id, status, now):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE support_tickets SET status = %s, updated_at = %s, user_unread = 1, admin_unread = 0 WHERE id = %s",
                        (status, now, ticket_id))
            conn.commit()
        return self._run(work)

    def mark_seen(self, ticket_id, by_staff, now):
        def work(conn):
            cur = conn.cursor()
            cur.execute(f"UPDATE support_tickets SET {'admin_unread' if by_staff else 'user_unread'} = 0 WHERE id = %s", (ticket_id,))
            conn.commit()
        return self._run(work)

    def add_notice(self, user_id, kind, title, body, link, now):
        def work(conn):
            cur = conn.cursor()
            cur.execute("INSERT INTO user_notifications (user_id, kind, title, body, link, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                        (user_id, kind, title[:160], body[:300], link[:200], now))
            conn.commit()
        return self._run(work)

    def undelivered(self, user_id, since):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, kind, title, body, link, created_at FROM user_notifications "
                        "WHERE user_id = %s AND delivered_at IS NULL AND created_at >= %s ORDER BY id LIMIT 20", (user_id, since))
            return cur.fetchall()
        return self._run(work)

    def mark_delivered(self, user_id, ids, now):
        if not ids:
            return 0

        def work(conn):
            cur = conn.cursor()
            marks = ", ".join(["%s"] * len(ids))
            cur.execute(f"UPDATE user_notifications SET delivered_at = %s WHERE user_id = %s AND delivered_at IS NULL AND id IN ({marks})",
                        (now, user_id, *ids))
            conn.commit()
            return cur.rowcount
        return self._run(work)


store = MySQLSupportStore()


def _one_connection(fn):
    """Run the whole action on one database connection instead of one per step (each new one costs ~half a second)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        unit = getattr(store, "unit", None)
        if unit is None:                                    # the tests' in-memory store
            return fn(*args, **kwargs)
        with unit():
            return fn(*args, **kwargs)
    return wrapper


# ── checking what people type ──

def _clean_line(raw, most, what, least=1):
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(raw or "")).split())
    if len(text) < least:
        raise SupportError(f"Please enter a {what}." if least <= 1 else f"The {what} needs at least {least} characters.")
    if len(text) > most:
        raise SupportError(f"The {what} can be at most {most} characters.")
    return text


def _clean_message(raw):
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch.isprintable()).strip()
    if not text:
        raise SupportError("Please write a message.")
    if len(text) > MAX_MESSAGE:
        raise SupportError(f"The message can be at most {MAX_MESSAGE} characters.")
    return text


# ── what the pages see ──

def _ticket_view(t, for_staff=False):
    unread = t.get("admin_unread") if for_staff else t.get("user_unread")
    out = {"id": t["id"], "category": t["category"], "category_label": CATEGORIES.get(t["category"], t["category"]), "subject": t["subject"],
           "status": t["status"], "status_label": STATUSES.get(t["status"], t["status"]), "created_at": int(t["created_at"]),
           "updated_at": int(t["updated_at"]), "unread": bool(unread)}
    if "message_count" in t:
        out["message_count"] = int(t["message_count"] or 0)
    if for_staff:
        out["user_id"] = t["user_id"]
        out["user_email"] = t.get("user_email") or "(account deleted)"
    return out


def _messages_view(ticket_id):
    return [{"id": m["id"], "from_staff": bool(m["is_staff"]), "body": m["body"], "at": int(m["created_at"])} for m in store.messages(ticket_id)]


def _own_ticket(user_id, ticket_id):
    t = store.get_ticket(ticket_id)
    if not t or t["user_id"] != user_id:                    # someone else's ticket looks exactly like a missing one
        raise SupportError("That ticket wasn't found.", 404)
    return t


def _any_ticket(ticket_id):
    t = store.get_ticket(ticket_id)
    if not t:
        raise SupportError("That ticket wasn't found.", 404)
    return t


# ── for the person who needs help ──

@_one_connection
def create_ticket(user_id, user_email, category, subject, message, base="", now=None):
    now = int(time.time() if now is None else now)
    if category not in CATEGORIES:
        raise SupportError("Please choose a category.")
    subject = _clean_line(subject, MAX_SUBJECT, "subject", MIN_SUBJECT)
    body = _clean_message(message)
    ticket_id = store.create_ticket(user_id, category, subject, body, now)
    ticket = _ticket_view(store.get_ticket(ticket_id))
    _log("ticket_opened", user_id, user_id, f"#{ticket_id} {subject}")
    _email_support(ticket, user_email, body, base, is_reply=False)
    return ticket


@_one_connection
def my_tickets(user_id):
    return [_ticket_view(t) for t in store.tickets_for_user(user_id)]


@_one_connection
def my_ticket(user_id, ticket_id, now=None):
    now = int(time.time() if now is None else now)
    t = _own_ticket(user_id, ticket_id)
    view = _ticket_view(t)
    store.mark_seen(ticket_id, False, now)
    return {**view, "unread": False, "messages": _messages_view(ticket_id)}


@_one_connection
def reply_as_user(user_id, user_email, ticket_id, message, base="", now=None):
    """The person adds to their ticket. A resolved ticket opens again, since the problem evidently isn't over."""
    now = int(time.time() if now is None else now)
    t = _own_ticket(user_id, ticket_id)
    body = _clean_message(message)
    reopen = "open" if t["status"] == "resolved" else None
    store.add_message(ticket_id, user_id, False, body, now, reopen)
    ticket = _ticket_view(store.get_ticket(ticket_id))
    if reopen:
        _log("ticket_status", user_id, user_id, f"#{ticket_id} reopened by a reply")
    _email_support(ticket, user_email, body, base, is_reply=True)
    return {**ticket, "unread": False, "messages": _messages_view(ticket_id)}


# ── for administrators ──

@_one_connection
def all_tickets():
    tickets = [_ticket_view(t, for_staff=True) for t in store.all_tickets()]
    counts = {s: sum(1 for t in tickets if t["status"] == s) for s in STATUSES}
    return {"tickets": tickets, "counts": {**counts, "total": len(tickets)}}


@_one_connection
def open_count():
    """Tickets nobody has picked up yet (shown on the Admin button)."""
    return sum(1 for t in store.all_tickets() if t["status"] == "open")


@_one_connection
def staff_ticket(ticket_id, now=None):
    now = int(time.time() if now is None else now)
    t = _any_ticket(ticket_id)
    store.mark_seen(ticket_id, True, now)
    return {**_ticket_view(t, for_staff=True), "unread": False, "messages": _messages_view(ticket_id)}


@_one_connection
def reply_as_staff(admin_id, ticket_id, message, status=None, base="", now=None):
    """An administrator replies (and may change the status in the same step). The person is told in the bell and by email."""
    now = int(time.time() if now is None else now)
    t = _any_ticket(ticket_id)
    body = _clean_message(message)
    if status is not None and status not in STATUSES:
        raise SupportError("Unknown status.")
    changed = status if status and status != t["status"] else None
    store.add_message(ticket_id, admin_id, True, body, now, changed)
    if changed:
        _log("ticket_status", t["user_id"], admin_id, f"#{ticket_id} {STATUSES[changed]}")
    ticket = _ticket_view(store.get_ticket(ticket_id), for_staff=True)
    _tell_user(t, ticket, body, STATUSES[changed] if changed else "", base, now)
    return {**ticket, "unread": False, "messages": _messages_view(ticket_id)}


@_one_connection
def set_status(admin_id, ticket_id, status, base="", now=None):
    now = int(time.time() if now is None else now)
    if status not in STATUSES:
        raise SupportError("Unknown status.")
    t = _any_ticket(ticket_id)
    if status == t["status"]:
        return {**_ticket_view(t, for_staff=True), "messages": _messages_view(ticket_id)}
    store.set_status(ticket_id, status, now)
    _log("ticket_status", t["user_id"], admin_id, f"#{ticket_id} {STATUSES[status]}")
    ticket = _ticket_view(store.get_ticket(ticket_id), for_staff=True)
    _tell_user(t, ticket, "", STATUSES[status], base, now)
    return {**ticket, "unread": False, "messages": _messages_view(ticket_id)}


# ── the bell ──

@_one_connection
def pending_notices(user_id, now=None):
    now = int(time.time() if now is None else now)
    return [{"id": n["id"], "kind": n["kind"], "title": n["title"], "body": n["body"], "link": n["link"], "at": int(n["created_at"])}
            for n in store.undelivered(user_id, now - NOTICE_DAYS * 86400)]


@_one_connection
def notices_shown(user_id, ids, now=None):
    now = int(time.time() if now is None else now)
    clean = sorted({int(i) for i in ids if isinstance(i, int) and not isinstance(i, bool) and i > 0})[:50]
    return store.mark_delivered(user_id, clean, now)


# ── telling people ──

def _log(kind, user_id, actor_id, detail):
    import auth
    auth.log_event(kind, user_id, actor_id, detail)


def _email_support(ticket, author_email, body, base, is_reply):
    try:
        import mailer
        mailer.new_ticket_alert(ticket, author_email, body, base, is_reply)
    except Exception as e:                                   # noqa: BLE001 - the ticket is saved either way
        log.error("Could not queue the support email for ticket #%s: %s", ticket.get("id"), e)


def _wants_email(user_id):
    import auth
    return auth.wants(user_id, "email_ticket_replies")


def _tell_user(raw, ticket, body, status_label, base, now):
    """Bell notification plus email to the person who raised the ticket. Failures are logged; the reply is saved anyway."""
    title = f"Reply on ticket #{ticket['id']}" if body else f"Ticket #{ticket['id']} is now {status_label.lower()}"
    text = (body[:200] + ("…" if len(body) > 200 else "")) if body else ticket["subject"]
    try:
        store.add_notice(raw["user_id"], "support", title, text, f"/settings#ticket-{ticket['id']}", now)
    except Exception as e:                                   # noqa: BLE001
        log.error("Could not add the bell notification for ticket #%s: %s", ticket["id"], e)
    to = raw.get("user_email")
    if to and _wants_email(raw["user_id"]):
        try:
            import mailer
            mailer.ticket_update(to, ticket, body, status_label, base)
        except Exception as e:                               # noqa: BLE001
            log.error("Could not queue the email for ticket #%s: %s", ticket["id"], e)
