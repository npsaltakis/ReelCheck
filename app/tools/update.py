"""
Έλεγχος για νέα έκδοση στο GitHub και αυτόματη αναβάθμιση του .exe.

Ροή: check_update() -> download_update() -> install_update().
Το τρέχον .exe είναι κλειδωμένο όσο τρέχει, οπότε την αντικατάσταση την κάνει ένα μικρό script (.bat)
που περιμένει να κλείσει το πρόγραμμα, βάζει το νέο αρχείο στη θέση του και το ξανανοίγει.
Ρωτά μόνο το δημόσιο GitHub, δεν στέλνει δεδομένα χρήστη.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from app.core.config import APP_NAME, APP_VERSION, NO_WINDOW, log
from app.core.util import Cancelled

GITHUB_REPO = "npsaltakis/ReelCheck"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
EXE_NAME = "ReelCheck.exe"
UPDATE_DIR = os.path.join(tempfile.gettempdir(), "ReelCheck_update")
_HEADERS = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def is_newer(tag, current=APP_VERSION):
    return _ver(tag) > _ver(current)


def can_self_update():
    """Μόνο το .exe αναβαθμίζεται μόνο του· από τον κώδικα γίνεται με git pull."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def parse_release(data):
    """Από την απάντηση του GitHub API: {version, page, exe_url, size, sha256}."""
    tag = data.get("tag_name", "")
    asset = next((a for a in data.get("assets", []) if a.get("name", "").lower() == EXE_NAME.lower()), None)
    digest = (asset or {}).get("digest") or ""
    return {"version": tag.lstrip("vV"), "page": data.get("html_url") or RELEASES_URL,
            "exe_url": (asset or {}).get("browser_download_url"), "size": (asset or {}).get("size") or 0,
            "sha256": digest[7:].lower() if digest.lower().startswith("sha256:") else ""}


def latest_release(timeout=6):
    req = urllib.request.Request(API_LATEST, headers=dict(_HEADERS, Accept="application/vnd.github+json"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_release(json.loads(r.read().decode("utf-8")))


def check_update(timeout=6):
    """Το release αν είναι νεότερο από την τρέχουσα έκδοση, αλλιώς None."""
    rel = latest_release(timeout)
    return rel if is_newer(rel["version"]) else None


def download_update(rel, progress=None, cancel=None, dest_dir=UPDATE_DIR):
    """
    Κατεβάζει το νέο .exe σε προσωρινό φάκελο και ελέγχει μέγεθος (και SHA-256 όταν το δίνει το GitHub).
    Επιστρέφει τη διαδρομή του. Πετά ValueError αν κάτι δεν ταιριάζει.
    """
    if not rel.get("exe_url"):
        raise ValueError("Το release δεν έχει ReelCheck.exe")
    os.makedirs(dest_dir, exist_ok=True)
    final = os.path.join(dest_dir, f"ReelCheck_{rel['version']}.exe")
    if os.path.isfile(final) and (not rel.get("size") or os.path.getsize(final) == rel["size"]):
        return final  # κατέβηκε ήδη σε προηγούμενη φορά
    part = final + ".part"
    h = hashlib.sha256()
    done = 0
    req = urllib.request.Request(rel["exe_url"], headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as out:
        total = int(r.headers.get("Content-Length") or rel.get("size") or 0)
        while True:
            if cancel is not None and cancel.is_set():
                out.close()
                os.remove(part)
                raise Cancelled()
            b = r.read(1024 * 1024)
            if not b:
                break
            out.write(b)
            h.update(b)
            done += len(b)
            if progress:
                progress(done / total if total else 0, f"Λήψη v{rel['version']}: {done // 1048576} MB")
    _verify_download(part, done, h.hexdigest(), rel)
    os.replace(part, final)
    log.info("Κατέβηκε η v%s: %s (%d bytes)", rel["version"], final, done)
    return final


def _verify_download(path, size, sha256, rel):
    problem = None
    if rel.get("size") and size != rel["size"]:
        problem = f"μέγεθος {size} αντί {rel['size']}"
    elif rel.get("sha256") and sha256 != rel["sha256"]:
        problem = "λάθος SHA-256"
    else:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                problem = "δεν είναι πρόγραμμα Windows"
    if problem:
        os.remove(path)
        raise ValueError(f"Η λήψη δεν είναι σωστή ({problem})")


SWAP_BAT = r"""@echo off
chcp 65001 >nul
rem ReelCheck: αντικατάσταση του .exe μετά το κλείσιμο του προγράμματος
set "NEW={new}"
set "CUR={cur}"
set /a N=0
:retry
move /Y "%NEW%" "%CUR%" >nul 2>nul && goto ok
set /a N+=1
if %N% geq {wait} goto fail
"%SystemRoot%\System32\PING.EXE" -n 2 127.0.0.1 >nul
goto retry
:ok
{start}
del "%~f0" & exit /b 0
:fail
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Η αναβάθμιση δεν ολοκληρώθηκε: το ReelCheck.exe είναι ακόμα ανοιχτό. Κλείσε όλα τα παράθυρα του ReelCheck και τρέξε: %~f0', 'ReelCheck')" >nul
"""


def write_swap_script(new_exe, current_exe, restart=True, wait_seconds=90, script_dir=UPDATE_DIR):
    """Γράφει το .bat που κάνει την αντικατάσταση. Επιστρέφει τη διαδρομή του."""
    os.makedirs(script_dir, exist_ok=True)
    path = os.path.join(script_dir, "install_update.bat")
    text = SWAP_BAT.format(new=new_exe, cur=current_exe, wait=int(wait_seconds),
                           start='start "" "%CUR%"' if restart else "rem χωρίς επανεκκίνηση")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)
    return path


def install_update(new_exe, current_exe=None, restart=True):
    """
    Ξεκινά το script αντικατάστασης σε ανεξάρτητη διεργασία. Μετά πρέπει να κλείσει το πρόγραμμα
    (και όλα τα παράθυρα αναπαραγωγής), ώστε να ξεκλειδωθεί το .exe.
    """
    current_exe = current_exe or sys.executable
    bat = write_swap_script(new_exe, current_exe, restart)
    flags = 0x00000008 | 0x00000200 | NO_WINDOW  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True, cwd=os.path.dirname(bat))
    log.info("Αναβάθμιση: %s -> %s", new_exe, current_exe)
    return bat
