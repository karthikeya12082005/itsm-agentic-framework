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
    # ---- software (winget IDs) ----
    "Google.Chrome":              {"kind": "software", "policy_id": "POL-0017", "label": "Google Chrome"},
    "Microsoft.VisualStudioCode": {"kind": "software", "policy_id": "POL-0018", "label": "Visual Studio Code"},
    "Git.Git":                    {"kind": "software", "policy_id": "POL-0019", "label": "Git"},
    "Python.Python.3.12":         {"kind": "software", "policy_id": "POL-0019", "label": "Python 3.12"},
    "Postman.Postman":            {"kind": "software", "policy_id": "POL-0019", "label": "Postman"},
}

# Approved alternative offered when a request is denied (policy_id -> text)
ALTERNATIVES = {
    "POL-0030": "Use the approved AI assistant Gemini (gemini.google.com); you can request it through a ticket.",
    "POL-0027": "Use the company-approved file-sharing service instead of personal file-sharing sites.",
    "POL-0020": "Choose an approved application from the software catalogue.",
}

# Keywords for recognising software in free text -> winget ID
SOFTWARE_KEYWORDS = {
    "google chrome": "Google.Chrome", "chrome": "Google.Chrome",
    "visual studio code": "Microsoft.VisualStudioCode", "vs code": "Microsoft.VisualStudioCode",
    "vscode": "Microsoft.VisualStudioCode", "git": "Git.Git", "python": "Python.Python.3.12",
    "postman": "Postman.Postman",
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
APPROVER_GROUPS = {"SECURITY_TEAM": "IT Security"}
# Group members for the LOCAL source (mirrors servicenow_setup.GROUPS)
LOCAL_GROUP_MEMBERS = {"IT Security": {"security01"}}
