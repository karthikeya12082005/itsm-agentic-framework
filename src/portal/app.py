"""Local Windows portal "IT Help" (TODO 6.4): employees raise tickets and follow the agents' replies from their own
Windows session. Runs on the laptop (127.0.0.1 only) under the project owner's account - the employee never needs
the project, the venv or .env.

    python -m src.portal                          # serve http://127.0.0.1:8810 (ServiceNow; --source local)
    python -m src.portal register employee01      # ADMINISTRATOR terminal: token for that Windows user
    (scripts/portal/install_it_help.ps1 does the registration + the desktop shortcut in one go)

Identity: `register` writes a random token into the Windows user's own profile
(%LOCALAPPDATA%\\ITSM-Portal\\token.txt, ACL: that user + Administrators + SYSTEM) and stores only its SHA-256 in
data/portal/tokens.json. The "IT Help" desktop shortcut runs a launcher AS the employee that reads the token and opens
/login?t=<token>; the portal swaps it for an HttpOnly, SameSite=Strict session cookie and redirects (the token
leaves the address bar). Other local users cannot read the token file. Forms carry a CSRF token; text is escaped;
at most 10 new tickets per user per hour. The Windows user name is the ticket-system user name (employee01).

Pages: /  request form ("What do you need?" access | network | application | software | hardware + free text) and
"My tickets"; /ticket/<number>  status, approval, the conversation (customer-visible comments) - only the
requester's own tickets. Tickets go through the TicketSource interface (caller = the employee, assignment group
AI Service Desk), so the agent worker handles them like any other ticket.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.common.config import PROJECT_ROOT
from src.dashboard.app import CSS, esc, status_html

PORT = 8810
TOKENS = PROJECT_ROOT / "data" / "portal" / "tokens.json"
COOKIE = "itsm_portal"
SESSION_TTL_S = 8 * 3600
MAX_TICKETS_PER_HOUR = 10
MAX_TEXT = 2000
KINDS = [("access", "Access to a website, AI tool or software"),
         ("network", "Problem: VPN or network"),
         ("internal_applications", "Problem: an application or website (e.g. the shop)"),
         ("software", "Problem: software on my laptop"),
         ("hardware", "Problem: my laptop or other hardware")]
USER_RX = re.compile(r"^[A-Za-z0-9._-]{2,40}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_tokens(path: Path = TOKENS) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def add_token(user: str, path: Path = TOKENS) -> str:
    """New token for `user` (old tokens of that user are revoked). Returns the token (to be written to the user's
    profile by `register`); only its hash is stored."""
    if not USER_RX.match(user):
        raise ValueError(f"invalid user name {user!r}")
    token = secrets.token_urlsafe(32)
    d = {h: v for h, v in load_tokens(path).items() if v.get("user") != user}
    d[_hash(token)] = {"user": user, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=1), encoding="utf-8")
    return token


# ===================================================================== app
class Portal:
    def __init__(self, source, tokens_path: Path = TOKENS):
        self.source, self.tokens_path = source, tokens_path
        self.sessions: dict[str, dict] = {}
        self.created: dict[str, list[float]] = {}
        self.lock = threading.RLock()                     # one request at a time touches the ticket source

    def session(self, request: Request) -> dict | None:
        s = self.sessions.get(request.cookies.get(COOKIE, ""))
        if s and time.time() - s["t"] < SESSION_TTL_S:
            return s
        return None

    def login(self, token: str) -> str | None:
        entry = load_tokens(self.tokens_path).get(_hash(token or ""))
        if not entry:
            return None
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {"user": entry["user"], "csrf": secrets.token_urlsafe(16), "t": time.time()}
        return sid

    def my_tickets(self, user: str) -> list[dict]:
        with self.lock:
            return [r for r in self.source.list(100) if r.get("requester") == user][:30]

    def allowed_to_create(self, user: str) -> bool:
        now = time.time()
        recent = [t for t in self.created.get(user, []) if now - t < 3600]
        self.created[user] = recent
        return len(recent) < MAX_TICKETS_PER_HOUR


def page(title: str, body: str, user: str | None = None, refresh: int | None = None) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    who = f'<span class="muted">signed in as <b>{esc(user)}</b> (Windows)</span>' if user else ""
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{meta}<title>{esc(title)} - IT Help</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}'
            "textarea,select{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;"
            "background:var(--surface);color:var(--text)}button{font:inherit;padding:8px 18px;border-radius:6px;"
            "border:1px solid var(--line);background:var(--panel);color:var(--text);cursor:pointer}"
            ".msg{background:var(--panel);border-radius:8px;padding:8px 12px;margin:6px 0}</style></head><body>"
            f'<header><b>IT Help</b><a href="/">My requests</a>{who}</header><main>{body}</main></body></html>')


def create_app(source, tokens_path: Path = TOKENS) -> FastAPI:
    p = Portal(source, tokens_path)
    app = FastAPI(title="IT Help (local portal)", docs_url=None, redoc_url=None)

    def unauth():
        return HTMLResponse(page("Sign in", "<h1>IT Help</h1><p>Open the <b>IT Help</b> shortcut on your desktop to "
                                            "sign in.</p>"), status_code=401)

    @app.get("/login")
    def login(t: str = ""):
        sid = p.login(t)
        if not sid:
            return HTMLResponse(page("Sign in", "<h1>IT Help</h1><p>This sign-in link is not valid. Use the IT Help "
                                                "shortcut on your desktop, or ask IT to reinstall it.</p>"),
                                status_code=403)
        r = RedirectResponse("/", status_code=303)                 # the token leaves the address bar
        r.set_cookie(COOKIE, sid, httponly=True, samesite="strict", max_age=SESSION_TTL_S, path="/")
        return r

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, sent: str = ""):
        s = p.session(request)
        if not s:
            return unauth()
        rows = p.my_tickets(s["user"])
        opts = "".join(f'<option value="{v}">{esc(label)}</option>' for v, label in KINDS)
        body = ["<h1>IT Help</h1>",
                f"<p class='muted'>Ticket {esc(sent)} was created. The AI Service Desk is working on it.</p>" if sent
                else "",
                '<div class="card"><h3>New request</h3><form method="post" action="/new">'
                f'<input type="hidden" name="csrf" value="{esc(s["csrf"])}">'
                f'<p><label>What do you need?<br><select name="kind">{opts}</select></label></p>'
                f'<p><label>Describe it in your own words<br><textarea name="text" rows="4" maxlength="{MAX_TEXT}" '
                'required placeholder="e.g. I need access to github.com to clone our dependencies / I cannot connect '
                'to the VPN from home"></textarea></label></p><button type="submit">Send to the AI Service Desk'
                "</button></form></div>",
                "<h2>My requests</h2>"]
        if rows:
            body.append("<table><tr><th>Number</th><th>Status</th><th>About</th><th>Created</th></tr>" + "".join(
                f"<tr><td><a href='/ticket/{esc(r['number'])}'>{esc(r['number'])}</a></td><td>{status_html(r['state'])}"
                f"</td><td>{esc(r.get('target') or r.get('category') or '')}</td><td class='small'>{esc(r['created_at'])}"
                "</td></tr>" for r in rows) + "</table>")
        else:
            body.append("<p class='muted'>No requests yet.</p>")
        return page("My requests", "".join(body), s["user"], refresh=30)

    @app.post("/new")
    async def new(request: Request):
        # application/x-www-form-urlencoded parsed with the standard library (no python-multipart dependency)
        form = parse_qs((await request.body())[:MAX_TEXT * 4].decode("utf-8", "replace"), keep_blank_values=True)
        kind, text, csrf = (form.get(k, [""])[0] for k in ("kind", "text", "csrf"))
        s = p.session(request)
        if not s:
            return unauth()
        if not secrets.compare_digest(csrf, s["csrf"]):
            return HTMLResponse(page("Error", "<p>The form expired. Go back and send it again.</p>", s["user"]),
                                status_code=403)
        text = " ".join((text or "").split())[:MAX_TEXT]
        if kind not in dict(KINDS) or len(text) < 5:
            return HTMLResponse(page("Error", "<p>Please choose what you need and describe it (at least a few "
                                              "words).</p>", s["user"]), status_code=400)
        with p.lock:
            if not p.allowed_to_create(s["user"]):
                return HTMLResponse(page("Error", f"<p>Too many new requests (max {MAX_TICKETS_PER_HOUR} per hour)."
                                                  "</p>", s["user"]), status_code=429)
            number = source.create(s["user"], text, category=kind)
            p.created.setdefault(s["user"], []).append(time.time())
        return RedirectResponse(f"/?sent={number}", status_code=303)

    @app.get("/ticket/{number}", response_class=HTMLResponse)
    def ticket(request: Request, number: str):
        s = p.session(request)
        if not s:
            return unauth()
        if not re.fullmatch(r"[A-Z]{2,5}\d{5,9}", number):
            return HTMLResponse(page("Not found", "<p>Not found.</p>", s["user"]), status_code=404)
        with p.lock:
            try:
                t = source.get(number)
            except Exception:                                   # noqa: BLE001
                t = None
            if not t or t.get("requester") != s["user"]:          # only your own tickets
                return HTMLResponse(page("Not found", "<p>Not found.</p>", s["user"]), status_code=404)
            msgs = source.messages(number)
            link = source.link(number) if hasattr(source, "link") else ""
        body = [f"<h1>{esc(number)} {status_html(t['state'])}</h1>",
                f"<p><b>Your request:</b> {esc(t['description'])}</p>",
                f"<p class='muted small'>created {esc(t['created_at'])}"
                + (f"; approval: {esc(t['approval_status'])}" if t.get("approval_status") else "") + "</p>",
                "<h2>Replies from the AI Service Desk</h2>"]
        body += [f"<div class='msg'><div class='small muted'>{esc(m['time'])} - {esc(m['author'])}</div>"
                 f"{esc(m['text'])}</div>" for m in msgs] or ["<p class='muted'>No reply yet - the agents usually answer "
                                                              "within a minute. This page refreshes itself.</p>"]
        if link and "service-now.com" in link:
            sp = link.split("/incident.do")[0] + f"/sp?id=ticket&table=incident&sys_id={t.get('sys_id', '')}"
            body.append(f"<p class='small'>Also in the ServiceNow portal (with your ServiceNow login): "
                        f"<a href='{esc(sp)}' target='_blank' rel='noopener'>open</a></p>")
        return page(number, "".join(body), s["user"], refresh=20)

    @app.get("/health")
    def health():
        return {"status": "ok", "source": type(source).__name__}

    return app


# ===================================================================== register (Administrator)
def register(user: str, profile_root: str = r"C:\Users") -> Path:
    """Token file in the Windows user's own profile, readable only by that user, Administrators and SYSTEM."""
    if not USER_RX.match(user):
        raise SystemExit(f"invalid Windows user name {user!r}")
    home = Path(profile_root) / user
    if not home.exists():
        raise SystemExit(f"{home} does not exist - sign in once as {user} first (creates the profile)")
    d = home / "AppData" / "Local" / "ITSM-Portal"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "token.txt"
    f.write_text(add_token(user), encoding="utf-8")
    for target in (d, f):                                      # explicit ACL, no inherited access for others
        r = subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)R" if target == d
                            else f"{user}:R", "*S-1-5-32-544:(OI)(CI)F" if target == d else "*S-1-5-32-544:F",
                            "*S-1-5-18:(OI)(CI)F" if target == d else "*S-1-5-18:F"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"icacls failed on {target}: {r.stdout.strip()} {r.stderr.strip()} "
                             "(run this in an Administrator terminal)")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    rg = sub.add_parser("register", help="(Administrator) create the sign-in token for a Windows user")
    rg.add_argument("user")
    ap.add_argument("--source", choices=["servicenow", "local"], default="servicenow")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    if args.cmd == "register":
        print(f"token written to {register(args.user)} (readable by {args.user}, Administrators, SYSTEM only)")
        return
    import uvicorn

    from src.access.ticket_source import get_source
    from src.orchestrator import checkpoint as cp
    source = get_source(args.source).attach_checkpoints(cp.sqlite_saver())
    users = sorted({v["user"] for v in load_tokens().values()})
    print(f"IT Help portal: http://127.0.0.1:{args.port}  (source {args.source}; registered users: "
          f"{', '.join(users) or 'none - run register in an Administrator terminal'}; Ctrl+C stops)")
    uvicorn.run(create_app(source), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
