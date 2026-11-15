"""Ανάγνωση DCP: ASSETMAP, CPL, PKL, κανονικά αρχεία βίντεο."""
import os
from fractions import Fraction

from app.core.config import log
from app.core.util import child, find_all, local, norm_id, text, xml_root


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
    folder = os.path.abspath(folder)
    parent = os.path.dirname(folder)
    out = {}
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return out
    for n in names:
        d = os.path.join(parent, n)
        if os.path.normcase(d) == os.path.normcase(folder) or not os.path.isdir(d):
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
