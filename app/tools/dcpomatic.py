"""
DCP-o-matic: εύρεση, εγκατάσταση και μετατροπή βίντεο σε DCP μέσω των εντολών του
(dcpomatic2_create για το project, dcpomatic2_cli για την κωδικοποίηση).

Το DCP-o-matic (https://dcpomatic.com, GPL) είναι ξεχωριστό πρόγραμμα· το καλούμε, δεν το ενσωματώνουμε.
Η λήψη του γίνεται από τη σελίδα του στον browser (περνά από σελίδα δωρεάς, χωρίς απευθείας link)·
το ReelCheck βρίσκει τον installer στις Λήψεις και κάνει μόνο του την εγκατάσταση.
"""
import glob
import os
import re
import shutil
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

from app.core.config import APP_NAME, APP_VERSION, get_setting, log, set_setting
from app.core.hashing import Cancelled

DOWNLOAD_PAGE = "https://dcpomatic.com/download"
DCP_TIME_RATE = 96000          # μονάδες χρόνου του DCP-o-matic ανά δευτερόλεπτο
REEL_BY_VIDEO = "1"            # ReelType: 0 = ένα reel, 1 = ένα reel ανά βίντεο, 2 = ανά διάρκεια
CONTENT_TYPES = ("FTR", "SHR", "TLR", "TSR", "ADV", "PSA", "POL", "TST", "XSN", "RTG")
NO_WINDOW = 0x08000000         # CREATE_NO_WINDOW
_HEADERS = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}


# ======================================================================== ΕΥΡΕΣΗ
def _candidate_dirs():
    saved = get_setting("dcpomatic_dir")
    if saved:
        yield saved
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            for d in sorted(glob.glob(os.path.join(base, "DCP-o-matic*")), reverse=True):
                yield os.path.join(d, "bin")
    for p in (shutil.which("dcpomatic2_cli"), shutil.which("dcpomatic2_cli.exe")):
        if p:
            yield os.path.dirname(p)
    yield from _registry_dirs()


def _registry_dirs():
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            root = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                    if "DCP-o-matic" not in str(winreg.QueryValueEx(k, "DisplayName")[0]):
                        continue
                    for val in ("InstallLocation", "UninstallString"):
                        try:
                            p = str(winreg.QueryValueEx(k, val)[0]).strip('"')
                        except OSError:
                            continue
                        d = p if os.path.isdir(p) else os.path.dirname(p)
                        yield os.path.join(d, "bin")
                        yield d
            except OSError:
                continue


def find_dcpomatic():
    """{'dir', 'cli', 'create', 'version'} ή None αν δεν είναι εγκατεστημένο."""
    for d in _candidate_dirs():
        cli, create = os.path.join(d, "dcpomatic2_cli.exe"), os.path.join(d, "dcpomatic2_create.exe")
        if os.path.isfile(cli) and os.path.isfile(create):
            info = {"dir": d, "cli": cli, "create": create, "version": tool_version(cli)}
            set_setting("dcpomatic_dir", d)
            return info
    return None


def tool_version(cli):
    try:
        out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stdout
        m = re.search(r"version\s+([\d.]+)", out)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


# ======================================================================== ΛΗΨΗ / ΕΓΚΑΤΑΣΤΑΣΗ
def latest_version(timeout=15):
    """Τελευταία σταθερή έκδοση για Windows 64-bit, από τη σελίδα λήψης."""
    req = urllib.request.Request(DOWNLOAD_PAGE, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    m = re.search(r"dl\?id=windows-64&(?:amp;)?version=([\d.]+)", page)
    if not m:
        raise ValueError("Δεν βρέθηκε η έκδοση για Windows στη σελίδα του DCP-o-matic")
    return m.group(1)


def downloads_folder():
    """Ο φάκελος Λήψεις του χρήστη (και αν έχει μετακινηθεί)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            p = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
            if os.path.isdir(p):
                return p
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def find_downloaded_installer(since=0.0):
    """
    Installer του DCP-o-matic που κατέβηκε στις Λήψεις μετά το «since» και έχει ολοκληρωθεί
    (όχι .crdownload/.part, ίδιο μέγεθος για 2 δευτ., αρχίζει με MZ). Αλλιώς None.
    """
    folder = downloads_folder()
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if ("dcp-o-matic" in f.lower() or "dcpomatic" in f.lower()) and f.lower().endswith(".exe")]
    except OSError:
        return None
    files = [f for f in files if os.path.getmtime(f) >= since - 5]
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        try:
            size = os.path.getsize(f)
            time.sleep(2)
            if size < 10_000_000 or os.path.getsize(f) != size:
                continue
            with open(f, "rb") as fh:
                if fh.read(2) == b"MZ":
                    return f
        except OSError:
            continue
    return None


def run_installer(installer, silent=True):
    """
    Εκτελεί τον installer με δικαιώματα διαχειριστή (τα Windows ρωτούν με UAC) και περιμένει να τελειώσει.
    silent: χωρίς παράθυρα (/S). Επιστρέφει τον κωδικό εξόδου (ή None αν δεν δόθηκε άδεια).
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    sei = SHELLEXECUTEINFO(cbSize=ctypes.sizeof(SHELLEXECUTEINFO), fMask=0x40,  # SEE_MASK_NOCLOSEPROCESS
                           lpVerb="runas", lpFile=installer, lpParameters="/S" if silent else None, nShow=1)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)) or not sei.hProcess:
        return None  # ο χρήστης αρνήθηκε το UAC
    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    log.info("Installer DCP-o-matic: κωδικός %s", code.value)
    return code.value


def install(installer):
    """
    Σιωπηλή εγκατάσταση (τα Windows ζητούν άδεια διαχειριστή). Αν δεν βρεθεί μετά, ξανά με κανονικό παράθυρο.
    Επιστρέφει το find_dcpomatic() ή πετά RuntimeError.
    """
    if run_installer(installer, silent=True) is None:
        raise RuntimeError("Η εγκατάσταση ακυρώθηκε (δεν δόθηκε άδεια διαχειριστή).")
    info = find_dcpomatic()
    if not info:
        run_installer(installer, silent=False)
        info = find_dcpomatic()
    if not info:
        raise RuntimeError("Το DCP-o-matic δεν βρέθηκε μετά την εγκατάσταση.")
    return info


# ======================================================================== PROJECT
def _run(args, timeout=600):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        tail = "\n".join([l for l in (r.stderr or r.stdout).splitlines() if "GLib" not in l and l.strip()][-6:])
        raise RuntimeError(f"Το DCP-o-matic απέτυχε (κωδικός {r.returncode}):\n{tail}")
    return r


def create_project(tools, film_dir, entries, opts):
    """
    Φτιάχνει project του DCP-o-matic. entries: [{"path", "srt"(ή None), "gain_db"}] με τη σειρά προβολής.
    opts: name, kind, ratio ('185'|'239'), standard ('SMPTE'|'interop'), fps (ή None), gap (δευτ.),
          sub_lang, reel_per_film, use_dcnc_name.
    """
    args = [tools["create"], "-n", opts["name"], "-c", opts.get("kind", "FTR"),
            "--container-ratio", opts.get("ratio", "185"), "--standard", opts.get("standard", "SMPTE"),
            "--no-encrypt", "--twok", "-o", film_dir]
    if opts.get("fps"):
        args += ["-f", str(opts["fps"])]
    if not opts.get("use_dcnc_name", True):
        args.append("--no-use-isdcf-name")
    args += project_paths(entries)
    _run(args)
    patch_project(os.path.join(film_dir, "metadata.xml"), entries, opts)
    return film_dir


def patch_project(metadata, entries, opts):
    """Reels ανά βίντεο, κενό ανάμεσα, υπότιτλοι στη θέση του βίντεό τους, γλώσσα υποτίτλων, εξίσωση έντασης."""
    tree = ET.parse(metadata)
    root = tree.getroot()
    if opts.get("reel_per_film", True):
        root.find("ReelType").text = REEL_BY_VIDEO
    playlist = root.find("Playlist")
    videos = [c for c in playlist.findall("Content") if c.findtext("Type") == "FFmpeg"]
    gap = int(round(float(opts.get("gap", 0) or 0) * DCP_TIME_RATE))
    by_path = {}
    for i, (c, e) in enumerate(zip(videos, entries)):
        pos = int(c.findtext("Position") or 0) + i * gap
        c.find("Position").text = str(pos)
        by_path[os.path.normcase(e["path"])] = pos
        if e.get("gain_db"):
            g = c.find("AudioGain")
            if g is not None:
                g.text = f"{e['gain_db']:.2f}"
    srt_owner = {os.path.normcase(e["srt"]): os.path.normcase(e["path"]) for e in entries if e.get("srt")}
    lang = (opts.get("sub_lang") or "").strip().lower()
    for c in playlist.findall("Content"):
        if c.findtext("Type") != "TextSubtitle":
            continue
        owner = srt_owner.get(os.path.normcase(c.findtext("Path") or ""))
        if owner in by_path:
            c.find("Position").text = str(by_path[owner])
        txt = c.find("Text")
        if lang and txt is not None and txt.find("Language") is None:
            ET.SubElement(txt, "Language").text = lang
    tree.write(metadata, encoding="UTF-8", xml_declaration=True)


def project_paths(entries):
    """Τα αρχεία που δίνονται στο dcpomatic2_create: κάθε βίντεο και αμέσως μετά το .srt του."""
    out = []
    for e in entries:
        out.append(e["path"])
        if e.get("srt"):
            out.append(e["srt"])
    return out


# ======================================================================== ΚΩΔΙΚΟΠΟΙΗΣΗ
_PROGRESS_RE = re.compile(r"(\d+)%(?:;\s*(\d+)/(\d+) frames)?(?:;\s*([\d.]+) fps)?")


def make_dcp(tools, film_dir, progress=None, cancel=None, threads=None):
    """Τρέχει το dcpomatic2_cli. Επιστρέφει τον φάκελο του DCP που βγήκε."""
    args = [tools["cli"], "-r"]
    if threads:
        args += ["-t", str(threads)]
    args.append(film_dir)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    buf, tail = b"", []
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise Cancelled()
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in (b"\r", b"\n"):
                line = buf.decode("utf-8", "replace").strip()
                buf = b""
                if line:
                    tail = (tail + [line])[-8:]
                    m = _PROGRESS_RE.search(line)
                    if m and progress:
                        fps = f", {m.group(4)} καρέ/δευτ." if m.group(4) else ""
                        frames = f" ({m.group(2)}/{m.group(3)} καρέ)" if m.group(2) else ""
                        progress(int(m.group(1)) / 100, f"Κωδικοποίηση {m.group(1)}%{frames}{fps}")
            else:
                buf += ch
    finally:
        proc.wait()
    if proc.returncode != 0:
        clean = [l for l in tail if "GLib" not in l]
        raise RuntimeError("Το DCP-o-matic απέτυχε:\n" + "\n".join(clean[-5:]))
    for d in sorted(os.listdir(film_dir)):
        p = os.path.join(film_dir, d)
        if os.path.isdir(p) and any(f.upper().startswith("ASSETMAP") for f in os.listdir(p)):
            return p
    raise RuntimeError("Δεν βρέθηκε το DCP μετά την κωδικοποίηση.")


def finish_output(dcp_dir, out_dir, film_dir, keep_project=False):
    """Μεταφέρει το DCP στον φάκελο εξόδου και σβήνει τα προσωρινά (j2c κ.λπ.). Επιστρέφει τη νέα διαδρομή."""
    target = os.path.join(out_dir, os.path.basename(dcp_dir))
    if os.path.exists(target):
        n = 2
        while os.path.exists(f"{target}_{n}"):
            n += 1
        target = f"{target}_{n}"
    shutil.move(dcp_dir, target)
    if not keep_project:
        shutil.rmtree(film_dir, ignore_errors=True)
    return target


def estimate_bytes(seconds, mbit=150):
    """Χώρος που χρειάζεται: DCP + προσωρινά καρέ (περίπου διπλάσιος από το τελικό DCP)."""
    return int(seconds * mbit * 1_000_000 / 8 * 2.2)
