"""Machine-wide software install/uninstall with winget (approved package IDs only)."""
import subprocess

COMMON = ["--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements",
          "--disable-interactivity"]


def is_installed(package_id: str) -> bool:
    r = subprocess.run(["winget", "list", "--exact", "--id", package_id, "--accept-source-agreements",
                        "--disable-interactivity"], capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and package_id.lower() in r.stdout.lower()


def install(package_id: str) -> dict:
    if is_installed(package_id):
        return {"changed": False, "returncode": 0, "message": "already installed"}
    r = subprocess.run(["winget", "install", "--id", package_id, "--scope", "machine", *COMMON],
                       capture_output=True, text=True, timeout=1200)
    return {"changed": r.returncode == 0, "returncode": r.returncode, "message": r.stdout[-800:] + r.stderr[-400:]}


def uninstall(package_id: str) -> dict:
    r = subprocess.run(["winget", "uninstall", "--id", package_id, *COMMON[:2], "--disable-interactivity"],
                       capture_output=True, text=True, timeout=1200)
    return {"changed": r.returncode == 0, "returncode": r.returncode, "message": r.stdout[-800:] + r.stderr[-400:]}
