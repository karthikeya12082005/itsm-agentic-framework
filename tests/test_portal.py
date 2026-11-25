"""Local Windows portal (item 6.4): sign-in by token, own tickets only, CSRF, escaping, rate limit. Offline: local
desk in a temporary folder, token store in a temporary file, LLM off (tickets are only created, not processed)."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.access.ticket_source import LocalSource
from src.orchestrator import checkpoint as cp
from src.portal import app as P


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tokens = Path(self.tmp.name) / "tokens.json"
        self.src = LocalSource(Path(self.tmp.name) / "t.db").attach_checkpoints(cp.memory_saver())
        self.addCleanup(self.src.store.db.close)
        self.c = TestClient(P.create_app(self.src, self.tokens))

    def sign_in(self, user="employee01", client=None):
        c = client or self.c
        token = P.add_token(user, self.tokens)
        r = c.get(f"/login?t={token}", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertNotIn(token, r.headers["location"])                  # the token leaves the address bar
        return c

    def csrf(self, c):
        html = c.get("/").text
        return html.split('name="csrf" value="')[1].split('"')[0]

    def test_no_session_and_bad_token_are_refused(self):
        self.assertEqual(self.c.get("/").status_code, 401)
        self.assertEqual(self.c.get("/login?t=forged").status_code, 403)

    def test_only_the_hash_is_stored_and_reissue_revokes(self):
        t1 = P.add_token("employee01", self.tokens)
        self.assertNotIn(t1, self.tokens.read_text())
        P.add_token("employee01", self.tokens)
        self.assertEqual(self.c.get(f"/login?t={t1}").status_code, 403)

    def test_create_request_and_see_it(self):
        c = self.sign_in()
        r = c.post("/new", data={"kind": "network", "text": "I cannot connect to the VPN <script>x</script>",
                                 "csrf": self.csrf(c)}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        number = r.headers["location"].split("sent=")[1]
        t = self.src.get(number)
        self.assertEqual((t["requester"], t["category"]), ("employee01", "network"))
        page = c.get(f"/ticket/{number}").text
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>x", page)
        self.assertIn(number, c.get("/").text)

    def test_csrf_and_validation(self):
        c = self.sign_in()
        self.assertEqual(c.post("/new", data={"kind": "access", "text": "need github.com", "csrf": "x"}).status_code,
                         403)
        self.assertEqual(c.post("/new", data={"kind": "root", "text": "need github.com",
                                              "csrf": self.csrf(c)}).status_code, 400)

    def test_other_peoples_tickets_are_invisible(self):
        other = self.src.create("manager01", "need pypi.org")
        c = self.sign_in()
        self.assertEqual(c.get(f"/ticket/{other}").status_code, 404)
        self.assertNotIn(other, c.get("/").text)

    def test_rate_limit(self):
        c = self.sign_in()
        token = self.csrf(c)
        codes = [c.post("/new", data={"kind": "access", "text": f"need github.com {i}", "csrf": token},
                        follow_redirects=False).status_code for i in range(P.MAX_TICKETS_PER_HOUR + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(x == 303 for x in codes[:-1]))


if __name__ == "__main__":
    unittest.main()
