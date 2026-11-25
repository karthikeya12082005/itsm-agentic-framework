"""Read-only web dashboard (TODO 5.2): tickets, the agent trace of each ticket, evaluation metrics, LLM health.

    python -m src.dashboard                      # ServiceNow (default), http://127.0.0.1:8800
    python -m src.dashboard --source local       # offline SQLite desk
    python -m src.dashboard --port 8801

Pages: /  overview (system status, tickets) | /ticket/<number>  request, route, agent trace, RCA, policy, critic,
execution, verification, ServiceNow work notes | /metrics  evaluation tables (evaluation/results/*.md) |
/llm  per-agent model chains, per-model latency / failures (logs/llm_calls.jsonl). JSON: /api/tickets,
/api/ticket/<number>, /health.
The dashboard never changes anything: tickets are raised with the CLI or in ServiceNow, approvals are given with
`cli approve` (approver checked against ServiceNow). It binds to 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import html
import re
import threading
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from src.common.config import RESULTS_DIR
from src.orchestrator import checkpoint as cp

METRIC_FILES = [("access_eval_summary.md", "Objective 2 - access requests"),
                ("incident_eval_summary.md", "Objective 3 - incident resolution"),
                ("ablations_summary.md", "Ablations"),
                ("retrieval_eval_category.md", "Retrieval (category filter)"),
                ("retrieval_eval.md", "Retrieval (no filter)")]
AGENTS = ("understanding", "retrieval", "rca", "critic", "communication")
CACHE_S = 20

# state -> (status role, icon, label); icon + label always accompany the colour
STATUS = {"RESOLVED": ("good", "✓", "Resolved"),
          "CLOSED_REJECTED": ("neutral", "■", "Closed / rejected"),
          "AWAITING_APPROVAL": ("warning", "◷", "Awaiting approval"),
          "WAITING_FOR_USER": ("warning", "◷", "Waiting for user"),
          "IN_PROGRESS": ("warning", "◷", "In progress"),
          "NEW": ("neutral", "○", "New"),
          "ESCALATED": ("serious", "▲", "Escalated"),
          "FAILED": ("critical", "✕", "Failed")}

CSS = """
:root{--surface:#fcfcfb;--panel:#f3f2ef;--line:#dedcd6;--text:#0b0b0b;--text2:#52514e;--link:#1f5fbf;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--neutral:#8a8983;--bar:#3a76d6}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--panel:#242422;--line:#3a3936;--text:#f0efec;
--text2:#c3c2b7;--link:#8db6ff;--neutral:#9c9b94;--bar:#6b9cf0}}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--text);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;gap:24px;align-items:baseline;padding:14px 24px;border-bottom:1px solid var(--line)}
header b{font-size:16px}header a{color:var(--text2);text-decoration:none}header a.on{color:var(--text);font-weight:600}
main{padding:18px 24px;max-width:1400px}a{color:var(--link)}h1{font-size:20px;margin:4px 0 12px}
h2{font-size:16px;margin:22px 0 8px}h3{font-size:14px;margin:16px 0 6px}
.muted{color:var(--text2)}.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px}
.tile{background:var(--panel);border-radius:8px;padding:10px 14px;min-width:150px}
.tile .v{font-size:24px;font-weight:600}.tile .l{color:var(--text2);font-size:12px}
table{border-collapse:collapse;width:100%;margin:6px 0 12px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--text2);font-weight:500;font-size:12px}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:baseline}
.st{white-space:nowrap}.good{background:var(--good)}.warning{background:var(--warning)}
.serious{background:var(--serious)}.critical{background:var(--critical)}.neutral{background:var(--neutral)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:var(--panel);border-radius:8px;padding:10px 14px}.card h3{margin-top:2px}
.bar{display:inline-block;height:8px;border-radius:0 4px 4px 0;background:var(--bar);vertical-align:middle}
code,pre{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}pre{white-space:pre-wrap;margin:0}
.small{font-size:12px}.route{font-size:12.5px;color:var(--text2)}table.md{width:auto;min-width:50%}
table.md td.num,table.md th.num{padding-left:28px}
"""


# ===================================================================== helpers
def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def status_html(state: str | None) -> str:
    role, icon, label = STATUS.get(state or "", ("neutral", "○", state or "-"))
    return f'<span class="st"><span class="dot {role}"></span>{icon} {esc(label)}</span>'


def health_html(ok: bool | None, label: str) -> str:
    role, icon = {True: ("good", "✓"), False: ("critical", "✕"), None: ("neutral", "○")}[ok]
    return f'<span class="st"><span class="dot {role}"></span>{icon} {esc(label)}</span>'


def inline_md(s: str) -> str:
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def md_to_html(text: str) -> str:
    """Enough Markdown for the evaluation reports: headings, pipe tables, bullet lists, paragraphs."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]
            num = lambda c: bool(re.fullmatch(r"-?[\d.]+( \(.*\))?|-", c))  # noqa: E731
            # a column is right-aligned as a whole (header included) when most of its cells are numbers
            right = [j > 0 and sum(num(r[j]) for r in body if j < len(r)) > len(body) / 2 for j in range(len(head))]
            cls = lambda j: ' class="num"' if j < len(right) and right[j] else ""  # noqa: E731
            out.append('<table class="md"><tr>' + "".join(f"<th{cls(j)}>{inline_md(c)}</th>" for j, c in enumerate(head))
                       + "</tr>" + "".join("<tr>" + "".join(f"<td{cls(j)}>{inline_md(c)}</td>" for j, c in enumerate(r))
                                           + "</tr>" for r in body) + "</table>")
            continue
        m = re.match(r"(#{1,3}) (.*)", ln)
        if m:
            lvl = len(m.group(1)) + 1          # page title is h1
            out.append(f"<h{lvl}>{inline_md(m.group(2))}</h{lvl}>")
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline_md(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif ln:
            para = [ln]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"[#|-]", lines[i + 1]):
                i += 1
                para.append(lines[i].strip())
            out.append(f"<p>{inline_md(' '.join(para))}</p>")
        i += 1
    return "\n".join(out)


def page(title: str, body: str, active: str = "", refresh: int | None = None) -> str:
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{k}</a>'
                  for k, h in (("Overview", "/"), ("Metrics", "/metrics"), ("LLM models", "/llm")))
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{meta}<title>{esc(title)} - ITSM agents'
            f'</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head>'
            f'<body><header><b>ITSM multi-agent framework</b>{nav}</header><main>{body}</main></body></html>')


def _secs(a, b):
    try:
        return (datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds()
    except (TypeError, ValueError):
        return None


def _dur(s):
    if s is None:
        return "-"
    return f"{s:.0f} s" if s < 120 else f"{s / 60:.1f} min" if s < 7200 else f"{s / 3600:.1f} h"


# ===================================================================== data (also used by the tests)
class Dashboard:
    def __init__(self, source, saver):
        self.source, self.saver = source, saver
        self._cache: dict[str, tuple[float, object]] = {}
        # one request at a time touches the ticket source / checkpoints (SQLite connections, REST session)
        self._lock = threading.RLock()

    def _cached(self, key, fn, fresh=False):
        with self._lock:
            hit = self._cache.get(key)
            if hit and not fresh and time.time() - hit[0] < CACHE_S:
                return hit[1]
            val = fn()
            self._cache[key] = (time.time(), val)
            return val

    def values(self, number) -> dict:
        return cp.values(self.saver, self.source.thread_id(number)) or {}

    def tickets(self, limit=40, fresh=False) -> list[dict]:
        def load():
            rows = []
            for r in self.source.list(limit):
                v = self.values(r["number"])
                inc = v.get("incident") or {}
                rca = inc.get("rca") or {}
                plan = (inc.get("plans") or [{}])[0]
                flow = v.get("flow") or ("access" if r.get("category") == "access" else
                                         "incident" if r.get("category") else None)
                top = (rca.get("ranking") or [[None, None]])[0]
                rows.append({**r, "flow": flow, "has_checkpoint": bool(v),
                             "root_service": rca.get("root_service"), "root_cause": top[0], "posterior": top[1],
                             "action": plan.get("action"), "duration_s": _secs(r.get("created_at"), r.get("resolved_at"))})
            return rows
        return self._cached(f"tickets{limit}", load, fresh)

    def detail(self, number) -> dict:
        with self._lock:
            return self._detail(number)

    def _detail(self, number) -> dict:
        try:
            t = self.source.get(number)
        except Exception as e:                       # noqa: BLE001 - unknown number / not an agent ticket
            raise KeyError(number) from e
        if not t:
            raise KeyError(number)
        return {"ticket": t, "state": self.values(number), "link": self.source.link(number)}

    def system(self, fresh=False) -> dict:
        def load():
            out = {"source": type(self.source).__name__}
            try:
                from src.access import endpoint_client
                s = endpoint_client.status(timeout=3)
                out["endpoint"] = {"ok": True, "allowlist": s.get("allowlist", []), "blocked": len(s.get("blocklist", [])),
                                   "download_restrictions": s.get("download_restrictions")}
            except Exception as e:                   # noqa: BLE001
                out["endpoint"] = {"ok": False, "error": str(e)[:120]}
            try:
                from src.lab import supervisor
                out["lab"] = supervisor.status()
            except Exception as e:                   # noqa: BLE001
                out["lab"] = {"error": str(e)[:120]}
            return out
        return self._cached("system", load, fresh)


def llm_overview(last: int = 500) -> dict:
    from src.common import llm_router
    from src.common.llm_stats import llm_usage, log_lines, log_records, provider_latency
    n = log_lines()
    recs = log_records(max(0, n - last))
    chains = {}
    for a in AGENTS:
        try:
            chains[a] = llm_router.agent_chain(a)
        except Exception as e:                       # noqa: BLE001
            chains[a] = [f"error: {e}"]
    return {"records": len(recs), "first": recs[0]["ts"] if recs else None, "last": recs[-1]["ts"] if recs else None,
            "per_model": provider_latency(recs), "usage": llm_usage(recs), "chains": chains,
            "cooling": {s: round(llm_router.cooling(s)) for c in chains.values() for s in c
                        if ":" in s and llm_router.cooling(s) > 0}}


# ===================================================================== HTML pages
def overview_html(d: Dashboard, fresh=False) -> str:
    rows = d.tickets(fresh=fresh)
    sysinfo = d.system(fresh=fresh)
    count = lambda *states: sum(r["state"] in states for r in rows)  # noqa: E731
    tiles = [("Tickets listed", len(rows)), ("Resolved", count("RESOLVED")),
             ("Closed / rejected", count("CLOSED_REJECTED")),
             ("Waiting (approval / user / job)", count("AWAITING_APPROVAL", "WAITING_FOR_USER", "IN_PROGRESS")),
             ("Escalated to humans", count("ESCALATED", "FAILED"))]
    done = [r["duration_s"] for r in rows if r["state"] == "RESOLVED" and r["duration_s"] is not None]
    if done:
        tiles.append(("Median time to resolve", _dur(sorted(done)[len(done) // 2])))
    body = ["<h1>Overview</h1>", '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="v">{esc(v)}</div><div class="l">{esc(k)}</div></div>' for k, v in tiles)
        + "</div>"]
    ep = sysinfo["endpoint"]
    ep_html = (health_html(True, "reachable") + f'<div class="small muted">allowlist: {esc(", ".join(ep["allowlist"]) or "empty")}'
               f'<br>blocked domains: {ep["blocked"]}; DownloadRestrictions {esc(ep["download_restrictions"])}</div>'
               if ep.get("ok") else health_html(False, "not reachable") + f'<div class="small muted">{esc(ep["error"])}</div>')
    lab = sysinfo["lab"]
    if "error" in lab:
        lab_html = health_html(None, "unknown") + f'<div class="small muted">{esc(lab["error"])}</div>'
    else:
        lab_html = "<table>" + "".join(
            f"<tr><td>{esc(n)}</td><td>{health_html(s['status'] == 'ok', s['status'])}</td>"
            f"<td class='small muted'>{esc(', '.join(s.get('failing') or []))}</td></tr>" for n, s in lab.items()) + "</table>"
    body.append('<div class="grid">'
                f'<div class="card"><h3>Ticket system</h3>{health_html(True, sysinfo["source"])}'
                '<div class="small muted">tickets refresh every 20 s; <a href="/?fresh=1">refresh now</a></div></div>'
                f'<div class="card"><h3>Endpoint enforcement service</h3>{ep_html}</div>'
                f'<div class="card"><h3>Simulated services (lab)</h3>{lab_html}</div></div>')
    body.append("<h2>Tickets</h2><table><tr><th>Number</th><th>Flow</th><th>Requester</th><th>Category</th>"
                "<th>State</th><th>Decision</th><th>Target / root cause</th><th>Action</th><th>Created</th>"
                "<th class='num'>Time to resolve</th></tr>")
    for r in rows:
        what = (esc(r["target"]) if r["flow"] == "access" else
                f"{esc(r['root_cause'])} <span class='muted small'>on {esc(r['root_service'])} "
                f"(p={r['posterior']:.2f})</span>" if r["root_cause"] else "-")
        body.append(f"<tr><td><a href='/ticket/{esc(r['number'])}'>{esc(r['number'])}</a></td><td>{esc(r['flow'] or '-')}"
                    f"</td><td>{esc(r['requester'])}</td><td>{esc(r.get('category') or '-')}</td>"
                    f"<td>{status_html(r['state'])}</td><td>{esc(r['decision'] or '-')}</td><td>{what}</td>"
                    f"<td>{esc(r['action'] or '-')}</td><td class='small st'>{esc(r['created_at'])}</td>"
                    f"<td class='num'>{_dur(r['duration_s'])}</td></tr>")
    body.append("</table><p class='small muted'>Rows without a flow were processed before the Phase-2 checkpoints "
                "existed. Read-only: tickets are raised with <code>python -m src.access.cli new</code> or by the lab "
                "monitor; approvals with <code>cli approve &lt;INC&gt; --by &lt;user&gt;</code>.</p>")
    return page("Overview", "".join(body), "Overview", refresh=30)


def _kv(pairs) -> str:
    return "<table>" + "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in pairs if v not in (None, "")) + "</table>"


def detail_html(d: Dashboard, number: str) -> str:
    info = d.detail(number)
    t, v = info["ticket"], info["state"]
    pol, crit = v.get("policy") or {}, v.get("critic") or {}
    body = [f"<h1>{esc(number)} {status_html(t['state'])}</h1>"]
    if info["link"]:
        body.append(f"<p><a href='{esc(info['link'])}' target='_blank' rel='noopener'>Open in ServiceNow</a></p>")
    body.append(_kv([("Request", f"<pre>{esc(t['description'])}</pre>"), ("Requester",
                     esc(f"{t['requester']} ({t.get('requester_role') or '-'}, manager {t.get('requester_manager') or '-'})")),
                     ("Category", esc(t.get("category"))), ("Created", esc(t["created_at"])),
                     ("Resolved", esc(t.get("resolved_at"))), ("Approval", esc(t.get("approval_status")))]))
    if not v:
        body.append("<p class='muted'>No agent checkpoint for this ticket (processed before Phase 2).</p>")
    else:
        body.append(f"<p class='route'>flow <b>{esc(v.get('flow'))}</b>, route: "
                    + esc(" > ".join(r for r in v.get("route", []) if r != "__end__")) + "</p>")
        body.append('<div class="grid">')
        body.append('<div class="card"><h3>Policy &amp; Risk Agent (rule-based)</h3>' + _kv([
            ("Decision", esc(pol.get("decision"))), ("Policy", esc(pol.get("policy_id"))), ("Risk", esc(pol.get("risk"))),
            ("Approver role", esc(pol.get("approver_role"))), ("Reason", esc(pol.get("reason"))),
            ("Authorization", esc(pol.get("authorization_id")))]) + "</div>")
        if crit:
            body.append('<div class="card"><h3>Critic</h3>' + _kv([
                ("Verdict", esc(crit.get("verdict"))), ("Concerns", esc("; ".join(crit.get("concerns") or []) or "none")),
                ("Model", esc(crit.get("model")))]) + "</div>")
        ex, ver = v.get("execution") or {}, v.get("verification") or {}
        if ex or ver:
            body.append('<div class="card"><h3>Execution &amp; verification</h3>' + _kv([
                ("Action", esc(ex.get("action") or ex.get("kind"))), ("Status", esc(ex.get("status"))),
                ("Action id", f"<code>{esc(ex.get('action_id'))}</code>" if ex.get("action_id") else None),
                ("Attempt", esc(ex.get("attempt"))), ("Verified", esc(ver.get("passed") if ver else v.get("verified"))),
                ("Rolled back", esc(ex.get("rolled_back")))]) + "</div>")
        body.append("</div>")
        inc = v.get("incident") or {}
        rca = inc.get("rca") or {}
        if rca:
            body.append("<h2>Root-cause analysis (ReAct + Bayesian ranking)</h2>")
            tools = [f"{t[0]}({t[1]})" if isinstance(t, (list, tuple)) else str(t) for t in rca.get("tools_used") or []]
            body.append(_kv([("Root service", esc(rca.get("root_service"))),
                             ("ReAct tool calls", esc(" > ".join(tools) or "-")),
                             ("Evidence present", esc(", ".join(k for k, x in (rca.get("features") or {}).items() if x))),
                             ("LLM hypotheses used", esc(rca.get("llm_used")))]))
            if rca.get("hypotheses"):
                body.append("<h3>LLM hypotheses (noisy observation in the Bayesian ranking)</h3><table><tr><th>Root cause"
                            "</th><th>Service</th><th class='num'>LLM confidence</th><th>Reason</th></tr>" + "".join(
                                f"<tr><td>{esc(h.get('root_cause'))}</td><td>{esc(h.get('root_service'))}</td>"
                                f"<td class='num'>{esc(h.get('confidence'))}</td><td class='small'>{esc(h.get('reason'))}"
                                "</td></tr>" for h in rca["hypotheses"]) + "</table>")
            body.append("<table><tr><th>Root cause</th><th class='num'>Posterior</th><th></th></tr>" + "".join(
                f"<tr><td>{esc(rc)}</td><td class='num'>{p:.3f}</td><td style='width:45%'>"
                f"<span class='bar' style='width:{max(1, p * 100):.0f}%'></span></td></tr>"
                for rc, p in (rca.get("ranking") or [])[:5]) + "</table>")
            if inc.get("plans"):
                body.append("<h3>Remediation plans (in order)</h3><table><tr><th>Root cause</th><th>SOP</th>"
                            "<th>Action</th><th>Service</th></tr>" + "".join(
                                f"<tr><td>{esc(p.get('root_cause'))}</td><td>{esc(p.get('sop_id'))}</td>"
                                f"<td>{esc(p.get('action'))}</td><td>{esc(p.get('service'))}</td></tr>"
                                for p in inc["plans"]) + "</table>")
        body.append("<h2>Agent trace</h2><table><tr><th>#</th><th>Agent</th><th>Model</th><th class='num'>Node s</th>"
                    "<th class='num'>LLM s</th><th>Fallback</th><th>Output</th></tr>")
        for i, s in enumerate(v.get("trace", []), 1):
            body.append(f"<tr><td class='num'>{i}</td><td>{esc(s['agent'])}</td><td class='small'>{esc(s.get('model'))}</td>"
                        f"<td class='num'>{s.get('latency_s') or 0:.2f}</td><td class='num'>{s.get('llm_latency_s') or 0:.2f}</td>"
                        f"<td>{esc(s.get('fallback'))}</td><td class='small'>{esc(s['output'])}"
                        + (f"<div class='muted'>in: {esc(s['input'])}</div>" if s.get("input") else "") + "</td></tr>")
        body.append("</table>")
    if t.get("work_notes"):
        body.append("<h2>Work notes in the ticket system</h2><table>" + "".join(
            f"<tr><td class='small'>{esc(n.get('agent'))}</td><td class='small'><pre>{esc(n.get('note'))}</pre></td></tr>"
            for n in t["work_notes"]) + "</table>")
    if t.get("user_message"):
        body.append(f"<h2>Message to the user</h2><p>{esc(t['user_message'])}</p>")
    return page(number, "".join(body))


def metrics_html() -> str:
    parts = ["<h1>Evaluation metrics</h1><p class='muted small'>From evaluation/results (regenerate with "
             "<code>python -m evaluation.eval_access</code>, <code>eval_incident</code>, <code>ablations</code>, "
             "<code>src.knowledge.eval_retrieval</code>).</p>"]
    toc, sections = [], []
    for i, (name, title) in enumerate(METRIC_FILES):
        f = RESULTS_DIR / name
        if not f.exists():
            continue
        toc.append(f"<a href='#m{i}'>{esc(title)}</a>")
        sections.append(f"<section id='m{i}' class='card' style='margin:14px 0'>{md_to_html(f.read_text(encoding='utf-8'))}"
                        "</section>")
    parts.append("<p>" + " · ".join(toc) + "</p>" + "".join(sections))
    return page("Metrics", "".join(parts), "Metrics")


def llm_html() -> str:
    o = llm_overview()
    body = ["<h1>LLM models</h1>", f"<p class='muted small'>Last {o['records']} agent LLM calls in "
            f"logs/llm_calls.jsonl ({esc(o['first'])} to {esc(o['last'])}). The policy decision never uses an LLM; every "
            "LLM call has a rule-based fallback.</p>",
            "<h2>Per-agent model chain (primary, then secondary)</h2><table><tr><th>Agent</th><th>Chain</th>"
            "<th class='num'>Calls</th><th class='num'>Primary</th><th class='num'>Secondary</th>"
            "<th class='num'>Rule-based</th></tr>"]
    for a, chain in o["chains"].items():
        u = o["usage"].get(a, {})
        body.append(f"<tr><td>{esc(a)}</td><td class='small'>{esc(' -> '.join(chain) or 'LLM off')}</td>"
                    f"<td class='num'>{u.get('calls', 0)}</td><td class='num'>{u.get('llm_primary', 0)}</td>"
                    f"<td class='num'>{u.get('llm_secondary', 0)}</td><td class='num'>{u.get('fallback_rules', 0)}</td></tr>")
    body.append("</table>")
    if o["cooling"]:
        body.append("<p class='small'>Cooling down in this dashboard process: " + esc(", ".join(
            f"{s} {v}s" for s, v in o["cooling"].items())) + "</p>")
    body.append("<h2>Per model: every attempt</h2><table><tr><th>Provider:model</th><th class='num'>Attempts</th>"
                "<th class='num'>Called</th><th class='num'>Answered</th><th>Success of called</th>"
                "<th class='num'>Median s</th><th class='num'>p90 s</th><th>Failures</th></tr>")
    for spec, m in o["per_model"].items():
        sr = m["success_rate_of_called"]
        body.append(f"<tr><td>{esc(spec)}</td><td class='num'>{m['attempts']}</td><td class='num'>{m['called']}</td>"
                    f"<td class='num'>{m['answered']}</td><td>"
                    + (f"<span class='bar' style='width:{max(1, sr * 120):.0f}px'></span> {sr:.2f}" if sr is not None else "-")
                    + f"</td><td class='num'>{esc(m['latency_median_s'] or '-')}</td><td class='num'>"
                    f"{esc(m['latency_p90_s'] or '-')}</td><td class='small'>"
                    + esc(", ".join(f"{k} {v}" for k, v in sorted(m["errors"].items())) or "-") + "</td></tr>")
    body.append("</table>")
    return page("LLM models", "".join(body), "LLM models")


# ===================================================================== app
def create_app(source, saver) -> FastAPI:
    d = Dashboard(source, saver)
    app = FastAPI(title="ITSM multi-agent dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def overview(fresh: int = 0):
        return overview_html(d, bool(fresh))

    @app.get("/ticket/{number}", response_class=HTMLResponse)
    def ticket(number: str):
        if not re.fullmatch(r"[A-Z]{2,5}\d{5,9}", number):
            raise HTTPException(404, "not a ticket number")
        try:
            return detail_html(d, number)
        except KeyError:
            raise HTTPException(404, f"{number} not found") from None

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics():
        return metrics_html()

    @app.get("/llm", response_class=HTMLResponse)
    def llm():
        return llm_html()

    @app.get("/api/tickets")
    def api_tickets(fresh: int = 0):
        return d.tickets(fresh=bool(fresh))

    @app.get("/api/ticket/{number}")
    def api_ticket(number: str):
        try:
            info = d.detail(number)
        except KeyError:
            raise HTTPException(404, f"{number} not found") from None
        return {"ticket": info["ticket"], "link": info["link"],
                "agents": {k: info["state"].get(k) for k in ("flow", "route", "understanding", "evidence", "policy",
                                                            "critic", "execution", "verification", "final_state",
                                                            "incident", "trace")}}

    @app.get("/health")
    def health():
        return {"status": "ok", "source": type(source).__name__}

    return app


def main():
    import uvicorn

    from src.access.ticket_source import SOURCES, get_source
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=sorted(SOURCES), default="servicenow")
    ap.add_argument("--port", type=int, default=8800)
    args = ap.parse_args()
    saver = cp.sqlite_saver()
    source = get_source(args.source).attach_checkpoints(saver)
    print(f"Dashboard: http://127.0.0.1:{args.port}  (source {args.source}; read-only; Ctrl+C to stop)")
    uvicorn.run(create_app(source, saver), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
