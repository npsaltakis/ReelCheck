"""Κατάλογος ταινιών φεστιβάλ (CSV) και αντιστοίχιση με τα DCP."""
import csv
import difflib
import re
import unicodedata

from app.checks.dcnc import parse_dcnc


# ======================================================================== ΚΑΤΑΛΟΓΟΣ ΦΕΣΤΙΒΑΛ
_GREEK = dict(zip("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ",
                  ["A", "V", "G", "D", "E", "Z", "I", "TH", "I", "K", "L", "M", "N", "X", "O", "P", "R", "S", "T",
                   "Y", "F", "CH", "PS", "O"]))


def norm_title(s):
    s = unicodedata.normalize("NFD", (s or "").upper())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = "".join(_GREEK.get(ch, ch) for ch in s)
    return re.sub(r"[^A-Z0-9]", "", s)


def read_catalog(path):
    """[{"title", "row"}] από CSV (ή TXT με έναν τίτλο ανά γραμμή)."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1253", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = [l for l in txt.splitlines() if l.strip() and not l.lower().startswith("sep=")]
    if not lines:
        return []
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=";,\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if ";" in lines[0] else ("," if "," in lines[0] else "\t")
    rows = list(csv.reader(lines, delimiter=delim))
    head = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(head) if re.search(r"τίτλ|τιτλ|title|ταινί|ταινι|film|movie", h)), None)
    if col is None:
        col, body = 0, rows  # χωρίς επικεφαλίδα
    else:
        body = rows[1:]
    out = []
    for r in body:
        if len(r) > col and r[col].strip():
            out.append({"title": r[col].strip(), "row": r})
    return out


def _match(cat_title, cpl):
    a = norm_title(cat_title)
    if len(a) < 2:
        return 0.0
    tok = norm_title(parse_dcnc(cpl.title).get("title") or cpl.title)
    full = norm_title(cpl.title)
    if a == tok or (len(a) >= 4 and a in full):
        return 1.0
    if len(tok) >= 6 and a.startswith(tok):  # DCNC: τίτλος κομμένος στους 14 χαρακτήρες
        return 0.95
    return difflib.SequenceMatcher(None, a, tok).ratio()


def match_catalog(catalog, items):
    """[(τίτλος καταλόγου, [δείκτες items])] και λίστα δεικτών items εκτός καταλόγου."""
    rows, used = [], set()
    for e in catalog:
        hits = [i for i, it in enumerate(items) if _match(e["title"], it["cpl"]) >= 0.8]
        used.update(hits)
        rows.append((e["title"], hits))
    extra = [i for i in range(len(items)) if i not in used]
    return rows, extra


def catalog_table(catalog, items, status_of_item):
    """
    Πίνακας αποτελέσματος: [(τίτλος καταλόγου, DCP, κατάσταση)] και σύνοψη.
    status_of_item(i) -> 'OK' | 'ΠΡΟΣΟΧΗ' | 'ΣΦΑΛΜΑ'.
    """
    rows, extra = match_catalog(catalog, items)
    table = []
    for title, hits in rows:
        if not hits:
            table.append((title, "—", "ΛΕΙΠΕΙ"))
            continue
        sts = [status_of_item(i) for i in hits]
        best = "OK" if "OK" in sts else ("ΠΡΟΣΟΧΗ" if "ΠΡΟΣΟΧΗ" in sts else "ΣΦΑΛΜΑ")
        table.append((title, " | ".join(items[i]["cpl"].title for i in hits), best))
    for i in extra:
        table.append(("(εκτός καταλόγου)", items[i]["cpl"].title, status_of_item(i)))
    found = table[:len(rows)]
    summary = (f"{len(rows)} ταινίες στον κατάλογο · {sum(r[2] == 'OK' for r in found)} OK · "
               f"{sum(r[2] == 'ΛΕΙΠΕΙ' for r in found)} λείπουν · {len(extra)} CPL εκτός καταλόγου")
    return table, summary
