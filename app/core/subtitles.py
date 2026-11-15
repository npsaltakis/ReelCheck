"""Υπότιτλοι: Interop XML, SMPTE MXF, εικόνες PNG, SRT."""
import os
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from app.core.config import log
from app.core.dcp import load_dcp
from app.core.util import find_all, local, norm_id, read_text, text


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


def _attr(el, *names, default=None):
    """Η πρώτη ιδιότητα που υπάρχει (τα DCP γράφουν π.χ. και VAlign και Valign)."""
    for n in names:
        if el.get(n) is not None:
            return el.get(n)
    return default


def _italic_of(el, italic):
    if local(el.tag) == "Font" and el.get("Italic") is not None:
        return _truthy(el.get("Italic"))
    return italic


def _text_runs(el, italic, cur):
    """Κομμάτια κειμένου [(κείμενο, πλάγια)] ενός <Text>, μαζί με τα <Font> και <Space> μέσα του."""
    italic = _italic_of(el, italic)
    if el.text:
        cur.append((el.text, italic))
    for c in el:
        if local(c.tag) == "Space":
            cur.append((" ", italic))
        else:
            _text_runs(c, italic, cur)
        if c.tail:
            cur.append((c.tail, italic))
    return cur


def _position(el):
    """(valign, vpos, halign, hpos, y) όπου y = κάθετη θέση 0-100 από πάνω."""
    valign = _attr(el, "VAlign", "Valign", default="bottom").lower()
    vpos = float(_attr(el, "VPosition", "Vposition", default="0") or 0)
    halign = _attr(el, "HAlign", "Halign", default="center").lower()
    hpos = float(_attr(el, "HPosition", "Hposition", default="0") or 0)
    y = vpos if valign == "top" else (50 + vpos if valign == "center" else 100 - vpos)
    return valign, vpos, halign, hpos, y


class _DcpSubtitles:
    """Υπότιτλοι ενός αρχείου DCP (Interop XML ή SMPTE MXF), κείμενο και εικόνες."""

    def __init__(self, path, data, root):
        self.root, self.folder = root, os.path.dirname(path)
        if local(root.tag) == "DCSubtitle":  # Interop: ο χρόνος σε ticks των 4 ms
            self.tick = 250
        else:
            r = (text(root, "TimeCodeRate") or text(root, "EditRate") or "24").split()
            self.tick = Fraction(int(r[0]), int(r[1]) if len(r) > 1 else 1)
        # εικόνες: Interop = αρχεία PNG δίπλα, SMPTE = μέσα στο MXF (αντιστοίχιση με σειρά εμφάνισης)
        self.img_ids = []
        for im in find_all(root, "Image"):
            k = (im.text or "").strip()
            if k and k not in self.img_ids:
                self.img_ids.append(k)
        self.blobs = _png_blobs(data) if path.lower().endswith(".mxf") and self.img_ids else []
        self.missing_imgs = 0
        self.entries = []

    def png_for(self, ref):
        ref = ref.strip()
        if self.blobs and ref in self.img_ids and self.img_ids.index(ref) < len(self.blobs):
            return self.blobs[self.img_ids.index(ref)]
        for cand in (ref, norm_id(ref) + ".png", ref + ".png"):
            p = os.path.join(self.folder, cand)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return None

    def _item(self, el, italic):
        """Ένα <Text> ή <Image> σε item, ή None (κενό κείμενο / εικόνα που λείπει)."""
        valign, vpos, halign, hpos, y = _position(el)
        if local(el.tag) == "Image":
            png = self.png_for(el.text or "")
            if png is None:
                self.missing_imgs += 1
                return None
            return {"type": "image", "png": png, "valign": valign, "vpos": vpos, "halign": halign, "hpos": hpos,
                    "y": y}
        runs = _text_runs(el, italic, [])
        line = "".join(t for t, _ in runs).strip()
        if not line:
            return None
        return {"type": "text", "text": line, "y": y, "italic": any(i for t, i in runs if t.strip())}

    def _subtitle(self, el, italic, offset):
        t_in = _sub_time(el.get("TimeIn", "0:0:0:0"), self.tick)
        t_out = _sub_time(el.get("TimeOut", "0:0:0:0"), self.tick)
        items = [self._item(tx, italic) for tx in el.iter() if local(tx.tag) in ("Text", "Image")]
        items = sorted((i for i in items if i), key=lambda r: r["y"])
        if items:
            self.entries.append((offset + t_in, offset + t_out, items))

    def visit(self, el, italic, offset):
        italic = _italic_of(el, italic)
        if local(el.tag) == "Subtitle":
            self._subtitle(el, italic, offset)
            return
        for c in el:
            self.visit(c, italic, offset)


def parse_subtitle_file(path, offset):
    """
    Επιστρέφει (entries, γλώσσα, σημείωση). entry = (αρχή, τέλος, items) σε χρόνο ταινίας.
    item: {"type": "text", "text", "italic", "y"} ή {"type": "image", "png", "valign", "vpos", "halign", "hpos"}
    """
    with open(path, "rb") as f:
        data = f.read()
    xmlb = _sub_xml_bytes(data, path.lower().endswith(".mxf"))
    if not xmlb:
        return [], None, "δεν βρέθηκε XML υποτίτλων"
    root = ET.fromstring(xmlb)
    subs = _DcpSubtitles(path, data, root)
    subs.visit(root, False, offset)
    subs.entries.sort(key=lambda s: s[0])
    note = f"{subs.missing_imgs} εικόνες υποτίτλων δεν βρέθηκαν" if subs.missing_imgs else None
    return subs.entries, text(root, "Language"), note


def parse_srt(path):
    txt = read_text(path)
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
        tracks.append({"name": label_prefix + name, "kind": kind, "entries": g["entries"], "notes": g["notes"],
                       "langs": g["langs"]})
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
