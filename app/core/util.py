"""
Μικρά βοηθητικά που χρησιμοποιούνται παντού: μορφοποίηση χρόνου/μεγέθους, XML, αρχεία κειμένου με ελληνική
κωδικοποίηση, CSV για το ελληνικό Excel, SHA-1 αρχείου, φάκελοι DCP, μεταγραφή ελληνικών σε λατινικούς.
"""
import base64
import csv
import hashlib
import os
import re
import unicodedata
import xml.etree.ElementTree as ET

TEXT_ENCODINGS = ("utf-8-sig", "cp1253", "latin-1")  # UTF-8, ελληνικά Windows, και ό,τι άλλο
HASH_CHUNK = 8 * 1024 * 1024


class Cancelled(Exception):
    """Η εργασία ακυρώθηκε από τον χρήστη (Ακύρωση)."""


def check_cancel(cancel):
    """Πετά Cancelled αν πατήθηκε Ακύρωση (cancel: threading.Event ή None)."""
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# ---------------------------------------------------------------- μορφοποίηση
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


# ---------------------------------------------------------------- XML
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


# ---------------------------------------------------------------- αρχεία
def read_text(path):
    """Αρχείο κειμένου (π.χ. .srt, .csv) σε UTF-8 ή ελληνικά Windows (cp1253)."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def write_excel_csv(path, header, rows):
    """CSV που ανοίγει σωστά στο ελληνικό Excel (UTF-8 με BOM, «;», και γραμμή sep=;)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("sep=;\n")
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def sha1_file(path, on_chunk=None, cancel=None, sink=None):
    """
    SHA-1 (base64, όπως στο PKL) ενός αρχείου, σε κομμάτια.
    on_chunk(bytes): για πρόοδο· cancel: threading.Event (πετά Cancelled)· sink: αρχείο όπου γράφονται
    τα ίδια bytes (αντιγραφή και hash με μία ανάγνωση).
    """
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            check_cancel(cancel)
            b = fh.read(HASH_CHUNK)
            if not b:
                break
            h.update(b)
            if sink is not None:
                sink.write(b)
            if on_chunk:
                on_chunk(b)
    return base64.b64encode(h.digest()).decode()


def is_dcp_folder(folder):
    """Φάκελος με ASSETMAP (δηλαδή DCP)."""
    try:
        return os.path.isdir(folder) and any(f.upper().startswith("ASSETMAP") for f in os.listdir(folder))
    except OSError:
        return False


# ---------------------------------------------------------------- ελληνικά → λατινικά
GREEK_LATIN = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i",
               "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
               "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}


def latin(s):
    """Ελληνικά σε λατινικούς χαρακτήρες (τα ονόματα DCP δέχονται μόνο λατινικά): Το Ταξίδι -> To Taxidi."""
    plain = "".join(ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn")
    plain = plain.replace("ΟΥ", "OU").replace("Ου", "Ou").replace("ου", "ou")
    out = []
    for ch in plain:
        low = ch.lower()
        rep = GREEK_LATIN.get(low)
        if rep is None:
            out.append(ch)
        elif ch != low:
            out.append(rep.upper() if plain.isupper() else rep.capitalize())
        else:
            out.append(rep)
    s = "".join(out).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()
