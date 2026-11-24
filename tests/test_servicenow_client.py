"""Unit tests for src/integrations/servicenow_client.py with a fake HTTP session (no network).

    python -m unittest tests.test_servicenow_client -v
"""
import json
import unittest

import requests

from src.integrations.servicenow_client import (
    ServiceNowAuthError, ServiceNowClient, ServiceNowError, ServiceNowFieldDropped, ServiceNowForbidden,
    ServiceNowHibernating, ServiceNowNotFound)

BASE = "https://dev000000.service-now.com"


def resp(status=200, body=None, *, text=None, ctype="application/json;charset=UTF-8", headers=None):
    """A real requests.Response with the given status/body."""
    r = requests.Response()
    r.status_code = status
    r.reason = {200: "OK", 201: "Created", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                429: "Too Many Requests", 503: "Service Unavailable"}.get(status, "")
    r._content = (text if text is not None else json.dumps(body if body is not None else {})).encode()
    r.headers["Content-Type"] = ctype
    r.headers.update(headers or {})
    return r


def ok(result, **kw):
    return resp(200, {"result": result}, **kw)


class FakeSession:
    """Stands in for requests.Session: returns queued responses (or raises queued exceptions)."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []
        self.headers = {}
        self.auth = None

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}), "json": json,
                           "timeout": timeout})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(*responses, **kw):
    sleeps = []
    s = FakeSession(*responses)
    c = ServiceNowClient(BASE, "ai.agent", "secret-pw", session=s, sleep=sleeps.append, **kw)
    return c, s, sleeps


class Construction(unittest.TestCase):
    def test_rejects_bad_instance_and_missing_credentials(self):
        with self.assertRaises(ValueError):
            ServiceNowClient("http://dev1.service-now.com", "u", "p", session=FakeSession())
        with self.assertRaises(ValueError):
            ServiceNowClient(BASE, "u", "", session=FakeSession())

    def test_auth_headers_and_repr_without_password(self):
        c, s, _ = client()
        self.assertEqual(s.auth, ("ai.agent", "secret-pw"))
        self.assertEqual(s.headers["Accept"], "application/json")
        self.assertNotIn("secret-pw", repr(c))
        self.assertEqual(c.record_url("incident", "abc"), f"{BASE}/incident.do?sys_id=abc")


class TableApi(unittest.TestCase):
    def test_get_by_sys_id(self):
        c, s, _ = client(ok({"sys_id": "a1", "number": "INC1"}))
        self.assertEqual(c.get("incident", "a1", ["sys_id", "number"])["number"], "INC1")
        call = s.calls[0]
        self.assertEqual((call["method"], call["url"]), ("GET", f"{BASE}/api/now/table/incident/a1"))
        self.assertEqual(call["params"]["sysparm_fields"], "sys_id,number")
        self.assertEqual(call["params"]["sysparm_exclude_reference_link"], "true")
        self.assertIsNotNone(call["timeout"])

    def test_pagination_reads_all_pages(self):
        c, s, _ = client(ok([{"n": 1}, {"n": 2}]), ok([{"n": 3}, {"n": 4}]), ok([{"n": 5}]), page_size=2)
        rows = c.query("sys_user", "active=true", "n")
        self.assertEqual([r["n"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual([x["params"]["sysparm_offset"] for x in s.calls], ["0", "2", "4"])
        self.assertEqual(s.calls[0]["params"]["sysparm_query"], "active=true^ORDERBYsys_id")

    def test_pagination_exact_multiple_stops_on_empty_page(self):
        c, s, _ = client(ok([{"n": 1}, {"n": 2}]), ok([]), page_size=2)
        self.assertEqual(len(c.query("sys_user")), 2)
        self.assertEqual(len(s.calls), 2)

    def test_acl_filtered_short_pages_do_not_stop_paging(self):
        # live behaviour: ACLs remove rows after the page is fetched -> 1 of 2 rows returned, more data follows
        total = {"X-Total-Count": "5"}
        c, s, _ = client(ok([{"n": 1}], headers=total), ok([{"n": 3}, {"n": 4}], headers=total),
                         ok([], headers=total), page_size=2)
        self.assertEqual([r["n"] for r in c.query("sys_user_role")], [1, 3, 4])
        self.assertEqual([x["params"]["sysparm_offset"] for x in s.calls], ["0", "2", "4"])

    def test_limit_shrinks_last_page(self):
        c, s, _ = client(ok([{"n": 1}, {"n": 2}]), ok([{"n": 3}]), page_size=2)
        self.assertEqual(len(c.query("sys_user", limit=3)), 3)
        self.assertEqual([x["params"]["sysparm_limit"] for x in s.calls], ["2", "1"])

    def test_order_by_descending(self):
        c, s, _ = client(ok([]))
        c.query("sys_journal_field", "element=work_notes", order_by="-sys_created_on")
        self.assertEqual(s.calls[0]["params"]["sysparm_query"],
                         "element=work_notes^ORDERBYDESCsys_created_on^ORDERBYsys_id")

    def test_count_uses_total_header(self):
        c, _, _ = client(ok([{"sys_id": "x"}], headers={"X-Total-Count": "42"}))
        self.assertEqual(c.count("incident"), 42)

    def test_create_update_delete(self):
        c, s, _ = client(resp(201, {"result": {"sys_id": "n1"}}), ok({"sys_id": "n1"}), resp(204, text="", ctype=""))
        self.assertEqual(c.create("incident", {"short_description": "x"})["sys_id"], "n1")
        c.update("incident", "n1", {"urgency": "2"})
        c.delete("incident", "n1")
        self.assertEqual([x["method"] for x in s.calls], ["POST", "PATCH", "DELETE"])
        self.assertEqual(s.calls[1]["url"], f"{BASE}/api/now/table/incident/n1")
        self.assertEqual(s.calls[1]["json"], {"urgency": "2"})

    def test_silently_dropped_field_raises(self):
        # live behaviour: HTTP 200 but close_code comes back blank when the value is not in the choice list
        c, s, _ = client(ok({"sys_id": "i1", "state": "6", "close_code": "", "close_notes": "x"}))
        with self.assertRaises(ServiceNowFieldDropped) as cm:
            c.update("incident", "i1", {"state": "6", "close_code": "Solution provided", "close_notes": "x",
                                        "work_notes": "journal fields always read back empty"}, fields="sys_id")
        self.assertIn("close_code", str(cm.exception))
        self.assertNotIn("work_notes", str(cm.exception))
        # the fields we write are requested back so the guard can see them (journal fields excluded)
        self.assertEqual(s.calls[0]["params"]["sysparm_fields"], "sys_id,state,close_code,close_notes")

    def test_dropped_reference_on_create_raises(self):
        c, _, _ = client(resp(201, {"result": {"sys_id": "a1", "approver": "", "sysapproval": "", "state": "requested"}}))
        with self.assertRaises(ServiceNowFieldDropped):
            c.create_approval("inc1", "u1", source_table="incident")

    def test_get_by_number_not_found(self):
        c, _, _ = client(ok([]))
        with self.assertRaises(ServiceNowNotFound):
            c.get_by_number("sc_req_item", "RITM0000001")


class Errors(unittest.TestCase):
    def test_401_is_auth_error_without_retry(self):
        c, s, sleeps = client(resp(401, {"error": {"message": "User Not Authenticated", "detail": "Required"}}))
        with self.assertRaises(ServiceNowAuthError) as cm:
            c.count("sys_user")
        self.assertEqual(cm.exception.status, 401)
        self.assertIn("User Not Authenticated", str(cm.exception))
        self.assertEqual((len(s.calls), sleeps), (1, []))

    def test_403_and_404(self):
        c, _, _ = client(resp(403, {"error": {"message": "ACL"}}), resp(404, {"error": {"message": "No Record"}}))
        with self.assertRaises(ServiceNowForbidden):
            c.update("incident", "x", {"state": "2"})
        with self.assertRaises(ServiceNowNotFound):
            c.get("incident", "x")

    def test_html_answer_means_hibernating(self):
        c, _, _ = client(resp(200, text="<html>Your instance is hibernating</html>", ctype="text/html"))
        with self.assertRaises(ServiceNowHibernating):
            c.query("incident")

    def test_other_status_is_generic_error(self):
        c, _, _ = client(resp(400, {"error": {"message": "Invalid query"}}))
        with self.assertRaises(ServiceNowError) as cm:
            c.query("incident", "bad")
        self.assertEqual(cm.exception.status, 400)


class Retries(unittest.TestCase):
    def test_get_retries_503_then_succeeds(self):
        c, s, sleeps = client(resp(503, {}), ok({"sys_id": "a"}))
        self.assertEqual(c.get("incident", "a")["sys_id"], "a")
        self.assertEqual((len(s.calls), len(sleeps)), (2, 1))

    def test_retry_after_is_honoured(self):
        c, _, sleeps = client(resp(429, {}, headers={"Retry-After": "7"}), ok([]))
        c.query("incident")
        self.assertEqual(sleeps, [7.0])

    def test_gives_up_after_max_retries(self):
        c, s, sleeps = client(*[resp(503, {}) for _ in range(3)], max_retries=2)
        with self.assertRaises(ServiceNowError) as cm:
            c.get("incident", "a")
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual((len(s.calls), len(sleeps)), (3, 2))

    def test_backoff_grows(self):
        c, _, sleeps = client(resp(502, {}), resp(502, {}), resp(502, {}), ok([]), backoff=1.0)
        c.query("incident")
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(1.0 <= sleeps[0] < 1.3 and 2.0 <= sleeps[1] < 2.3 and 4.0 <= sleeps[2] < 4.3, sleeps)

    def test_network_error_retried_for_get(self):
        c, s, _ = client(requests.ConnectionError("reset"), requests.ReadTimeout("slow"), ok([{"n": 1}]))
        self.assertEqual(len(c.query("incident")), 1)
        self.assertEqual(len(s.calls), 3)

    def test_post_not_retried_on_503_or_read_timeout(self):
        c, s, _ = client(resp(503, {}))
        with self.assertRaises(ServiceNowError):
            c.create("incident", {"short_description": "x"})
        self.assertEqual(len(s.calls), 1)
        c, s, _ = client(requests.ReadTimeout("server may have created it"))
        with self.assertRaises(ServiceNowError) as cm:
            c.create("incident", {"short_description": "x"})
        self.assertIsNone(cm.exception.status)
        self.assertEqual(len(s.calls), 1)

    def test_post_retried_on_429_and_connect_timeout(self):
        c, s, _ = client(resp(429, {}), requests.ConnectTimeout("no connection"),
                         resp(201, {"result": {"sys_id": "n1"}}))
        self.assertEqual(c.create("incident", {"short_description": "x"})["sys_id"], "n1")
        self.assertEqual(len(s.calls), 3)


class TaskHelpers(unittest.TestCase):
    def test_work_note_and_comment(self):
        c, s, _ = client(ok({"sys_id": "r1"}), ok({"sys_id": "r1"}))
        c.add_work_note("sc_req_item", "r1", "agent step")
        c.add_comment("sc_req_item", "r1", "hello user")
        self.assertEqual(s.calls[0]["json"], {"work_notes": "agent step"})
        self.assertEqual(s.calls[1]["json"], {"comments": "hello user"})
        self.assertEqual(s.calls[0]["method"], "PATCH")

    def test_set_state_by_name_with_notes(self):
        c, s, _ = client(ok({"state": "3"}))
        c.set_state("sc_req_item", "r1", "closed_complete", work_note="done", comment="access granted")
        self.assertEqual(s.calls[0]["json"], {"state": "3", "work_notes": "done", "comments": "access granted"})

    def test_set_state_raw_value_and_incident_map(self):
        c, s, _ = client(ok({}), ok({}))
        c.set_state("sc_req_item", "r1", "4")
        c.set_state("incident", "i1", "resolved", extra={"close_code": "Solved", "close_notes": "x"})
        self.assertEqual(s.calls[0]["json"], {"state": "4"})
        self.assertEqual(s.calls[1]["json"], {"state": "6", "close_code": "Solved", "close_notes": "x"})

    def test_set_state_unknown_name_sends_nothing(self):
        c, s, _ = client()
        with self.assertRaises(ValueError):
            c.set_state("sc_req_item", "r1", "resolved")  # 'resolved' is an incident state, not a RITM state
        self.assertEqual(s.calls, [])

    def test_ritm_variables(self):
        c, s, _ = client(ok([
            {"sc_item_option.item_option_new.name": "target", "sc_item_option.value": "github.com"},
            {"sc_item_option.item_option_new.name": "justification", "sc_item_option.value": "code review"},
            {"sc_item_option.item_option_new.name": "", "sc_item_option.value": "ignored"}]))
        self.assertEqual(c.get_ritm_variables("r1"), {"target": "github.com", "justification": "code review"})
        self.assertTrue(s.calls[0]["params"]["sysparm_query"].startswith("request_item=r1"))

    def test_journal_parsed_from_record_display_value(self):
        # exact format seen live on dev219979 (sys_journal_field itself is ACL-hidden from ai.agent)
        text = ("2026-11-24 08:50:01 - AI Agent (Work notes)\n[policy] DENY POL-0030\n\nalternative: Gemini\n\n"
                "2026-11-24 08:49:58 - AI Agent (Work notes)\nsmoke test: work note\n\n")
        c, s, _ = client(ok({"work_notes": text}))
        entries = c.get_journal("sc_req_item", "r1")
        self.assertEqual([e["value"] for e in entries],
                         ["[policy] DENY POL-0030\n\nalternative: Gemini", "smoke test: work note"])
        self.assertEqual((entries[0]["author"], entries[0]["kind"], entries[0]["created_on"]),
                         ("AI Agent", "Work notes", "2026-11-24 08:50:01"))
        self.assertEqual(s.calls[0]["url"], f"{BASE}/api/now/table/sc_req_item/r1")
        self.assertEqual(s.calls[0]["params"]["sysparm_display_value"], "true")

    def test_journal_empty(self):
        c, _, _ = client(ok({"comments": ""}))
        self.assertEqual(c.get_journal("incident", "i1", "comments"), [])


class Approvals(unittest.TestCase):
    def test_create_approval(self):
        c, s, _ = client(resp(201, {"result": {"sys_id": "ap1", "state": "requested"}}))
        c.create_approval("r1", "u9")
        body = s.calls[0]["json"]
        self.assertEqual((body["sysapproval"], body["approver"], body["state"], body["source_table"]),
                         ("r1", "u9", "requested", "sc_req_item"))

    def test_get_approvals_filter_by_state(self):
        c, s, _ = client(ok([{"sys_id": "ap1", "state": "approved"}]))
        c.get_approvals("r1", "approved")
        self.assertTrue(s.calls[0]["params"]["sysparm_query"].startswith("sysapproval=r1^state=approved"))

    def test_set_approval_validates_state(self):
        c, s, _ = client(ok({"state": "rejected"}))
        with self.assertRaises(ValueError):
            c.set_approval("ap1", "maybe")
        c.set_approval("ap1", "rejected", "not allowed")
        self.assertEqual(s.calls[0]["json"], {"state": "rejected", "comments": "not allowed"})

    def test_set_task_approval_on_incident(self):
        c, s, _ = client(ok({"approval": "approved"}))
        with self.assertRaises(ValueError):
            c.set_task_approval("incident", "i1", "ok")
        c.set_task_approval("incident", "i1", "approved", "approved by security01 (SECURITY_TEAM)")
        self.assertEqual((s.calls[0]["method"], s.calls[0]["url"]), ("PATCH", f"{BASE}/api/now/table/incident/i1"))
        self.assertEqual(s.calls[0]["json"], {"approval": "approved",
                                              "work_notes": "approved by security01 (SECURITY_TEAM)"})

    def test_group_members(self):
        c, s, _ = client(ok([{"user": "u1", "user.user_name": "sec01"}]))
        self.assertEqual(c.group_members("IT Security"), [{"sys_id": "u1", "user_name": "sec01"}])
        self.assertIn("group.name=IT Security^user.active=true", s.calls[0]["params"]["sysparm_query"])


if __name__ == "__main__":
    unittest.main()
