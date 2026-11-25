"""Demo catalogue: maps REAL request targets on the employee laptop to the dataset's policies.
The dataset uses fictional names (ChatNova, Orbit Assist, *.example); this table is the organisation's
decision about which real tools fall under which policy. Changing it changes policy, not code."""

# kind: "domain" -> endpoint action ALLOWLIST_DOMAIN, "software" -> INSTALL_SOFTWARE
TARGETS = {
    # ---- websites / AI tools ----
    "chatgpt.com":       {"kind": "domain", "policy_id": "POL-0030", "label": "ChatGPT (external AI tool)"},
    "chat.openai.com":   {"kind": "domain", "policy_id": "POL-0030", "label": "ChatGPT (external AI tool)"},
    "openai.com":        {"kind": "domain", "policy_id": "POL-0030", "label": "OpenAI website"},
    "claude.ai":         {"kind": "domain", "policy_id": "POL-0030", "label": "Claude (external AI tool)"},
    "perplexity.ai":     {"kind": "domain", "policy_id": "POL-0030", "label": "Perplexity (external AI tool)"},
    "gemini.google.com": {"kind": "domain", "policy_id": "POL-0028", "label": "Gemini (approved AI assistant)"},
    "github.com":        {"kind": "domain", "policy_id": "POL-0025", "label": "GitHub"},
    "pypi.org":          {"kind": "domain", "policy_id": "POL-0025", "label": "Python Package Index"},
    "wetransfer.com":    {"kind": "domain", "policy_id": "POL-0027", "label": "WeTransfer (file sharing)"},
    "mega.nz":           {"kind": "domain", "policy_id": "POL-0027", "label": "MEGA (file sharing)"},
    # ---- added in 3.8 (level 1, data only) ----
    "dropbox.com":       {"kind": "domain", "policy_id": "POL-0027", "label": "Dropbox (personal file sharing)"},
    "deepseek.com":      {"kind": "domain", "policy_id": "POL-0030", "label": "DeepSeek (external AI tool)"},
    "chat.deepseek.com": {"kind": "domain", "policy_id": "POL-0030", "label": "DeepSeek Chat (external AI tool)"},
    "stackoverflow.com": {"kind": "domain", "policy_id": "POL-0026", "label": "Stack Overflow"},
    "docs.python.org":   {"kind": "domain", "policy_id": "POL-0026", "label": "Python documentation"},
    "huggingface.co":    {"kind": "domain", "policy_id": "POL-0029", "label": "Hugging Face (AI development platform)"},
    "kaggle.com":        {"kind": "domain", "policy_id": "POL-0029", "label": "Kaggle (AI development platform)"},
    "colab.research.google.com": {"kind": "domain", "policy_id": "POL-0029",
                                  "label": "Google Colab (AI development platform)"},
    # ---- software (winget IDs) ----
    "Google.Chrome":              {"kind": "software", "policy_id": "POL-0017", "label": "Google Chrome"},
    "Microsoft.VisualStudioCode": {"kind": "software", "policy_id": "POL-0018", "label": "Visual Studio Code"},
    "Git.Git":                    {"kind": "software", "policy_id": "POL-0019", "label": "Git"},
    "Python.Python.3.12":         {"kind": "software", "policy_id": "POL-0019", "label": "Python 3.12"},
    "Postman.Postman":            {"kind": "software", "policy_id": "POL-0019", "label": "Postman"},
    # ---- added in 3.8: enterprise apps (POL-0023 ALLOW) and prohibited software (POL-0020 DENY) ----
    "Zoom.Zoom":                  {"kind": "software", "policy_id": "POL-0023", "label": "Zoom Workplace"},
    "SlackTechnologies.Slack":    {"kind": "software", "policy_id": "POL-0023", "label": "Slack"},
    "Microsoft.Teams":            {"kind": "software", "policy_id": "POL-0023", "label": "Microsoft Teams"},
    "Notepad++.Notepad++":        {"kind": "software", "policy_id": "POL-0023", "label": "Notepad++"},
    "7zip.7zip":                  {"kind": "software", "policy_id": "POL-0023", "label": "7-Zip"},
    "qBittorrent.qBittorrent":    {"kind": "software", "policy_id": "POL-0020", "label": "qBittorrent (torrent client)"},
    "uTorrent":                   {"kind": "software", "policy_id": "POL-0020", "label": "uTorrent (torrent client)"},
}

# Context for the Critic Agent (found live, Nov 24 2026: without it the LLM critic escalated Gemini because
# POL-0028's SOP action is ADD_GROUP_MEMBERSHIP for the fictional "Orbit Assist" licence group).
CATALOGUE_NOTE = (
    "Policy and SOP texts come from a synthetic dataset of the fictional company 'Orbitel Systems' and use "
    "fictional product names (e.g. 'Orbit Assist' = the approved internal AI assistant, 'ChatNova' = an "
    "unapproved external AI tool). The organisation's catalogue maps each real target to one of these policies; "
    "that mapping is authoritative. On this laptop every website target is enforced with ALLOWLIST_DOMAIN (Chrome "
    "URLAllowlist) and every software target with INSTALL_SOFTWARE (winget); the SOP's action type (e.g. "
    "ADD_GROUP_MEMBERSHIP for a licence group, GRANT_ACCESS) is the corporate equivalent of that action.")
ENFORCEMENT = {"domain": "ALLOWLIST_DOMAIN", "software": "INSTALL_SOFTWARE"}

# Approved alternative offered when a request is denied (policy_id -> text)
ALTERNATIVES = {
    "POL-0030": "Use the approved AI assistant Gemini (gemini.google.com); you can request it through a ticket.",
    "POL-0027": "Use the company-approved file-sharing service instead of personal file-sharing sites.",
    "POL-0020": "Choose an approved application from the software catalogue (e.g. 7-Zip, Notepad++, Zoom).",
}

# Keywords for recognising software in free text -> winget ID
SOFTWARE_KEYWORDS = {
    "google chrome": "Google.Chrome", "chrome": "Google.Chrome",
    "visual studio code": "Microsoft.VisualStudioCode", "vs code": "Microsoft.VisualStudioCode",
    "vscode": "Microsoft.VisualStudioCode", "git": "Git.Git", "python": "Python.Python.3.12",
    "postman": "Postman.Postman",
    "zoom": "Zoom.Zoom", "zoom workplace": "Zoom.Zoom", "slack": "SlackTechnologies.Slack",
    "microsoft teams": "Microsoft.Teams", "ms teams": "Microsoft.Teams",   # not bare "teams" ("our teams")
    "notepad++": "Notepad++.Notepad++", "notepad plus plus": "Notepad++.Notepad++",
    "7-zip": "7zip.7zip", "7zip": "7zip.7zip", "7 zip": "7zip.7zip",
    "qbittorrent": "qBittorrent.qBittorrent", "utorrent": "uTorrent", "µtorrent": "uTorrent",
}

# Names people use for managed websites / AI tools (no domain written) -> catalogue key.
# Matched as whole words, case-insensitive. Fixes "I need ChatGPT to debug my Python code" (TODO 2.4).
TARGET_ALIASES = {
    "chatgpt": "chatgpt.com", "chat gpt": "chatgpt.com", "openai": "openai.com", "open ai": "openai.com",
    "claude": "claude.ai", "perplexity": "perplexity.ai",
    "gemini": "gemini.google.com", "google gemini": "gemini.google.com",
    "github": "github.com", "git hub": "github.com", "pypi": "pypi.org", "python package index": "pypi.org",
    "wetransfer": "wetransfer.com", "we transfer": "wetransfer.com", "mega.nz": "mega.nz",
    "dropbox": "dropbox.com", "deepseek": "chat.deepseek.com", "stack overflow": "stackoverflow.com",
    "stackoverflow": "stackoverflow.com", "python docs": "docs.python.org",
    "python documentation": "docs.python.org", "hugging face": "huggingface.co", "huggingface": "huggingface.co",
    "kaggle": "kaggle.com", "google colab": "colab.research.google.com", "colab": "colab.research.google.com",
}

# Employee directory for the LOCAL ticket source only (tests/offline). With ServiceNow (default) the role is the
# caller's sys_user.title and the manager is sys_user.manager - created by src/integrations/servicenow_setup.py.
EMPLOYEES = {
    "employee01": {"name": "Employee 01", "role": "DEVELOPER", "department": "ENGINEERING", "manager": "manager01"},
    "manager01": {"name": "Manager 01", "role": "LINE_MANAGER", "department": "ENGINEERING", "manager": ""},
    "security01": {"name": "Security 01", "role": "SECURITY_ANALYST", "department": "IT_SECURITY", "manager": ""},
}

# Who may approve: policy approver_role -> ServiceNow group. LINE_MANAGER is not a group: it is the caller's
# manager. A role missing here (e.g. CHANGE_ADVISORY_BOARD) has no approvers -> every approval is refused.
APPROVER_GROUPS = {"SECURITY_TEAM": "IT Security", "IT_SERVICE_OWNER": "IT Service Owners",
                   "CHANGE_ADVISORY_BOARD": "Change Advisory Board", "AI_GOVERNANCE_BOARD": "AI Governance Board",
                   "APPLICATION_OWNER": "Application Owners", "DATA_OWNER": "Data Owners"}
# One approver record per group (no login). Created by servicenow_setup (item 3.2).
APPROVER_USERS = {"IT Security": ("security01", "SECURITY_ANALYST"),
                  "IT Service Owners": ("serviceowner01", "IT_SERVICE_OWNER"),
                  "Change Advisory Board": ("cab01", "CHANGE_MANAGER"),
                  "AI Governance Board": ("aigov01", "AI_GOVERNANCE_LEAD"),
                  "Application Owners": ("appowner01", "APPLICATION_OWNER"),
                  "Data Owners": ("dataowner01", "DATA_OWNER")}
# Group members for the LOCAL source (mirrors servicenow_setup.GROUPS)
LOCAL_GROUP_MEMBERS = {g: {u} for g, (u, _title) in APPROVER_USERS.items()}
