"""Μικρά βοηθητικά: μορφοποίηση χρόνου/μεγέθους και XML."""
import xml.etree.ElementTree as ET

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
