"""Fixed configuration for the endpoint enforcement service (Objective 2).
Only the targets listed here can ever be changed by the service."""

EMPLOYEE_USER = "employee01"          # the restricted standard Windows account
HOST, PORT = "127.0.0.1", 8765        # localhost only - never exposed to the network

# Domains managed by policy. They are blocked by default (apply_employee_policies.ps1);
# the agent may add/remove them from the Chrome URLAllowlist after Policy & Risk approves.
MANAGED_DOMAINS = {
    "chatgpt.com", "chat.openai.com", "openai.com", "claude.ai", "perplexity.ai",
    "gemini.google.com", "github.com", "pypi.org", "wetransfer.com", "mega.nz",
}

# Software the agent is allowed to install (winget package IDs), machine-wide.
APPROVED_PACKAGES = {
    "Google.Chrome": "Google Chrome",
    "Microsoft.VisualStudioCode": "Visual Studio Code",
    "Git.Git": "Git for Windows",
    "Python.Python.3.12": "Python 3.12",
    "Postman.Postman": "Postman",
}
