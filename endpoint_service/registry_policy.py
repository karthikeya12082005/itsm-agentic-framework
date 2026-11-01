"""Read/write Chrome policies for ONE Windows user (employee01) in HKEY_USERS\\<SID>.
Must run with administrator rights. Works whether employee01 is signed in or not."""
import contextlib
import os
import subprocess
import threading
import time
import winreg

from .endpoint_config import EMPLOYEE_USER

_LOCK = threading.Lock()
CHROME_KEY = r"Software\Policies\Google\Chrome"


def get_sid(username: str = EMPLOYEE_USER) -> str:
    cmd = ("(New-Object System.Security.Principal.NTAccount('%s')).Translate("
           "[System.Security.Principal.SecurityIdentifier]).Value" % username)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True, check=True)
    sid = out.stdout.strip()
    if not sid.startswith("S-1-5-"):
        raise RuntimeError(f"Could not resolve SID for {username}: {out.stderr.strip()}")
    return sid


def _profile_path(sid: str) -> str:
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList" + "\\" + sid
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        return os.path.expandvars(winreg.QueryValueEx(k, "ProfileImagePath")[0])


def _hive_loaded(sid: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, sid):
            return True
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def user_hive(sid: str):
    """Yields with the user's registry hive available under HKEY_USERS\\<sid>."""
    with _LOCK:
        loaded_here = False
        if not _hive_loaded(sid):
            ntuser = os.path.join(_profile_path(sid), "NTUSER.DAT")
            subprocess.run(["reg", "load", f"HKU\\{sid}", ntuser], capture_output=True, check=True)
            loaded_here = True
        try:
            yield
        finally:
            if loaded_here:
                for _ in range(5):                        # handles may need a moment to close
                    r = subprocess.run(["reg", "unload", f"HKU\\{sid}"], capture_output=True)
                    if r.returncode == 0:
                        break
                    time.sleep(1)


def _read_list(sid: str, sub: str) -> list:
    path = f"{sid}\\{CHROME_KEY}\\{sub}"
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, path) as k:
            items, i = [], 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(k, i)
                    items.append((int(name) if name.isdigit() else 10**6, value))
                    i += 1
                except OSError:
                    break
        return [v for _, v in sorted(items)]
    except FileNotFoundError:
        return []


def _write_list(sid: str, sub: str, values: list) -> None:
    path = f"{sid}\\{CHROME_KEY}\\{sub}"
    with winreg.CreateKeyEx(winreg.HKEY_USERS, path, 0, winreg.KEY_ALL_ACCESS) as k:
        names, i = [], 0
        while True:
            try:
                names.append(winreg.EnumValue(k, i)[0])
                i += 1
            except OSError:
                break
        for n in names:
            winreg.DeleteValue(k, n)
        for idx, v in enumerate(values, start=1):
            winreg.SetValueEx(k, str(idx), 0, winreg.REG_SZ, v)


def _read_dword(sid: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, f"{sid}\\{CHROME_KEY}") as k:
            return winreg.QueryValueEx(k, name)[0]
    except FileNotFoundError:
        return None


def get_policy_state(sid: str) -> dict:
    with user_hive(sid):
        return {"blocklist": _read_list(sid, "URLBlocklist"),
                "allowlist": _read_list(sid, "URLAllowlist"),
                "download_restrictions": _read_dword(sid, "DownloadRestrictions")}


def set_domain_allowed(sid: str, domain: str, allowed: bool) -> dict:
    """Adds (allowed=True) or removes (allowed=False) a domain in URLAllowlist.
    Returns the allowlist before and after the change."""
    with user_hive(sid):
        before = _read_list(sid, "URLAllowlist")
        after = list(before)
        if allowed and domain not in after:
            after.append(domain)
        if not allowed:
            after = [d for d in after if d != domain]
        if after != before:
            _write_list(sid, "URLAllowlist", after)
        return {"before": before, "after": _read_list(sid, "URLAllowlist")}
