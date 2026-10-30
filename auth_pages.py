"""The sign-in and sign-up pages. Plain server-rendered HTML, so they work before the dashboard's scripts have loaded."""
import html

_CSS = """
:root{--bg:#050508;--card:#0c0c16;--line:#1c1c30;--line2:#2e2e50;--text:#fff;--muted:#8a9ab0;--blue:#4f8ef7;--cyan:#00d4ff;--red:#ff5c5c}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px 16px;background:var(--bg);color:var(--text);
  font:15px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}
.card{width:100%;max-width:400px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px 26px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:22px}
.brand img{width:40px;height:40px;border-radius:10px}
.brand b{display:block;font-size:19px;letter-spacing:-.4px}.brand b span{color:var(--cyan)}
.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:2px;text-transform:uppercase;font-weight:600}
h1{margin:0 0 4px;font-size:20px;letter-spacing:-.3px}
.sub{margin:0 0 20px;color:var(--muted);font-size:13px}
label{display:block;margin:14px 0 6px;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase}
input[type=email],input[type=password],input[type=text]{width:100%;padding:12px;border-radius:9px;border:1px solid var(--line2);background:#10101e;color:var(--text);font-size:16px;font-family:inherit}
input:focus{outline:2px solid var(--blue);outline-offset:1px;border-color:var(--blue)}
.show{display:flex;align-items:center;gap:8px;margin:10px 0 0;color:var(--muted);font-size:13px;font-weight:500;letter-spacing:0;text-transform:none}
.show input{width:16px;height:16px;margin:0}
button{margin-top:20px;width:100%;min-height:46px;border:0;border-radius:9px;background:var(--blue);color:#fff;font-weight:800;font-size:15px;font-family:inherit;cursor:pointer}
button:hover{background:#3d7de0}
.err{margin:0 0 4px;padding:10px 12px;border:1px solid rgba(255,92,92,.35);background:rgba(255,92,92,.08);border-radius:9px;color:var(--red);font-weight:600;font-size:13px}
.hint{margin:6px 0 0;color:var(--muted);font-size:12px}
.alt{margin:20px 0 0;text-align:center;color:var(--muted);font-size:13px}
a{color:var(--cyan);font-weight:700;text-decoration:none}a:hover{text-decoration:underline}
"""

_SHOW_SCRIPT = """<script>
document.getElementById('show').addEventListener('change',function(){
  document.querySelectorAll('input[data-pw]').forEach(function(i){i.type=this.checked?'text':'password'},this);
});
</script>"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#08080e"><meta name="color-scheme" content="dark">
<link rel="icon" type="image/png" href="/icon-192.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">
<title>{html.escape(title)} · Aymetrix</title><style>{_CSS}</style></head><body>
<main class="card"><div class="brand"><img src="/icon-192.png" alt=""><div><b>Aymetrix</b><small>by AYEIM Ventures</small></div></div>
{body}</main></body></html>"""


def _error(message: str) -> str:
    return f'<p class="err" role="alert">{html.escape(message)}</p>' if message else ""


def login_page(error: str = "", email: str = "", signup_open: bool = True) -> str:
    alt = '<p class="alt">New here? <a href="/register">Create an account</a></p>' if signup_open else ""
    return _page("Sign in", f"""<h1>Welcome back</h1><p class="sub">Sign in to see your portfolio, transactions and watchlist.</p>
{_error(error)}
<form method="post" action="/login">
<label for="email">Email</label>
<input id="email" type="email" name="email" value="{html.escape(email, quote=True)}" autocomplete="username" inputmode="email" autocapitalize="none" spellcheck="false" required {'' if email else 'autofocus'}>
<label for="password">Password</label>
<input id="password" type="password" name="password" data-pw autocomplete="current-password" required {'autofocus' if email else ''}>
<label class="show"><input type="checkbox" id="show"> Show password</label>
<button type="submit">Sign in</button></form>{alt}{_SHOW_SCRIPT}""")


def register_page(error: str = "", email: str = "", needs_code: bool = False, closed: bool = False) -> str:
    if closed:
        return _page("Create account", """<h1>Sign-ups are closed</h1><p class="sub">New accounts can't be created right now. Ask the owner of this app for access.</p>
<p class="alt"><a href="/login">Back to sign in</a></p>""")
    code = ('<label for="code">Invite code</label><input id="code" type="text" name="code" autocomplete="off" autocapitalize="none" spellcheck="false" required>'
            if needs_code else "")
    return _page("Create account", f"""<h1>Create your account</h1><p class="sub">Your portfolio, transactions and watchlist stay private to you.</p>
{_error(error)}
<form method="post" action="/register">
<label for="email">Email</label>
<input id="email" type="email" name="email" value="{html.escape(email, quote=True)}" autocomplete="username" inputmode="email" autocapitalize="none" spellcheck="false" required autofocus>
<label for="password">Password</label>
<input id="password" type="password" name="password" data-pw autocomplete="new-password" minlength="8" required>
<p class="hint">At least 8 characters.</p>
<label for="confirm">Confirm password</label>
<input id="confirm" type="password" name="confirm" data-pw autocomplete="new-password" minlength="8" required>
{code}
<label class="show"><input type="checkbox" id="show"> Show passwords</label>
<button type="submit">Create account</button></form>
<p class="alt">Already have an account? <a href="/login">Sign in</a></p>{_SHOW_SCRIPT}""")


def notice_page(title: str, message: str, link: str = "/login", link_text: str = "Back to sign in") -> str:
    """A plain page for something that isn't a form mistake: waiting for approval, no access, accounts not set up yet."""
    return _page(title, f'<h1>{html.escape(title)}</h1><p class="sub">{html.escape(message)}</p><p class="alt"><a href="{html.escape(link, quote=True)}">{html.escape(link_text)}</a></p>')


def pending_page(email: str = "") -> str:
    who = f" for {email}" if email else ""
    return notice_page("Waiting for approval", f"Your account{who} has been created. An administrator needs to approve it before you can sign in. Please check back later.")


def blocked_page() -> str:
    return notice_page("No access", "This account doesn't have access to Aymetrix. If you think that's a mistake, please contact the administrator.")
