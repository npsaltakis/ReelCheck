"""
Γλώσσα περιβάλλοντος: ελληνικά (ο κώδικας) ή αγγλικά (μετάφραση την ώρα που εμφανίζεται ένα κείμενο).

Τα κείμενα του προγράμματος γράφονται στα ελληνικά. Στα αγγλικά, η tr() μεταφράζει μόνο γνωστές φράσεις
(app/core/lang_en.py): ακριβή κείμενα και πρότυπα με μεταβλητές («λείπουν {} αρχεία»). Σύνθετα μηνύματα
σπάνε σε γραμμές, «Ετικέτα: τιμή», διαχωριστικά (· ; |) και παρενθέσεις. Ό,τι δεν αναγνωρίζεται (ονόματα
ταινιών, διαδρομές) μένει όπως είναι.
"""
import re
from functools import lru_cache

from app.core.config import get_setting

GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
LANGS = {"auto": "Αυτόματα (γλώσσα των Windows)", "el": "Ελληνικά", "en": "English"}
_state = {"lang": None}


def system_language():
    """'el' αν τα Windows είναι στα ελληνικά, αλλιώς 'en'."""
    try:
        import ctypes
        return "el" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x08 else "en"
    except Exception:
        return "el"


def language():
    if _state["lang"] is None:
        choice = get_setting("language", "el")  # προεπιλογή ελληνικά: όσοι το έχουν ήδη δεν βλέπουν ξαφνική αλλαγή
        _state["lang"] = system_language() if choice not in ("el", "en") else choice
    return _state["lang"]


def set_language(lang):
    """Για δοκιμές· στο πρόγραμμα η αλλαγή γίνεται από τις ρυθμίσεις με επανεκκίνηση."""
    _state["lang"] = lang
    _translate.cache_clear()


def _compile_templates(pairs):
    """Πρώτα τα πιο συγκεκριμένα (με περισσότερο σταθερό κείμενο), ώστε π.χ. «λείπουν {} αρχεία» πριν από «λείπουν {}»."""
    out = []
    for greek, english in sorted(pairs, key=lambda p: -len(p[0].replace("{}", ""))):
        pieces = greek.split("{}")
        pattern = "(.+?)".join(re.escape(p) for p in pieces)
        out.append((re.compile(pattern + r"\Z", re.S), english))
    return out


_tables = {}


def _load():
    if not _tables:
        from app.core import lang_en
        _tables["exact"] = lang_en.EXACT
        _tables["templates"] = _compile_templates(lang_en.TEMPLATES)
    return _tables


def tr(text):
    """Μετάφραση για εμφάνιση (στα ελληνικά επιστρέφει το ίδιο κείμενο)."""
    if not isinstance(text, str) or language() == "el" or not GREEK.search(text):
        return text
    return _translate(text)


_LEAD = re.compile(r"(\s*)(.*?)(\s*)\Z", re.S)
_PREFIX = re.compile(r"([✖⚠ℹ▶▲▼•·#–\-]+\s*)(.+)\Z", re.S)
_LABEL = re.compile(r"([^\s:][^:\n]{0,40}):(\s+)(.+)\Z", re.S)
_WIDE = re.compile(r"(\s{2,})")
_LIST_SEPS = ("; ", " · ")


def _outer_paren(text):
    """«κείμενο  (μέσα (και μέσα))» -> ('κείμενο  ', 'μέσα (και μέσα)'): η εξωτερική παρένθεση στο τέλος."""
    if not text.endswith(")"):
        return None
    for pos, ch in enumerate(text):
        if ch == "(" and pos > 0 and text[pos - 1] == " ":
            depth = 0
            for k, c in enumerate(text[pos:-1]):
                depth += (c == "(") - (c == ")")
                if depth == 0 and k > 0:
                    break
            else:
                if depth == 1:
                    return text[:pos], text[pos + 1:-1]
    return None


@lru_cache(maxsize=20000)
def _translate(text, depth=0):
    t = _load()
    exact = t["exact"]
    if text in exact:
        return exact[text]
    if depth > 6:
        return text
    sub = lambda s: _translate(s, depth + 1) if GREEK.search(s) else s
    if "\n" in text:
        return "\n".join(sub(line) for line in text.split("\n"))
    lead, core, trail = _LEAD.match(text).groups()
    if lead or trail:
        return lead + sub(core) + trail
    m = _PREFIX.match(text)
    if m:
        return m.group(1) + sub(m.group(2))
    for rx, english in t["templates"]:
        m = rx.match(text)
        # μια μεταβλητή δεν «καταπίνει» λίστα (« ; » / « · »): αυτή σπάει πιο κάτω σε κομμάτια
        if m and not any(sep in g for g in m.groups() for sep in _LIST_SEPS):
            return english.format(*(sub(g) for g in m.groups()))
    m = _LABEL.match(text)
    if m and GREEK.search(m.group(1)) and (m.group(1) + ":") in exact:
        label = exact[m.group(1) + ":"]
        pad = max(1, len(m.group(1)) + 1 + len(m.group(2)) - len(label))  # κρατά τη στοίχιση
        return label + " " * pad + sub(m.group(3))
    for sep in (" · ", "; ", " | ", " – "):  # λίστα: μόνο διαχωριστικά έξω από παρενθέσεις
        parts = _split_top(text, sep)
        if len(parts) > 1:
            return sep.join(sub(p) for p in parts)
    split = _outer_paren(text)
    if split:
        head, inner = split
        return f"{sub(head.rstrip())}{head[len(head.rstrip()):]}({sub(inner)})"
    if _WIDE.search(text):
        return "".join(p if _WIDE.fullmatch(p) else sub(p) for p in _WIDE.split(text))
    for sep in (", ", ": "):  # «αρχείο: μήνυμα» → μεταφράζεται ό,τι αναγνωρίζεται, τα υπόλοιπα μένουν
        if sep in text:
            parts = text.split(sep) if sep == ", " else text.split(sep, 1)
            out = [sub(p) for p in parts]
            if out != parts:
                return sep.join(out)
    return text


def _split_top(text, sep):
    """Χωρισμός στο sep μόνο σε βάθος 0 (όχι μέσα σε παρενθέσεις)."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return parts


def tr_html(html_text):
    """Μετάφραση του κειμένου ανάμεσα στις ετικέτες HTML (για αναφορές)."""
    if language() == "el":
        return html_text
    html_text = html_text.replace("<html lang='el'>", "<html lang='en'>")
    return re.sub(r">([^<>]*[Ͱ-Ͽ][^<>]*)<", lambda m: ">" + tr(m.group(1)) + "<", html_text)
