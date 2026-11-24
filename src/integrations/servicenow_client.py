"""ServiceNow REST (Table API) client used by the ticket source and the worker (Phase 1).

Authenticates as the integration user (SERVICENOW_USER / SERVICENOW_PASSWORD, Basic auth; the user must hold
the role snc_basic_auth_api_access on PDI dev219979). Agents never use this module directly: they go through
the TicketSource interface (src/access/ticket_source.py, item 1.3).

Features
  * timeouts on every call (connect, read)
  * retries with exponential backoff for 429 / 502 / 503 / 504 and network errors; Retry-After is honoured.
    POST (create) is retried only when the server cannot have created the record (429, connect timeout),
    so a retry never creates a duplicate ticket or approval.
  * pagination with sysparm_offset (query() returns everything up to `limit`)
  * typed errors: ServiceNowAuthError (401), ServiceNowForbidden (403), ServiceNowNotFound (404),
    ServiceNowHibernating (HTML page instead of JSON), ServiceNowError (everything else)
  * helpers: work notes, customer-visible comments, state changes by name, RITM catalog variables,
    journal entries, approvals (sysapproval_approver), users and group members
  * every call is logged (method, table, HTTP status, latency) - never credentials

CLI (live smoke test against the PDI):
    python -m src.integrations.servicenow_client smoke            # read-only checks
    python -m src.integrations.servicenow_client smoke --write    # also create/annotate/cancel one test incident
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sys
import time
from typing import Any, Callable, Iterator

import requests

from src.common import config  # noqa: F401  (loads .env)

log = logging.getLogger("servicenow")

# ---------------------------------------------------------------------------------------------- state values
# Numeric choice values of the out-of-the-box `state` fields (verified live by `smoke` against sys_choice).
RITM_STATES = {
    "pending": "-5",
    "open": "1",
    "work_in_progress": "2",
    "closed_complete": "3",
    "closed_incomplete": "4",
    "closed_skipped": "7",
}
INCIDENT_STATES = {
    "new": "1",
    "in_progress": "2",
    "on_hold": "3",
    "resolved": "6",
    "closed": "7",
    "canceled": "8",
}
STATE_MAPS = {"sc_req_item": RITM_STATES, "incident": INCIDENT_STATES}
# sysapproval_approver.state values (strings, not numbers)
APPROVAL_STATES = ("requested", "approved", "rejected", "cancelled", "not requested", "not_required")

RETRY_STATUSES = {429, 502, 503, 504}


# ---------------------------------------------------------------------------------------------- errors
class ServiceNowError(Exception):
    """Any failed ServiceNow call. `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message if not detail else f"{message} ({detail})")
        self.status = status
        self.detail = detail


class ServiceNowAuthError(ServiceNowError):
    """HTTP 401: wrong user/password, user locked/inactive, or missing snc_basic_auth_api_access."""


class ServiceNowForbidden(ServiceNowError):
    """HTTP 403: authenticated, but an ACL denies the operation (missing role)."""


class ServiceNowNotFound(ServiceNowError):
    """HTTP 404: record (sys_id) or table does not exist / is not visible."""


class ServiceNowHibernating(ServiceNowError):
    """The PDI answered with an HTML page instead of JSON (hibernating or waking up)."""


# ---------------------------------------------------------------------------------------------- helpers
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip("'\"")


_JOURNAL_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.+?) \(([^()\n]+)\)[ \t]*$", re.M)


def parse_journal(text: str) -> list[dict]:
    """Split a journal field display value into entries (same order as given: newest first)."""
    heads = list(_JOURNAL_HEADER.finditer(text or ""))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append({"created_on": m.group(1), "author": m.group(2), "kind": m.group(3),
                    "value": text[m.end():end].strip("\r\n")})
    return out


def _error_text(r: requests.Response) -> tuple[str, str]:
    """(message, detail) from a ServiceNow JSON error body, or a short text excerpt."""
    try:
        err = r.json().get("error") or {}
        return str(err.get("message") or r.reason or "error"), str(err.get("detail") or "")[:300]
    except ValueError:
        return r.reason or "error", r.text[:200]


# ---------------------------------------------------------------------------------------------- client
class ServiceNowClient:
    """Thin, safe wrapper around /api/now/table for one instance and one (integration) user."""

    def __init__(self, instance: str, user: str, password: str, *, timeout: float | tuple = (10, 30),
                 max_retries: int = 3, backoff: float = 1.0, page_size: int = 100,
                 session: Any = None, sleep: Callable[[float], None] = time.sleep):
        instance = instance.rstrip("/")
        if not instance.startswith("https://"):
            raise ValueError("instance must look like 'https://devNNNNNN.service-now.com'")
        if not user or not password:
            raise ValueError("ServiceNow user and password are required")
        self.instance = instance
        self.user = user
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.page_size = page_size
        self._sleep = sleep
        self.session = session or requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    @classmethod
    def from_env(cls, **kwargs) -> "ServiceNowClient":
        """Build from SERVICENOW_INSTANCE / SERVICENOW_USER / SERVICENOW_PASSWORD in .env."""
        instance = _env("SERVICENOW_INSTANCE")
        if "service-now.com" not in instance:
            raise ValueError("SERVICENOW_INSTANCE in .env must look like 'https://devNNNNNN.service-now.com'")
        return cls(instance, _env("SERVICENOW_USER"), _env("SERVICENOW_PASSWORD"), **kwargs)

    def __repr__(self) -> str:  # never shows the password
        return f"ServiceNowClient({self.instance!r}, user={self.user!r})"

    def record_url(self, table: str, sys_id: str) -> str:
        """Link for humans (work notes, CLI output)."""
        return f"{self.instance}/{table}.do?sys_id={sys_id}"

    # ------------------------------------------------------------------------------------ low level
    def request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None,
                ) -> requests.Response:
        """One API call with retries. `path` is relative to the instance, e.g. '/api/now/table/incident'.
        Returns the successful response; raises a ServiceNowError subclass otherwise."""
        url = self.instance + path
        method = method.upper()
        attempt = 0
        while True:
            attempt += 1
            t0 = time.perf_counter()
            try:
                r = self.session.request(method, url, params=params, json=json, timeout=self.timeout)
            except requests.RequestException as e:
                ms = (time.perf_counter() - t0) * 1000
                # a POST is only safe to repeat if the connection was never established
                safe = method != "POST" or isinstance(e, requests.ConnectTimeout)
                log.warning("%s %s -> %s after %.0f ms (attempt %d)", method, path, type(e).__name__, ms, attempt)
                if safe and attempt <= self.max_retries:
                    self._sleep(self._delay(attempt))
                    continue
                raise ServiceNowError(f"{method} {path}: network error {type(e).__name__}", None,
                                      str(e)[:200]) from e

            ms = (time.perf_counter() - t0) * 1000
            log.info("%s %s -> %s in %.0f ms", method, path, r.status_code, ms)

            if r.status_code in RETRY_STATUSES and attempt <= self.max_retries:
                # 429 = request rejected (not processed) -> safe for POST too; 5xx only for non-POST
                if r.status_code == 429 or method != "POST":
                    self._sleep(self._delay(attempt, r.headers.get("Retry-After")))
                    continue
            return self._check(r, method, path)

    def _delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(self.backoff * 2 ** (attempt - 1), 30.0) + random.uniform(0, 0.25 * self.backoff)

    @staticmethod
    def _check(r: requests.Response, method: str, path: str) -> requests.Response:
        what = f"{method} {path}"
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 204:
            return r
        if "json" not in ctype:
            if r.status_code < 400 or r.status_code in (502, 503):
                raise ServiceNowHibernating(f"{what}: non-JSON answer (HTTP {r.status_code}, {ctype or '?'}) - "
                                            "instance is probably hibernating; wake it at developer.servicenow.com",
                                            r.status_code)
            raise ServiceNowError(f"{what}: HTTP {r.status_code}", r.status_code, r.text[:200])
        if r.ok:
            return r
        msg, detail = _error_text(r)
        cls = {401: ServiceNowAuthError, 403: ServiceNowForbidden, 404: ServiceNowNotFound}.get(r.status_code,
                                                                                               ServiceNowError)
        raise cls(f"{what}: HTTP {r.status_code} {msg}", r.status_code, detail)

    @staticmethod
    def _params(fields, display_value) -> dict:
        p = {"sysparm_exclude_reference_link": "true"}
        if fields:
            p["sysparm_fields"] = fields if isinstance(fields, str) else ",".join(fields)
        if display_value:
            p["sysparm_display_value"] = "all" if display_value == "all" else "true"
        return p

    # ------------------------------------------------------------------------------------ Table API
    def get(self, table: str, sys_id: str, fields=None, display_value: bool | str = False) -> dict:
        """One record by sys_id. Raises ServiceNowNotFound if it does not exist."""
        r = self.request("GET", f"/api/now/table/{table}/{sys_id}", params=self._params(fields, display_value))
        return r.json()["result"]

    def iter_query(self, table: str, query: str = "", fields=None, *, limit: int | None = None,
                   order_by: str | None = None, display_value: bool | str = False) -> Iterator[dict]:
        """Yield records matching an encoded query, page by page (sysparm_offset).
        order_by: field name, prefix '-' for descending (e.g. '-sys_created_on'). A stable order is added
        (sys_id) so that pages never overlap.

        ServiceNow applies read ACLs AFTER fetching a page, so a page can hold fewer rows than requested
        while more data follows (seen live: 19 of 20 sys_user_role rows). Therefore the offset advances by
        the requested page size and X-Total-Count (the pre-ACL total) decides when to stop; a short page
        only ends the loop if that header is missing. `limit` counts rows scanned (pre-ACL)."""
        q = query or ""
        if order_by:
            q += ("^" if q else "") + (f"ORDERBYDESC{order_by[1:]}" if order_by.startswith("-")
                                       else f"ORDERBY{order_by}")
        q += ("^" if q else "") + "ORDERBYsys_id"
        offset = 0
        while True:
            size = self.page_size if limit is None else min(self.page_size, limit - offset)
            if size <= 0:
                return
            params = self._params(fields, display_value)
            params.update({"sysparm_query": q, "sysparm_limit": str(size), "sysparm_offset": str(offset)})
            r = self.request("GET", f"/api/now/table/{table}", params=params)
            rows = r.json()["result"]
            yield from rows
            offset += size
            total = r.headers.get("X-Total-Count")
            if total is not None and total.isdigit():
                if offset >= int(total):
                    return
            elif len(rows) < size:
                return

    def query(self, table: str, query: str = "", fields=None, **kwargs) -> list[dict]:
        """All records matching an encoded query (see iter_query for keyword arguments)."""
        return list(self.iter_query(table, query, fields, **kwargs))

    def first(self, table: str, query: str, fields=None, display_value: bool | str = False) -> dict | None:
        rows = self.query(table, query, fields, limit=1, display_value=display_value)
        return rows[0] if rows else None

    def count(self, table: str, query: str = "") -> int:
        """Number of matching records (X-Total-Count of a 1-row query; counted before read ACLs)."""
        r = self.request("GET", f"/api/now/table/{table}", params={
            "sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": "sys_id"})
        return int(r.headers.get("X-Total-Count", len(r.json()["result"])))

    def create(self, table: str, data: dict, fields=None) -> dict:
        r = self.request("POST", f"/api/now/table/{table}", params=self._params(fields, False), json=data)
        return r.json()["result"]

    def update(self, table: str, sys_id: str, data: dict, fields=None) -> dict:
        r = self.request("PATCH", f"/api/now/table/{table}/{sys_id}", params=self._params(fields, False),
                         json=data)
        return r.json()["result"]

    def delete(self, table: str, sys_id: str) -> None:
        self.request("DELETE", f"/api/now/table/{table}/{sys_id}")

    # ------------------------------------------------------------------------------------ task helpers
    def get_by_number(self, table: str, number: str, fields=None, display_value: bool | str = False) -> dict:
        row = self.first(table, f"number={number}", fields, display_value)
        if row is None:
            raise ServiceNowNotFound(f"{table} {number} not found", 404)
        return row

    def add_work_note(self, table: str, sys_id: str, text: str) -> dict:
        """Internal note (visible to fulfillers only)."""
        return self.update(table, sys_id, {"work_notes": text}, fields="sys_id,number")

    def add_comment(self, table: str, sys_id: str, text: str) -> dict:
        """Customer-visible comment (Additional comments)."""
        return self.update(table, sys_id, {"comments": text}, fields="sys_id,number")

    def set_state(self, table: str, sys_id: str, state: str, *, work_note: str | None = None,
                  comment: str | None = None, extra: dict | None = None) -> dict:
        """Set `state` by name (e.g. 'closed_complete', 'work_in_progress') or by raw value ('3').
        Optional work note / comment / extra fields go into the same update."""
        states = STATE_MAPS.get(table, {})
        value = states.get(state, state)
        if states and value not in states.values():
            raise ValueError(f"unknown state {state!r} for {table}; use one of {sorted(states)}")
        data = {"state": value, **(extra or {})}
        if work_note:
            data["work_notes"] = work_note
        if comment:
            data["comments"] = comment
        return self.update(table, sys_id, data, fields="sys_id,number,state")

    def get_journal(self, table: str, sys_id: str, element: str = "work_notes") -> list[dict]:
        """Journal entries of a record, newest first: [{created_on, author, kind, value}].
        element: 'work_notes', 'comments' or 'comments_and_work_notes'.

        sys_journal_field is hidden from ai.agent by read ACLs on dev219979 (3 entries exist, 0 readable), so
        the journal field of the record itself is read as display value and parsed. Its format is
        'YYYY-MM-DD HH:MM:SS - Author Name (Work notes)\\ntext\\n\\n...'."""
        text = self.get(table, sys_id, element, display_value=True).get(element, "") or ""
        return parse_journal(text)

    def get_ritm_variables(self, ritm_sys_id: str) -> dict[str, str]:
        """Catalog variables of a requested item as {variable name: value} (raw values;
        reference variables give the sys_id)."""
        rows = self.query("sc_item_option_mtom", f"request_item={ritm_sys_id}",
                          "sc_item_option.item_option_new.name,sc_item_option.value")
        out = {}
        for row in rows:
            name = row.get("sc_item_option.item_option_new.name")
            if name:
                out[name] = row.get("sc_item_option.value", "")
        return out

    # ------------------------------------------------------------------------------------ approvals
    # Lean design: approvals are given through the agent CLI and recorded ON THE INCIDENT (approval field +
    # work note). sysapproval_approver rows cannot be created for incidents by a non-admin: platform field
    # ACLs only allow approver/sysapproval on new rows for change_request / catalog tasks, and otherwise
    # blank them silently (HTTP 201 with empty fields - seen live on dev219979).
    def set_task_approval(self, table: str, sys_id: str, state: str, work_note: str | None = None) -> dict:
        """Set the task's own `approval` field (requested / approved / rejected / ...) with an optional note."""
        if state not in APPROVAL_STATES:
            raise ValueError(f"approval state must be one of {APPROVAL_STATES}")
        data = {"approval": state}
        if work_note:
            data["work_notes"] = work_note
        return self.update(table, sys_id, data, fields="sys_id,number,approval")

    def get_approvals(self, task_sys_id: str, state: str | None = None) -> list[dict]:
        q = f"sysapproval={task_sys_id}" + (f"^state={state}" if state else "")
        return self.query("sysapproval_approver", q,
                          "sys_id,state,approver,approver.user_name,comments,sys_updated_on", order_by="sys_created_on")

    def create_approval(self, task_sys_id: str, approver_sys_id: str, source_table: str = "sc_req_item") -> dict:
        """Ask `approver` to approve the task (state 'requested'). Works for catalog/change tasks only;
        for incidents use set_task_approval (see the note above)."""
        return self.create("sysapproval_approver", {
            "sysapproval": task_sys_id, "document_id": task_sys_id, "source_table": source_table,
            "approver": approver_sys_id, "state": "requested"}, fields="sys_id,state,approver")

    def set_approval(self, approval_sys_id: str, state: str, comments: str | None = None) -> dict:
        if state not in APPROVAL_STATES:
            raise ValueError(f"approval state must be one of {APPROVAL_STATES}")
        data = {"state": state}
        if comments:
            data["comments"] = comments
        return self.update("sysapproval_approver", approval_sys_id, data, fields="sys_id,state")

    # ------------------------------------------------------------------------------------ users / groups
    def find_user(self, user_name: str, fields="sys_id,user_name,name,email,manager,active") -> dict | None:
        return self.first("sys_user", f"user_name={user_name}", fields)

    def group_members(self, group_name: str) -> list[dict]:
        """Active members of a group as [{sys_id, user_name}]."""
        rows = self.query("sys_user_grmember", f"group.name={group_name}^user.active=true",
                          "user,user.user_name")
        return [{"sys_id": r["user"], "user_name": r.get("user.user_name", "")} for r in rows]


# ---------------------------------------------------------------------------------------------- smoke test
def _smoke(write: bool) -> int:
    sn = ServiceNowClient.from_env()
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            print(f"[OK]   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            ok = False
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")

    print(f"Instance: {sn.instance}  user: {sn.user}")
    me = sn.find_user(sn.user)
    print(f"[{'OK' if me else 'FAIL'}]   find_user({sn.user}): {'found' if me else 'NOT found'}")
    ok &= bool(me)

    # pagination: 4 pages of 20 over sys_user_role must give exactly the rows of one page of 75 (read ACLs
    # hide some roles from ai.agent, so short pages occur and must not end the loop early)
    def paging():
        small = ServiceNowClient.from_env(page_size=20).query("sys_user_role", "", "sys_id", limit=75)
        big = sn.query("sys_user_role", "", "sys_id", limit=75)
        ids = [r["sys_id"] for r in small]
        assert len(ids) == len(set(ids)), "duplicates across pages"
        assert ids == [r["sys_id"] for r in big] and len(ids) > 40, (len(ids), len(big))
        all_rows = len(ServiceNowClient.from_env(page_size=250).query("sys_user_role", "", "sys_id"))
        return (f"75 scanned in pages of 20 = one page of 75 ({len(ids)} readable, no duplicates); "
                f"all pages: {all_rows} readable of {sn.count('sys_user_role')}")
    step("pagination", paging)

    # state numbers used by set_state(): labels seen on existing records (ai.agent can read these) ...
    for table, mapping in STATE_MAPS.items():
        def seen(table=table, mapping=mapping):
            rows = sn.query(table, "", "state", display_value="all", limit=500)
            live = {r["state"]["value"]: r["state"]["display_value"] for r in rows}
            wrong = {v: live[v] for k, v in mapping.items()
                     if v in live and live[v].lower().replace(" ", "_") != k}
            assert not wrong, f"value/label mismatch: {wrong}"
            return ", ".join(f"{v}='{lbl}'" for v, lbl in sorted(live.items()))
        step(f"state labels on {table} records", seen)

    # ... and the full choice lists (sys_choice is admin-only on dev219979 -> admin web session, setup only)
    if _env("SERVICENOW_ADMIN_PASSWORD"):
        from src.integrations.create_ai_agent import Admin
        admin = Admin()
        for table, mapping in STATE_MAPS.items():
            def choices(table=table, mapping=mapping):
                source = table
                for source in (table, "task"):  # sc_req_item has no own choices: it inherits task.state
                    rows = admin.get("sys_choice", f"name={source}^element=state^inactive=false^language=en",
                                     "value,label", limit=50)
                    live = {r["value"]: r["label"] for r in rows}
                    if live:
                        break
                missing = {k: v for k, v in mapping.items() if v not in live}
                assert not missing, f"not in choice list: {missing}; live={live}"
                return f"[from {source}] " + ", ".join(f"{k}={v} '{live[v]}'" for k, v in mapping.items())
            step(f"state choices {table} (admin)", choices)
    else:
        print("[SKIP] full state choice lists: sys_choice needs admin (SERVICENOW_ADMIN_PASSWORD not set)")

    step("count sc_req_item", lambda: sn.count("sc_req_item"))
    step("count incident", lambda: sn.count("incident"))
    step("count sysapproval_approver", lambda: sn.count("sysapproval_approver"))
    step("read sc_item_option_mtom", lambda: f"{len(sn.query('sc_item_option_mtom', '', 'sys_id', limit=3))} rows")

    def not_found():
        try:
            sn.get("incident", "0" * 32)
        except ServiceNowNotFound:
            return "404 -> ServiceNowNotFound"
        raise AssertionError("no error for a missing sys_id")
    step("missing record", not_found)

    def bad_password():
        bad = ServiceNowClient(sn.instance, sn.user, "wrong-password-for-test", max_retries=0)
        try:
            bad.count("sys_user")
        except ServiceNowAuthError:
            return "401 -> ServiceNowAuthError"
        raise AssertionError("wrong password was accepted")
    step("wrong password", bad_password)

    if write:
        inc = sn.create("incident", {
            "short_description": "[smoke test] servicenow_client - safe to ignore",
            "description": "Created by python -m src.integrations.servicenow_client smoke --write",
            "caller_id": me["sys_id"] if me else "", "impact": "3", "urgency": "3"}, fields="sys_id,number,state")
        sid = inc["sys_id"]
        print(f"[OK]   create incident: {inc['number']} state={inc['state']}  {sn.record_url('incident', sid)}")
        step("add_work_note", lambda: sn.add_work_note("incident", sid, "smoke test: work note")["number"])
        step("add_comment", lambda: sn.add_comment("incident", sid, "smoke test: customer-visible comment")["number"])
        step("set_state in_progress", lambda: sn.set_state("incident", sid, "in_progress")["state"])

        def journal(element, expected):
            entries = sn.get_journal("incident", sid, element)
            assert [e["value"] for e in entries] == expected, entries
            return f"{len(entries)} entry by {entries[0]['author']!r} ({entries[0]['kind']}): {expected}"
        step("get_journal work_notes", lambda: journal("work_notes", ["smoke test: work note"]))
        step("get_journal comments", lambda: journal("comments", ["smoke test: customer-visible comment"]))
        step("get_by_number", lambda: sn.get_by_number("incident", inc["number"], "number,state"))
        step("set_state canceled (cleanup)", lambda: sn.set_state(
            "incident", sid, "canceled", work_note="smoke test finished - canceled",
            extra={"close_code": "Duplicate", "close_notes": "smoke test"})["state"])

    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("smoke", help="live checks against the PDI")
    sm.add_argument("--write", action="store_true", help="also create, annotate and cancel one test incident")
    sm.add_argument("-v", "--verbose", action="store_true", help="log every HTTP call (method, path, status, ms)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="  %(name)s: %(message)s")
    sys.exit(_smoke(args.write))


if __name__ == "__main__":
    main()
