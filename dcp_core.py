"""
Ανάγνωση και έλεγχος DCP: ASSETMAP / CPL / PKL, υπότιτλοι, hash, σημειώσεις, αναφορές.
Δεν εξαρτάται από γραφικά, ώστε να χρησιμοποιείται και από τον player και από το παράθυρο.
"""
import base64
import csv
import datetime
import hashlib
import html
import io
import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import traceback
import xml.etree.ElementTree as ET
from fractions import Fraction

APP_NAME = "ReelCheck"
APP_VERSION = "1.1"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ReelCheck")
LOG_FILE = os.path.join(APP_DIR, "reelcheck.log")
NOTES_DIR = os.path.join(APP_DIR, "notes")
HASH_CACHE_FILE = os.path.join(APP_DIR, "hash_cache.json")
SHOTS_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ReelCheck")

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm", ".ts", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg")

# Σειρά καναλιών DCP (SMPTE 428-12 / ISDCF)
CH_NAMES = ["L", "R", "C", "LFE", "Ls", "Rs", "HI", "VI", "9", "10", "BsL", "BsR", "Motion", "Sync", "SL", "16"]

log = logging.getLogger("dcp")


# ----------------------------------------------------------------- γενικά
def resource(name):
    """Αρχείο δίπλα στο script, ή μέσα στο .exe (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, name)
    return p if os.path.isfile(p) else None


SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")


def get_setting(key, default=None):
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError):
        return default


def set_setting(key, value):
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s[key] = value
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def system_theme():
    """'light' ή 'dark' από τις ρυθμίσεις των Windows (Εφαρμογές: φωτεινό/σκούρο)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except (OSError, ImportError):
        return "light"


def dark_title_bar(tk_root, dark):
    """Σκούρα μπάρα τίτλου στα Windows 10/11 (αγνοείται αλλού)."""
    try:
        import ctypes
        tk_root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(tk_root.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (νέο / παλιό build)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        # ανανέωση πλαισίου ώστε να φανεί αμέσως
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    except Exception:
        pass


def migrate_old_data():
    """Μεταφορά δεδομένων από το παλιό όνομα (DCP Player): σημειώσεις, cache hash, τελευταίος φάκελος."""
    import shutil
    old = os.path.join(os.path.dirname(APP_DIR), "DCPPlayer")
    if not os.path.isdir(old) or os.path.isdir(NOTES_DIR):
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        for name in ("notes", "hash_cache.json", "last_folder.txt"):
            src, dst = os.path.join(old, name), os.path.join(APP_DIR, name)
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.copytree(src, dst)
            elif os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except OSError:
        pass


def setup_logging(role):
    """Log στο %APPDATA%\\ReelCheck\\reelcheck.log (μέχρι 3 αρχεία x 2MB)."""
    migrate_old_data()
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(f"%(asctime)s [{role}:%(process)d] %(levelname)s %(message)s"))
    log.addHandler(h)
    if sys.stderr:
        log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)

    def hook(t, v, tb):
        log.critical("Μη αναμενόμενο σφάλμα:\n%s", "".join(traceback.format_exception(t, v, tb)))
        sys.__excepthook__(t, v, tb)

    sys.excepthook = hook
    threading.excepthook = lambda a: log.critical(
        "Σφάλμα σε thread %s:\n%s", a.thread.name if a.thread else "?",
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    log.info("---- έναρξη %s v%s | Python %s | %s | frozen=%s | args=%s", role, APP_VERSION,
             platform.python_version(), platform.platform(), getattr(sys, "frozen", False), sys.argv[1:])


def fmt_time(s, rate=None):
    s = max(0.0, s or 0.0)
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    if rate:
        fr = int((s - int(s)) * float(rate))
        return f"{h:02d}:{m:02d}:{sec:02d}:{fr:02d}"
    return f"{h:02d}:{m:02d}:{sec:02d}"


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def norm_id(s):
    s = (s or "").strip().lower()
    return s[9:] if s.startswith("urn:uuid:") else s


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def text(el, name, default=None):
    c = child(el, name)
    return c.text.strip() if c is not None and c.text and c.text.strip() else default


def find_all(el, name):
    return [e for e in el.iter() if local(e.tag) == name]


def xml_root(path):
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None


# ----------------------------------------------------------------- ASSETMAP / CPL / PKL
def read_assetmap(folder):
    path = None
    for n in ("ASSETMAP.xml", "ASSETMAP", "assetmap.xml", "assetmap"):
        p = os.path.join(folder, n)
        if os.path.isfile(p):
            path = p
            break
    amap = {}
    if not path:
        return amap
    root = xml_root(path)
    if root is None:
        log.error("Χαλασμένο ASSETMAP: %s", path)
        return amap
    for a in find_all(root, "Asset"):
        aid = norm_id(text(a, "Id"))
        paths = [e.text.strip() for e in find_all(a, "Path") if e.text and e.text.strip()]
        if aid and paths:
            rel = paths[0]
            for pre in ("file:///", "file://", "file:"):
                if rel.startswith(pre):
                    rel = rel[len(pre):]
            amap[aid] = os.path.normpath(os.path.join(folder, rel))
    return amap


class Reel:
    def __init__(self):
        self.pic = self.snd = None
        self.pic_entry = self.snd_entry = 0
        self.dur = 0                 # σε καρέ (edit units της εικόνας)
        self.rate = Fraction(24)
        self.snd_rate = Fraction(24)
        self.snd_dur = 0
        self.stereo = False
        self.subs = []               # [(είδος 'sub'|'cc', αρχείο, entry, rate)]
        self.start = 0.0             # δευτερόλεπτα στη συνολική ταινία

    @property
    def seconds(self):
        return float(self.dur / self.rate) if self.rate else 0.0


class CPL:
    def __init__(self, path):
        self.path = path
        self.folder = os.path.dirname(os.path.abspath(path))
        self.id = None
        self.title = "?"
        self.kind = ""
        self.issuer = ""
        self.annotation = ""
        self.reels = []
        self.encrypted = False
        self.missing = []
        self.assets = {}             # id -> αρχείο (ή None αν λείπει)
        self.external = {}           # id -> φάκελος άλλου DCP (VF που παίρνει assets από OV)
        self.is_file = False         # κανονικό αρχείο βίντεο, όχι DCP

    @property
    def seconds(self):
        return sum(r.seconds for r in self.reels)

    @property
    def rate(self):
        return self.reels[0].rate if self.reels else Fraction(24)


# Στοιχεία που ζουν μόνο μέσα στο CPL, δεν έχουν αρχείο.
CPL_ONLY_ASSETS = {"MainMarkers", "CompositionMetadataAsset"}


def sibling_assetmap(folder):
    """ASSETMAP των άλλων DCP δίπλα στον φάκελο (ένα VF παίρνει εικόνα/ήχο από το OV του)."""
    folder = os.path.normcase(os.path.abspath(folder))
    parent = os.path.dirname(folder)
    out = {}
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return out
    for n in names:
        d = os.path.join(parent, n)
        if os.path.normcase(d) == folder or not os.path.isdir(d):
            continue
        try:
            if any(f.upper().startswith("ASSETMAP") for f in os.listdir(d)):
                for aid, f in read_assetmap(d).items():
                    out.setdefault(aid, f)
        except OSError:
            continue
    return out


def parse_cpl(path, amap, others=None):
    """others: dict ή callable που δίνει dict id -> αρχείο από άλλα DCP (για VF)."""
    root = xml_root(path)
    if root is None or local(root.tag) != "CompositionPlaylist":
        return None
    cpl = CPL(path)
    cpl.id = norm_id(text(root, "Id"))
    cpl.title = text(root, "ContentTitleText", os.path.basename(path))
    cpl.kind = text(root, "ContentKind", "")
    cpl.issuer = text(root, "Issuer", "")
    cpl.annotation = text(root, "AnnotationText", "")
    t = 0.0
    for r in find_all(root, "Reel"):
        al = child(r, "AssetList")
        if al is None:
            continue
        reel = Reel()
        for a in al:
            n = local(a.tag)
            aid = norm_id(text(a, "Id"))
            if not aid or n in CPL_ONLY_ASSETS:
                continue
            f = amap.get(aid)
            if not f or not os.path.isfile(f):
                if callable(others):
                    others = others()
                g = (others or {}).get(aid)
                if g and os.path.isfile(g):
                    f = g
                    cpl.external[aid] = os.path.dirname(g)
                else:
                    cpl.missing.append(f"{n}: {f or aid}")
                    f = None
            cpl.assets[aid] = f
            if text(a, "KeyId"):
                cpl.encrypted = True
            er = (text(a, "EditRate", "24 1") or "24 1").split()
            try:
                rate = Fraction(int(er[0]), int(er[1]) if len(er) > 1 else 1)
            except (ValueError, ZeroDivisionError):
                rate = Fraction(24)
            entry = int(text(a, "EntryPoint", "0") or 0)
            intr = int(text(a, "IntrinsicDuration", "0") or 0)
            dur = int(text(a, "Duration", "0") or 0) or max(0, intr - entry)
            if n == "MainSubtitle":
                reel.subs.append(("sub", f, entry, rate))
            elif n in ("MainClosedCaption", "ClosedCaption"):
                reel.subs.append(("cc", f, entry, rate))
            elif n == "MainSound":
                reel.snd, reel.snd_entry, reel.snd_rate, reel.snd_dur = f, entry, rate, dur
            elif n in ("MainPicture", "MainStereoscopicPicture"):
                reel.pic, reel.pic_entry, reel.rate, reel.dur = f, entry, rate, dur
                reel.stereo = n == "MainStereoscopicPicture"
        reel.start = t
        t += reel.seconds
        cpl.reels.append(reel)
    return cpl


def load_dcp(folder):
    amap = read_assetmap(folder)
    cpls = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return cpls
    cache = []

    def others():  # διαβάζεται μόνο αν λείπει κάτι τοπικά
        if not cache:
            cache.append(sibling_assetmap(folder))
        return cache[0]

    for fn in names:
        if fn.lower().endswith(".xml"):
            c = parse_cpl(os.path.join(folder, fn), amap, others)
            if c:
                cpls.append(c)
    return cpls


def find_dcps(folder):
    """Ο φάκελος μπορεί να είναι το ίδιο το DCP ή να περιέχει DCP σε υποφακέλους."""
    out = []
    for root, dirs, files in os.walk(folder):
        if any(f.upper().startswith("ASSETMAP") for f in files):
            out.append(root)
            dirs[:] = []
        dirs.sort()
    return out


def read_pkls(folder):
    """id -> {hash, size, name} από όλα τα PKL του φακέλου."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for fn in names:
        if not fn.lower().endswith(".xml"):
            continue
        root = xml_root(os.path.join(folder, fn))
        if root is None or local(root.tag) != "PackingList":
            continue
        for a in find_all(root, "Asset"):
            aid = norm_id(text(a, "Id"))
            if aid:
                out[aid] = {"hash": text(a, "Hash"), "size": int(text(a, "Size", "0") or 0),
                            "name": text(a, "OriginalFileName") or text(a, "AnnotationText") or "",
                            "pkl": fn}
    return out


def video_cpl(path):
    """Κανονικό αρχείο βίντεο ως «CPL» με ένα reel."""
    import av
    cpl = CPL(path)
    cpl.is_file = True
    cpl.title = os.path.basename(path)
    cpl.kind = "αρχείο βίντεο"
    reel = Reel()
    with av.open(path) as c:
        if c.streams.video:
            vs = c.streams.video[0]
            rate = vs.average_rate or vs.guessed_rate or Fraction(25)
            reel.rate = Fraction(rate)
            if vs.duration and vs.time_base:
                secs = float(vs.duration * vs.time_base)
            elif c.duration:
                secs = c.duration / 1_000_000
            else:
                secs = (vs.frames or 0) / float(reel.rate)
            reel.dur = int(round(secs * float(reel.rate)))
            reel.pic = path
        if c.streams.audio:
            reel.snd = path
            reel.snd_rate = reel.rate
            reel.snd_dur = reel.dur
    srt = os.path.splitext(path)[0] + ".srt"
    if os.path.isfile(srt):
        reel.subs.append(("srt", srt, 0, reel.rate))
    cpl.reels.append(reel)
    return cpl


def load_target(path):
    """CPL (.xml) ή αρχείο βίντεο."""
    if path.lower().endswith(".xml"):
        folder = os.path.dirname(os.path.abspath(path))
        return parse_cpl(path, read_assetmap(folder), lambda: sibling_assetmap(folder))
    return video_cpl(path)


# ----------------------------------------------------------------- ΥΠΟΤΙΤΛΟΙ
def _sub_xml_bytes(data, is_mxf):
    """Interop: σκέτο XML. SMPTE: το XML είναι μέσα στο MXF, το ψάχνουμε στα bytes."""
    if not is_mxf and data.lstrip()[:1] in (b"<", b"\xef"):
        return data
    m = re.search(rb"<([\w.-]+:)?SubtitleReel[\s>]", data)
    if not m:
        m = re.search(rb"<DCSubtitle[\s>]", data)
        if not m:
            return None
        end = data.find(b"</DCSubtitle>", m.start())
        return data[m.start():end + 13] if end > 0 else None
    pre = m.group(1) or b""
    close = b"</" + pre + b"SubtitleReel>"
    end = data.find(close, m.start())
    return data[m.start():end + len(close)] if end > 0 else None


def _png_blobs(data):
    """Όλα τα PNG μέσα σε ένα MXF υποτίτλων, με τη σειρά που βρίσκονται."""
    out, pos = [], 0
    while True:
        s = data.find(b"\x89PNG\r\n\x1a\n", pos)
        if s < 0:
            return out
        e = data.find(b"IEND\xaeB`\x82", s)
        if e < 0:
            return out
        out.append(data[s:e + 8])
        pos = e + 8


def _sub_time(s, tick_rate):
    """HH:MM:SS:xx (xx = ticks/καρέ) ή HH:MM:SS.mmm -> δευτερόλεπτα."""
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    if len(parts) == 4:
        h, m, sec, x = parts
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(x) / float(tick_rate)
    if len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return float(s)


def _truthy(v):
    return (v or "").lower() in ("yes", "true", "1")


def parse_subtitle_file(path, offset):
    """
    Επιστρέφει (entries, γλώσσα, σημείωση). entry = (αρχή, τέλος, items) σε χρόνο ταινίας.
    item: {"type": "text", "text", "italic", "y"} ή {"type": "image", "png", "valign", "vpos", "halign", "hpos"}
    """
    with open(path, "rb") as f:
        data = f.read()
    is_mxf = path.lower().endswith(".mxf")
    xmlb = _sub_xml_bytes(data, is_mxf)
    if not xmlb:
        return [], None, "δεν βρέθηκε XML υποτίτλων"
    root = ET.fromstring(xmlb)
    interop = local(root.tag) == "DCSubtitle"
    lang = text(root, "Language")
    if interop:
        tick = 250
    else:
        r = (text(root, "TimeCodeRate") or text(root, "EditRate") or "24").split()
        tick = Fraction(int(r[0]), int(r[1]) if len(r) > 1 else 1)

    # εικόνες: Interop = αρχεία PNG δίπλα, SMPTE = μέσα στο MXF (αντιστοίχιση με σειρά εμφάνισης)
    img_ids = []
    for im in find_all(root, "Image"):
        k = (im.text or "").strip()
        if k and k not in img_ids:
            img_ids.append(k)
    blobs = _png_blobs(data) if is_mxf and img_ids else []
    folder = os.path.dirname(path)

    def png_for(ref):
        ref = ref.strip()
        if blobs and ref in img_ids and img_ids.index(ref) < len(blobs):
            return blobs[img_ids.index(ref)]
        for cand in (ref, norm_id(ref) + ".png", ref + ".png"):
            p = os.path.join(folder, cand)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return None

    out, missing_imgs = [], 0

    def collect(el, italic, cur):
        it = italic
        if local(el.tag) == "Font" and el.get("Italic") is not None:
            it = _truthy(el.get("Italic"))
        if el.text:
            cur.append((el.text, it))
        for c in el:
            if local(c.tag) == "Space":
                cur.append((" ", it))
            else:
                collect(c, it, cur)
            if c.tail:
                cur.append((c.tail, it))

    def attr(el, *names, default=None):
        for n in names:
            if el.get(n) is not None:
                return el.get(n)
        return default

    def visit(el, italic):
        nonlocal missing_imgs
        if local(el.tag) == "Font" and el.get("Italic") is not None:
            italic = _truthy(el.get("Italic"))
        if local(el.tag) == "Subtitle":
            t_in = _sub_time(el.get("TimeIn", "0:0:0:0"), tick)
            t_out = _sub_time(el.get("TimeOut", "0:0:0:0"), tick)
            items = []
            for tx in el.iter():
                n = local(tx.tag)
                if n not in ("Text", "Image"):
                    continue
                valign = (attr(tx, "VAlign", "Valign", default="bottom")).lower()
                vpos = float(attr(tx, "VPosition", "Vposition", default="0") or 0)
                halign = (attr(tx, "HAlign", "Halign", default="center")).lower()
                hpos = float(attr(tx, "HPosition", "Hposition", default="0") or 0)
                y = vpos if valign == "top" else (50 + vpos if valign == "center" else 100 - vpos)
                if n == "Image":
                    png = png_for(tx.text or "")
                    if png is None:
                        missing_imgs += 1
                        continue
                    items.append({"type": "image", "png": png, "valign": valign, "vpos": vpos,
                                  "halign": halign, "hpos": hpos, "y": y})
                    continue
                cur = []
                collect(tx, italic, cur)
                line = "".join(t for t, _ in cur).strip()
                if line:
                    items.append({"type": "text", "text": line, "y": y,
                                  "italic": any(i for t, i in cur if t.strip())})
            items.sort(key=lambda r: r["y"])
            if items:
                out.append((offset + t_in, offset + t_out, items))
            return
        for c in el:
            visit(c, italic)

    visit(root, False)
    out.sort(key=lambda s: s[0])
    note = f"{missing_imgs} εικόνες υποτίτλων δεν βρέθηκαν" if missing_imgs else None
    return out, lang, note


def parse_srt(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1253", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out = []
    for block in re.split(r"\r?\n\s*\r?\n", txt.strip()):
        lines = block.strip().splitlines()
        for i, l in enumerate(lines):
            m = re.match(r"\s*(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", l)
            if m:
                raw_lines = [x for x in lines[i + 1:] if x.strip()]
                items = [{"type": "text", "text": re.sub(r"<[^>]+>", "", x), "italic": "<i>" in x.lower(),
                          "y": 90 + k} for k, x in enumerate(raw_lines)]
                if items:
                    out.append((_sub_time(m.group(1), 1000), _sub_time(m.group(2), 1000), items))
                break
    return out


def load_tracks(cpl, label_prefix=""):
    """Κομμάτια υποτίτλων ενός CPL: [{"name", "kind", "entries", "notes"}]."""
    groups = {}
    for r in cpl.reels:
        for kind, f, entry, rate in r.subs:
            g = groups.setdefault(kind, {"entries": [], "langs": [], "notes": []})
            if not f:
                g["notes"].append("λείπει αρχείο υποτίτλων")
                continue
            try:
                if kind == "srt":
                    e, lang, note = parse_srt(f), None, None
                else:
                    e, lang, note = parse_subtitle_file(f, r.start - float(entry / rate))
                g["entries"] += e
                if lang and lang not in g["langs"]:
                    g["langs"].append(lang)
                if note:
                    g["notes"].append(note)
            except Exception as ex:
                log.exception("Σφάλμα στους υπότιτλους %s", f)
                g["notes"].append(f"υπότιτλοι: {ex}")
    tracks = []
    for kind, g in groups.items():
        g["entries"].sort(key=lambda s: s[0])
        prefix = {"sub": "", "cc": "CC ", "srt": "SRT "}[kind]
        name = (prefix + "/".join(g["langs"])).strip() or "υπότιτλοι"
        tracks.append({"name": label_prefix + name, "kind": kind, "entries": g["entries"], "notes": g["notes"]})
    return tracks


def sibling_tracks(cpl):
    """Υπότιτλοι από άλλα CPL του ίδιου φακέλου με ίδια διάρκεια (π.χ. VF σε άλλη γλώσσα)."""
    out = []
    if cpl.is_file:
        return out
    for other in load_dcp(cpl.folder):
        if other.id == cpl.id or abs(other.seconds - cpl.seconds) > 1.0:
            continue
        if any(r.subs for r in other.reels):
            out += load_tracks(other, label_prefix=f"[{other.title[:30]}] ")
    return out


def track_summary(cpl):
    tracks = load_tracks(cpl)
    if not tracks:
        return "όχι", []
    parts, notes = [], []
    for t in tracks:
        n_img = sum(1 for e in t["entries"] for it in e[2] if it["type"] == "image")
        s = f"{t['name']}: {len(t['entries'])}"
        if n_img:
            s += f" ({n_img} εικόνες)"
        parts.append(s)
        notes += t["notes"]
    return ", ".join(parts), notes


# ----------------------------------------------------------------- PROBE / THUMBNAIL
def probe(cpl):
    """Ανάλυση εικόνας, codec και κανάλια ήχου από το πρώτο reel."""
    import av
    info = {}
    r = next((r for r in cpl.reels if r.pic), None)
    if r:
        try:
            with av.open(r.pic) as c:
                v = c.streams.video[0]
                info["size"] = (v.codec_context.width, v.codec_context.height)
                info["pix"] = v.codec_context.format.name if v.codec_context.format else "?"
                info["codec"] = v.codec_context.name
        except Exception as e:
            log.exception("Δεν ανοίγει η εικόνα %s", r.pic)
            info["pic_err"] = str(e)
    r = next((r for r in cpl.reels if r.snd), None)
    if r:
        try:
            with av.open(r.snd) as c:
                a = c.streams.audio[0]
                info["ch"] = a.codec_context.channels
                info["sr"] = a.codec_context.sample_rate
        except Exception as e:
            log.exception("Δεν ανοίγει ο ήχος %s", r.snd)
            info["snd_err"] = str(e)
    return info


def thumbnail(cpl, at=None, width=480):
    """PNG (bytes) ενός καρέ: προεπιλογή στο 10% της διάρκειας."""
    import av
    from PIL import Image
    at = cpl.seconds * 0.1 if at is None else at
    reel = next((r for r in cpl.reels if r.pic and r.start <= at < r.start + r.seconds), None)
    reel = reel or next((r for r in cpl.reels if r.pic), None)
    if not reel:
        return None
    with av.open(reel.pic) as c:
        vs = c.streams.video[0]
        if vs.codec_context.name == "jpeg2000":
            vs.codec_context.options = {"lowres": "1"}
        mult = 2 if reel.stereo else 1
        frame_no = (reel.pic_entry + int((max(0.0, at - reel.start)) * float(reel.rate))) * mult
        target = Fraction(frame_no) / (reel.rate * mult)
        start = vs.start_time or 0
        try:
            c.seek(int(target / vs.time_base) + start, stream=vs, backward=True)
        except Exception:
            pass
        for fr in c.decode(vs):
            if fr.time is not None and fr.time - float(start * vs.time_base) + 0.001 < float(target):
                continue
            h = max(2, int(fr.height * width / fr.width) // 2 * 2)
            img = Image.fromarray(fr.reformat(width=width, height=h, format="rgb24").to_ndarray())
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
    return None


# ----------------------------------------------------------------- HASH (PKL)
_hash_lock = threading.Lock()


def _hash_cache():
    try:
        with open(HASH_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _hash_cache_put(key, value):
    with _hash_lock:
        c = _hash_cache()
        c[key] = value
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(HASH_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(c, f)
        except OSError:
            pass


class Cancelled(Exception):
    pass


def verify_cpl(cpl, progress=None, cancel=None):
    """
    Έλεγχος αρχείων του CPL με το PKL (μέγεθος + SHA-1).
    progress(done_bytes, total_bytes, όνομα αρχείου). Επιστρέφει λίστα αποτελεσμάτων.
    """
    pkl = read_pkls(cpl.folder)
    for d in set(cpl.external.values()):  # τα assets του OV ελέγχονται με το PKL του OV
        for k, v in read_pkls(d).items():
            pkl.setdefault(k, v)
    items = [(cpl.id, cpl.path)] + [(aid, f) for aid, f in cpl.assets.items()]
    results, todo = [], []
    for aid, f in items:
        p = pkl.get(aid)
        name = os.path.basename(f) if f else (p["name"] if p else aid)
        r = {"id": aid, "file": f, "name": name, "status": "", "detail": ""}
        results.append(r)
        if not f or not os.path.isfile(f):
            r["status"], r["detail"] = "ΛΕΙΠΕΙ", "το αρχείο δεν υπάρχει"
            continue
        if not p or not p["hash"]:
            r["status"], r["detail"] = "ΧΩΡΙΣ PKL", "δεν υπάρχει στο PKL"
            continue
        size = os.path.getsize(f)
        if p["size"] and size != p["size"]:
            r["status"], r["detail"] = "ΛΑΘΟΣ ΜΕΓΕΘΟΣ", f"{size} αντί {p['size']} bytes"
            continue
        todo.append((r, f, p["hash"], size))
    total = sum(t[3] for t in todo)
    done = 0
    cache = _hash_cache()
    for r, f, want, size in todo:
        key = f"{os.path.abspath(f)}|{size}|{int(os.path.getmtime(f))}"
        got = cache.get(key)
        if got is None:
            h = hashlib.sha1()
            with open(f, "rb") as fh:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    b = fh.read(8 * 1024 * 1024)
                    if not b:
                        break
                    h.update(b)
                    done += len(b)
                    if progress:
                        progress(done, total, r["name"])
            got = base64.b64encode(h.digest()).decode()
            _hash_cache_put(key, got)
        else:
            done += size
            if progress:
                progress(done, total, r["name"])
        if got == want.strip():
            r["status"], r["detail"] = "OK", fmt_size(size)
        else:
            r["status"], r["detail"] = "ΛΑΘΟΣ HASH", "το αρχείο είναι χαλασμένο ή διαφορετικό"
    bad = [r for r in results if r["status"] not in ("OK",)]
    log.info("Hash %s: %d αρχεία, %d προβλήματα %s", cpl.title, len(results), len(bad),
             [(r["name"], r["status"]) for r in bad])
    return results


def hash_summary(results):
    if results is None:
        return "—"
    bad = [r for r in results if r["status"] != "OK"]
    if not bad:
        return "OK"
    if all(r["status"] == "ΧΩΡΙΣ PKL" for r in bad):
        return f"OK ({len(bad)} χωρίς PKL)"
    return f"ΣΦΑΛΜΑ ({len(bad)})"


# ----------------------------------------------------------------- ΣΗΜΕΙΩΣΕΙΣ
def _notes_path(cpl):
    key = cpl.id or hashlib.sha1(os.path.abspath(cpl.path).lower().encode()).hexdigest()
    return os.path.join(NOTES_DIR, f"{key}.json")


def load_notes(cpl):
    try:
        with open(_notes_path(cpl), encoding="utf-8") as f:
            return sorted(json.load(f).get("notes", []), key=lambda n: n["t"])
    except (OSError, ValueError):
        return []


def save_notes(cpl, notes):
    try:
        os.makedirs(NOTES_DIR, exist_ok=True)
        with open(_notes_path(cpl), "w", encoding="utf-8") as f:
            json.dump({"title": cpl.title, "path": cpl.path,
                       "notes": sorted(notes, key=lambda n: n["t"])}, f, ensure_ascii=False, indent=1)
    except OSError:
        log.exception("Δεν σώθηκαν οι σημειώσεις")


# ----------------------------------------------------------------- ΚΑΤΑΣΤΑΣΗ / ΑΝΑΦΟΡΕΣ
def status_of(cpl, info, hash_results, notes):
    """('OK' | 'ΠΡΟΣΟΧΗ' | 'ΣΦΑΛΜΑ', [λόγοι])"""
    errors, warns = [], []
    if cpl.missing:
        errors.append(f"λείπουν {len(cpl.missing)} αρχεία")
    for k in ("pic_err", "snd_err"):
        if info and info.get(k):
            errors.append(("εικόνα: " if k == "pic_err" else "ήχος: ") + info[k])
    if hash_results:
        bad = [r for r in hash_results if r["status"] in ("ΛΑΘΟΣ HASH", "ΛΑΘΟΣ ΜΕΓΕΘΟΣ", "ΛΕΙΠΕΙ")]
        if bad:
            errors.append(f"hash: {len(bad)} αρχεία με πρόβλημα")
        if any(r["status"] == "ΧΩΡΙΣ PKL" for r in hash_results):
            warns.append("αρχεία χωρίς PKL")
    if cpl.encrypted:
        warns.append("κρυπτογραφημένο (θέλει KDM)")
    if not any(r.snd for r in cpl.reels):
        warns.append("χωρίς ήχο")
    if notes:
        warns.append(f"{len(notes)} σημειώσεις")
    if errors:
        return "ΣΦΑΛΜΑ", errors + warns
    if warns:
        return "ΠΡΟΣΟΧΗ", warns
    return "OK", []


def item_row(item):
    """Στοιχεία για πίνακα / CSV / αναφορά."""
    c, info = item["cpl"], item.get("info") or {}
    size = info.get("size")
    subs, _ = track_summary(c)
    st, why = status_of(c, info, item.get("hash"), item.get("notes"))
    return {
        "Κατάσταση": st,
        "Τίτλος": c.title,
        "Είδος": c.kind,
        "Διάρκεια": fmt_time(c.seconds, c.rate),
        "Reels": len(c.reels),
        "fps": f"{float(c.rate):g}",
        "Εικόνα": f"{size[0]}x{size[1]}" if size else "?",
        "3D": "ναι" if any(r.stereo for r in c.reels) else "όχι",
        "Ήχος": f"{info['ch']}ch {info['sr'] // 1000}kHz" if info.get("ch") else "όχι",
        "Υπότιτλοι": subs,
        "Κρυπτογρ.": "ναι" if c.encrypted else "όχι",
        "Αρχεία": f"λείπουν {len(c.missing)}" if c.missing else ("OK (+OV)" if c.external else "OK"),
        "Hash": hash_summary(item.get("hash")),
        "Σημειώσεις": len(item.get("notes") or []),
        "Παρατηρήσεις": "; ".join(why),
        "Φάκελος": c.folder,
    }


def write_csv(items, path):
    rows = [item_row(i) for i in items]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("sep=;\n")  # για να το ανοίγει σωστά το ελληνικό Excel
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["Τίτλος"], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    log.info("CSV: %s (%d ταινίες)", path, len(rows))


_REPORT_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d1d1f;background:#f5f5f7}
h1{font-size:22px;margin:0 0 4px} .sub{color:#666;margin-bottom:20px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #ddd;padding:6px 8px;font-size:13px;text-align:left;vertical-align:top}
th{background:#eee} .OK{color:#137333;font-weight:600} .ΠΡΟΣΟΧΗ{color:#b06000;font-weight:600}
.ΣΦΑΛΜΑ{color:#c5221f;font-weight:600}
.film{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin:20px 0;page-break-inside:avoid}
.film h2{font-size:17px;margin:0 0 10px} .grid{display:flex;gap:16px;flex-wrap:wrap}
.grid img{width:360px;max-width:100%;border:1px solid #ccc} .kv td:first-child{color:#666;width:130px}
.small{font-size:12px;color:#666}
@media (prefers-color-scheme:dark){
 body{background:#17181c;color:#e8e8ec} .sub,.small,.kv td:first-child{color:#9a9aa2}
 table,.film{background:#1f2026} th{background:#2a2b32} th,td,.film{border-color:#3a3c45}
 .grid img{border-color:#3a3c45} .OK{color:#5bd17a} .ΠΡΟΣΟΧΗ{color:#f0a640} .ΣΦΑΛΜΑ{color:#ff6b6b}
}
@media print{body{background:#fff;color:#000;margin:0} table,.film{background:#fff}}
"""


def write_report(items, path):
    """Αναφορά HTML (ανοίγει σε browser, εκτυπώνεται και σε PDF)."""
    esc = html.escape
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    rows = [item_row(i) for i in items]
    counts = {k: sum(1 for r in rows if r["Κατάσταση"] == k) for k in ("OK", "ΠΡΟΣΟΧΗ", "ΣΦΑΛΜΑ")}
    out = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>Αναφορά ελέγχου DCP</title>"
           f"<style>{_REPORT_CSS}</style></head><body>",
           f"<h1>Αναφορά ελέγχου DCP</h1><div class='sub'>{now} · {len(rows)} ταινίες · "
           f"<span class='OK'>{counts['OK']} OK</span> · <span class='ΠΡΟΣΟΧΗ'>{counts['ΠΡΟΣΟΧΗ']} προσοχή</span> · "
           f"<span class='ΣΦΑΛΜΑ'>{counts['ΣΦΑΛΜΑ']} σφάλμα</span></div>"]
    cols = ["Κατάσταση", "Τίτλος", "Διάρκεια", "Εικόνα", "Ήχος", "Υπότιτλοι", "Αρχεία", "Hash", "Σημειώσεις"]
    out.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
    for r in rows:
        out.append("<tr>" + "".join(
            f"<td class='{esc(str(r[c]))}'>{esc(str(r[c]))}</td>" if c == "Κατάσταση" else f"<td>{esc(str(r[c]))}</td>"
            for c in cols) + "</tr>")
    out.append("</table>")
    for item, r in zip(items, rows):
        c = item["cpl"]
        out.append(f"<div class='film'><h2><span class='{r['Κατάσταση']}'>[{r['Κατάσταση']}]</span> {esc(c.title)}</h2>"
                   "<div class='grid'>")
        png = item.get("thumb")
        if png:
            out.append(f"<img src='data:image/png;base64,{base64.b64encode(png).decode()}'>")
        out.append("<table class='kv'>")
        for k in ("Είδος", "Διάρκεια", "Reels", "fps", "Εικόνα", "3D", "Ήχος", "Υπότιτλοι", "Κρυπτογρ.",
                  "Αρχεία", "Hash", "Παρατηρήσεις", "Φάκελος"):
            out.append(f"<tr><td>{k}</td><td>{esc(str(r[k]))}</td></tr>")
        out.append("</table></div>")
        if c.missing:
            out.append("<p><b>Λείπουν:</b><br>" + "<br>".join(esc(m) for m in c.missing) + "</p>")
        hr = item.get("hash")
        if hr:
            out.append("<p><b>Έλεγχος hash</b></p><table><tr><th>Αρχείο</th><th>Αποτέλεσμα</th><th>Λεπτομέρειες</th></tr>")
            for h in hr:
                cls = "OK" if h["status"] == "OK" else ("ΠΡΟΣΟΧΗ" if h["status"] == "ΧΩΡΙΣ PKL" else "ΣΦΑΛΜΑ")
                out.append(f"<tr><td>{esc(h['name'])}</td><td class='{cls}'>{esc(h['status'])}</td>"
                           f"<td>{esc(h['detail'])}</td></tr>")
            out.append("</table>")
        notes = item.get("notes")
        if notes:
            out.append("<p><b>Σημειώσεις</b></p><table><tr><th>Χρόνος</th><th>Σημείωση</th></tr>")
            for n in notes:
                out.append(f"<tr><td>{fmt_time(n['t'], c.rate)}</td><td>{esc(n['text'])}</td></tr>")
            out.append("</table>")
        out.append("</div>")
    out.append(f"<p class='small'>{APP_NAME} v{APP_VERSION}</p></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    log.info("Αναφορά: %s (%d ταινίες)", path, len(rows))
