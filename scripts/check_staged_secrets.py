"""Scan the staged git diff for secrets before a commit.

    python scripts/check_staged_secrets.py        (exit 0 = clean, 1 = something found)

Checks (1) every non-trivial value from .env and (2) key-shaped strings (Groq gsk_, Google AQ./AIza, HF hf_,
postgres URLs with a password, long random tokens next to words like token/password/secret).
Only key NAMES and file names are printed, never values.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = {"DATABASE_URL", "ENDPOINT_TOKEN", "SERVICENOW_PASSWORD", "SERVICENOW_ADMIN_PASSWORD",
               "GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "SMTP_PASSWORD"}
PATTERNS = {
    "groq key": r"gsk_[A-Za-z0-9]{20,}",
    "google key": r"\b(?:AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{30,})",
    "hf token": r"\bhf_[A-Za-z0-9]{30,}",
    "postgres url with password": r"postgres(?:ql)?://[^:\s/]+:[^@\s]{6,}@",
    "assigned secret": r"(?i)(?:password|passwd|secret|token|api_key)\s*[=:]\s*['\"][^'\"\s]{16,}['\"]",
}


def env_values():
    out = {}
    p = ROOT / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line)
        if m and m.group(1) in SECRET_KEYS:
            v = m.group(2).strip().strip("'\"")
            if len(v) >= 8:
                out[m.group(1)] = v
            pw = re.match(r"^[a-z0-9+]+://[^:/\s]+:([^@\s]+)@", v)       # password inside a connection URL
            if pw and len(pw.group(1)) >= 6:
                out[m.group(1) + " (password part)"] = pw.group(1)
    return out


def main():
    diff = subprocess.run(["git", "diff", "--cached", "-U0"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    files = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT, capture_output=True,
                           text=True).stdout.split()
    added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    problems = [f"staged file is a secrets file: {f}" for f in files if Path(f).name in (".env", "token")
                or f.startswith(("data/lab/", "data/checkpoints/", "logs/"))]
    problems += [f".env value of {k} appears in the staged diff" for k, v in env_values().items() if v in added]
    problems += [f"{name} pattern in the staged diff" for name, rx in PATTERNS.items() if re.search(rx, added)]
    print(f"scanned {len(files)} staged files, {len(added.splitlines())} added lines")
    for p in problems:
        print("  FOUND:", p)
    print("RESULT:", "SECRETS FOUND - do not commit" if problems else "clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
