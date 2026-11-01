"""Local ticket desk (SQLite). Stands in for ServiceNow until the PDI connector is added:
the agents only use these methods, so swapping in ServiceNow later does not change them."""
import json
import sqlite3
from datetime import datetime, timezone

from src.common.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "tickets.db"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TicketStore:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            number TEXT PRIMARY KEY, requester TEXT NOT NULL, description TEXT NOT NULL,
            state TEXT NOT NULL, decision TEXT, approval_status TEXT, target TEXT, policy_id TEXT, sop_id TEXT,
            created_at TEXT NOT NULL, acknowledged_at TEXT, resolved_at TEXT,
            context TEXT, work_notes TEXT NOT NULL DEFAULT '[]', user_message TEXT)""")
        self.db.commit()

    def _next_number(self):
        row = self.db.execute("SELECT number FROM tickets ORDER BY number DESC LIMIT 1").fetchone()
        n = int(row["number"][4:]) + 1 if row else 9000001        # local range, no clash with dataset IDs
        return f"RITM{n:07d}"

    def create(self, requester, description):
        number = self._next_number()
        self.db.execute("INSERT INTO tickets (number, requester, description, state, created_at) VALUES (?,?,?,?,?)",
                        (number, requester, description, "NEW", now()))
        self.db.commit()
        return number

    def get(self, number):
        row = self.db.execute("SELECT * FROM tickets WHERE number = ?", (number,)).fetchone()
        if not row:
            raise KeyError(number)
        t = dict(row)
        t["context"] = json.loads(t["context"]) if t["context"] else {}
        t["work_notes"] = json.loads(t["work_notes"])
        return t

    def update(self, number, **fields):
        if "context" in fields:
            fields["context"] = json.dumps(fields["context"], default=str)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE tickets SET {cols} WHERE number = ?", (*fields.values(), number))
        self.db.commit()

    def add_note(self, number, agent, text):
        notes = self.get(number)["work_notes"]
        notes.append({"time": now(), "agent": agent, "note": text})
        self.db.execute("UPDATE tickets SET work_notes = ? WHERE number = ?", (json.dumps(notes), number))
        self.db.commit()

    def list(self, limit=20):
        return [dict(r) for r in self.db.execute(
            "SELECT number, requester, state, decision, target, created_at, resolved_at FROM tickets "
            "ORDER BY number DESC LIMIT ?", (limit,))]
