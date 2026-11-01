"""Manual test of the endpoint service: allow gemini.google.com, verify, then roll back.
Run in a NORMAL terminal (venv active) while the service runs in the Administrator terminal."""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = "http://127.0.0.1:8765"
H = {"X-Endpoint-Token": os.environ["ENDPOINT_TOKEN"]}

def show(title, r):
    print(f"\n== {title} [{r.status_code}]")
    print(r.json())
    return r.json()

show("health", requests.get(f"{BASE}/health"))
show("status (before)", requests.get(f"{BASE}/status", headers=H))
step = sys.argv[1] if len(sys.argv) > 1 else "allow"

if step == "allow":
    rec = show("allowlist gemini.google.com", requests.post(f"{BASE}/actions/allowlist_domain", headers=H, json={
        "domain": "gemini.google.com", "ticket_id": "RITM0000001", "authorization_id": "TEST-AUTH-1"}))
    show("verify", requests.get(f"{BASE}/verify/domain/gemini.google.com", headers=H))
    print("\nNOW: sign in as employee01 -> Chrome -> gemini.google.com should OPEN.")
    print(f"Then roll back with:  python endpoint_service\\test_endpoint.py rollback {rec['action_id']}")
elif step == "rollback":
    show("rollback", requests.post(f"{BASE}/actions/rollback", headers=H, json={
        "action_id": sys.argv[2], "ticket_id": "RITM0000001"}))
    show("verify", requests.get(f"{BASE}/verify/domain/gemini.google.com", headers=H))
    print("\nNOW: as employee01, gemini.google.com should be BLOCKED again.")
elif step == "negative":
    show("unmanaged domain must be refused (403)", requests.post(f"{BASE}/actions/allowlist_domain", headers=H, json={
        "domain": "example.org", "ticket_id": "RITM0000002", "authorization_id": "TEST"}))
    show("wrong token must be refused (401)", requests.get(f"{BASE}/status", headers={"X-Endpoint-Token": "wrong"}))
