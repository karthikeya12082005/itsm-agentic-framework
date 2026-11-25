"""Fixed configuration for the endpoint enforcement service (Objective 2).
Only the targets listed here can ever be changed by the service."""

EMPLOYEE_USER = "employee01"          # the restricted standard Windows account
HOST, PORT = "127.0.0.1", 8765        # localhost only - never exposed to the network

# Domains managed by policy. They are blocked by default (apply_employee_policies.ps1);
# the agent may add/remove them from the Chrome URLAllowlist after Policy & Risk approves.
MANAGED_DOMAINS = {
    "chatgpt.com", "chat.openai.com", "openai.com", "claude.ai", "perplexity.ai",
    "gemini.google.com", "github.com", "pypi.org", "wetransfer.com", "mega.nz",
    # added in 3.8 (level 1): blocked by default too (the service adds missing ones to URLBlocklist at start)
    "dropbox.com", "deepseek.com", "chat.deepseek.com", "stackoverflow.com", "docs.python.org",
    "huggingface.co", "kaggle.com", "colab.research.google.com",
}

# Software the agent is allowed to install (winget package IDs), machine-wide. DENY-only software
# (qBittorrent, uTorrent) is deliberately NOT here: the service could never install it.
APPROVED_PACKAGES = {
    "Google.Chrome": "Google Chrome",
    "Microsoft.VisualStudioCode": "Visual Studio Code",
    "Git.Git": "Git for Windows",
    "Python.Python.3.12": "Python 3.12",
    "Postman.Postman": "Postman",
    "Zoom.Zoom": "Zoom Workplace",
    "SlackTechnologies.Slack": "Slack",
    "Microsoft.Teams": "Microsoft Teams",
    "Notepad++.Notepad++": "Notepad++",
    "7zip.7zip": "7-Zip",
}

# Item 3.1: an installer fails/cancels while the app is running -> pre-check these process names first.
PACKAGE_PROCESSES = {
    "Google.Chrome": ["chrome.exe"],
    "Microsoft.VisualStudioCode": ["Code.exe"],
    "Git.Git": ["git-bash.exe", "bash.exe"],
    "Python.Python.3.12": [],
    "Postman.Postman": ["Postman.exe"],
    "Zoom.Zoom": ["Zoom.exe"],
    "SlackTechnologies.Slack": ["slack.exe"],
    "Microsoft.Teams": ["ms-teams.exe", "Teams.exe"],
    "Notepad++.Notepad++": ["notepad++.exe"],
    "7zip.7zip": ["7zFM.exe", "7zG.exe"],
}
