"""
Sends the app's emails (password reset links and Help Desk updates) through a small Google Apps Script that runs in the
ayeim.noreply Gmail account. The app posts each email to the script's web address over HTTPS; the script sends it with
that Gmail account. This works on Render's free plan, which blocks ordinary email (SMTP) connections.

Settings, in .env locally and in Render's Environment tab:
    MAIL_WEBHOOK_URL     the script's web app address (ends in /exec)
    MAIL_WEBHOOK_SECRET  the same secret that is written in the script
    SUPPORT_EMAIL        where new-ticket alerts go (ayeim.noreply@gmail.com)
    APP_URL              the dashboard's address, used in links (Render fills in RENDER_EXTERNAL_URL by itself)

Emails are sent in the background, so nobody waits for Gmail. If the settings are missing, nothing is sent and the server
log says so (and, on your own computer only, prints the email so a reset link can still be tested).
"""
import html
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("mailer")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mailer")
TIMEOUT = 30
BRAND = "Aymetrix"


def configured() -> bool:
    return bool(os.getenv("MAIL_WEBHOOK_URL", "").strip() and os.getenv("MAIL_WEBHOOK_SECRET", "").strip())


def support_inbox() -> str:
    return os.getenv("SUPPORT_EMAIL", "").strip()


def app_url(request_base: str = "") -> str:
    """The dashboard's public address, for links in emails. Never taken from the request's Host header (anyone can send
    a fake one), except on your own computer."""
    for name in ("APP_URL", "RENDER_EXTERNAL_URL"):
        value = os.getenv(name, "").strip().rstrip("/")
        if value.startswith(("https://", "http://")):
            return value
    base = (request_base or "").rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host in ("localhost", "127.0.0.1"):
        return base
    return ""


def _deliver(to: str, subject: str, text: str, html_body: str) -> bool:
    url, secret = os.getenv("MAIL_WEBHOOK_URL", "").strip(), os.getenv("MAIL_WEBHOOK_SECRET", "").strip()
    if not (url and secret):
        log.warning("Email isn't set up (MAIL_WEBHOOK_URL / MAIL_WEBHOOK_SECRET), so this email to %s was not sent: %s", to, subject)
        if not os.getenv("RENDER"):                          # on your own computer: show it, so the flow can be tried
            print(f"\n[email not sent - not set up]\nTo: {to}\nSubject: {subject}\n\n{text}\n")
        return False
    try:
        r = requests.post(url, json={"secret": secret, "to": to, "subject": subject, "text": text, "html": html_body, "name": BRAND},
                          timeout=TIMEOUT)
        ok = False
        try:
            ok = bool(r.json().get("ok"))
        except ValueError:
            pass
        if not ok:
            log.error("The email script refused an email to %s (HTTP %s): %s", to, r.status_code, r.text[:300])
        return ok
    except requests.RequestException as e:
        log.error("Could not reach the email script for an email to %s: %s", to, e)
        return False


def send(to: str, subject: str, text: str, html_body: str):
    """Queue an email. Returns a Future (tests wait on it); callers normally ignore it."""
    if not to:
        return None
    return _pool.submit(_deliver, to, subject, text, html_body)


# ── what the emails look like ──

FOOTER_TEXT = "For help, use Help Desk in Settings on the dashboard. This mailbox isn't monitored for replies."


def _layout(heading: str, paragraphs: list, button: tuple = None, base: str = "", footer: str = "") -> str:
    """A plain, mobile-friendly email in the dashboard's colours. Every piece of text is escaped."""
    paras = "".join(f'<p style="margin:0 0 14px;color:#33415c;font-size:15px;line-height:1.5">{html.escape(p)}</p>' for p in paragraphs)
    btn = ""
    if button:
        label, link = button
        btn = (f'<p style="margin:22px 0"><a href="{html.escape(link, quote=True)}" style="display:inline-block;background:#4f8ef7;color:#ffffff;'
               f'text-decoration:none;font-weight:700;font-size:15px;padding:12px 22px;border-radius:8px">{html.escape(label)}</a></p>'
               f'<p style="margin:0 0 14px;color:#6b7a90;font-size:12px;line-height:1.5">If the button doesn\'t work, copy this link into your browser:<br>'
               f'<span style="word-break:break-all">{html.escape(link)}</span></p>')
    help_link = f' <a href="{html.escape(base + "/settings#help", quote=True)}" style="color:#4f8ef7">Open Help Desk</a>' if base and not footer else ""
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f2f4f8">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f8;padding:24px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:12px;overflow:hidden;font-family:Segoe UI,Helvetica,Arial,sans-serif">
<tr><td style="background:#000E21;padding:18px 24px"><span style="color:#ffffff;font-size:19px;font-weight:800">{BRAND}</span>
<span style="display:block;color:#8a9ab0;font-size:10px;letter-spacing:2px;text-transform:uppercase">by AYEIM Ventures</span></td></tr>
<tr><td style="padding:24px">
<h1 style="margin:0 0 16px;font-size:20px;color:#0b1730">{html.escape(heading)}</h1>{paras}{btn}
</td></tr>
<tr><td style="padding:14px 24px;background:#f7f8fb;color:#6b7a90;font-size:12px;line-height:1.5">{html.escape(footer or FOOTER_TEXT)}{help_link}</td></tr>
</table></td></tr></table></body></html>"""


def _text(heading: str, paragraphs: list, button: tuple = None, footer: str = "") -> str:
    lines = [heading, ""] + [p + "\n" for p in paragraphs]
    if button:
        lines += [f"{button[0]}: {button[1]}", ""]
    return "\n".join(lines + ["--", footer or FOOTER_TEXT])


def password_reset(to: str, link: str, minutes: int, base: str = "", by_admin: bool = False):
    heading = "Reset your Aymetrix password"
    first = ("An administrator sent you this link so you can set a new password." if by_admin
             else "We received a request to reset the password for your Aymetrix account.")
    paras = [first, f"Click the button below to choose a new password. The link works for {minutes} minutes and can be used once.",
             "If you didn't ask for this, you can ignore this email. Your password stays the same."]
    return send(to, heading, _text(heading, paras, ("Reset password", link)), _layout(heading, paras, ("Reset password", link), base))


def new_ticket_alert(ticket: dict, author_email: str, message: str, base: str = "", is_reply: bool = False):
    """To the support inbox: a ticket was raised, or the person added a reply."""
    to = support_inbox()
    if not to:
        log.warning("SUPPORT_EMAIL isn't set, so no email was sent about ticket #%s", ticket.get("id"))
        return None
    what = "New reply on ticket" if is_reply else "New ticket"
    subject = f"{what} #{ticket['id']}: {ticket['subject']}"
    paras = [f"From: {author_email}", f"Category: {ticket.get('category_label') or ticket.get('category')}", f"Subject: {ticket['subject']}", message]
    button = ("Open in Admin Center", f"{base}/admin#ticket-{ticket['id']}") if base else None
    return send(to, subject, _text(subject, paras, button), _layout(subject, paras, button, base))


def ticket_update(to: str, ticket: dict, message: str = "", status_label: str = "", base: str = ""):
    """To the person who raised the ticket: an administrator replied, or the status changed."""
    subject = f"Update on your ticket #{ticket['id']}: {ticket['subject']}"
    paras = []
    if message:
        paras += ["The Aymetrix team replied:", message]
    if status_label:
        paras.append(f"Status: {status_label}")
    button = ("View your ticket", f"{base}/settings#ticket-{ticket['id']}") if base else None
    return send(to, subject, _text(subject, paras, button), _layout(subject, paras, button, base))


def confirm_new_email(to: str, link: str, minutes: int, base: str = ""):
    """To the NEW address: click to confirm it. Nothing changes until they do."""
    heading = "Confirm your new email for Aymetrix"
    paras = ["You asked to use this email address for your Aymetrix account.",
             f"Click the button below to confirm it. The link works for {minutes} minutes. Until you click it, your account keeps using your old email.",
             "If you didn't ask for this, ignore this email and nothing will change."]
    return send(to, heading, _text(heading, paras, ("Confirm new email", link)), _layout(heading, paras, ("Confirm new email", link), base))


def email_changed(old: str, new: str, by_admin: bool = False, base: str = ""):
    """To BOTH addresses once the email has changed. The old address hears about it, in case it wasn't them."""
    heading = "Your Aymetrix email was changed"
    who = "The Aymetrix team changed" if by_admin else "You changed"
    paras = [f"{who} the email for your Aymetrix account from {old} to {new}.",
             f"From now on, sign in with {new}. Your portfolio, transactions and settings are unchanged.",
             f"If you didn't ask for this, email {support_inbox() or 'the Aymetrix team'} straight away."]
    futures = []
    for to in (old, new):
        futures.append(send(to, heading, _text(heading, paras), _layout(heading, paras, None, base)))
    return futures
