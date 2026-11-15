"""Στατιστικά φεστιβάλ από τη λίστα ταινιών: πλήθος, ώρες, GB, ανάλυση, ήχος, γλώσσες υποτίτλων, προβλήματα."""
import os
from collections import Counter

from app.checks.dcnc import cpl_standard, parse_dcnc
from app.core.util import fmt_size, fmt_time

CONTENT_KINDS = {"feature": "FTR", "short": "SHR", "trailer": "TLR", "teaser": "TSR", "advertisement": "ADV",
                 "psa": "PSA", "transitional": "XSN", "test": "TST", "rating": "RTG", "policy": "POL"}
KINDS = {"FTR": "μεγάλου μήκους", "SHR": "μικρού μήκους", "TLR": "τρέιλερ", "TST": "test", "ADV": "διαφήμιση",
         "PSA": "ανακοίνωση", "XSN": "ident", "RTG": "rating"}


def _bytes(cpl):
    """Μέγεθος στον δίσκο: τα αρχεία του CPL (κάθε αρχείο μία φορά)."""
    files = {os.path.normcase(f) for f in cpl.assets.values() if f} | {os.path.normcase(cpl.path)}
    return sum(os.path.getsize(f) for f in files if os.path.isfile(f))


def _resolution(info, name):
    size = (info or {}).get("size")
    if size:
        return "4K" if size[0] >= 3800 else ("2K" if size[0] >= 1900 else f"{size[0]}×{size[1]}")
    return name.get("res") or "?"


def _audio(info, name):
    ch = (info or {}).get("ch")
    if ch:
        return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch} κανάλια")
    return {"51": "5.1", "71": "7.1", "20": "stereo", "10": "mono"}.get(name.get("audio"), "χωρίς ήχο / ;")


def _sub_langs(name, has_subs):
    lang = (name.get("sub_lang") or "").upper()
    if lang and lang != "XX":
        return lang
    return "υπότιτλοι (χωρίς γλώσσα στο όνομα)" if has_subs else "χωρίς υπότιτλους"


def _count_line(counter):
    return ", ".join(f"{k}: {v}" for k, v in counter.most_common())


def festival_stats(items, status_of=None):
    """
    [(μέτρηση, τιμή)]. items: τα στοιχεία της λίστας (cpl, info, hash, scan...).
    status_of(item) -> 'OK' | 'ΠΡΟΣΟΧΗ' | 'ΣΦΑΛΜΑ' (προαιρετικό).
    """
    dcps = [it for it in items if not it["cpl"].is_file]
    seconds = sum(it["cpl"].seconds for it in dcps)
    size = sum(_bytes(it["cpl"]) for it in dcps)
    kinds, res, audio, std, subs, status = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    by_kind_time = Counter()
    for it in dcps:
        c = it["cpl"]
        name = parse_dcnc(c.title) or {}
        kind = (name.get("kind") or CONTENT_KINDS.get((c.kind or "").lower()) or c.kind or "?").upper()
        label = KINDS.get(kind, kind.lower())
        kinds[label] += 1
        by_kind_time[label] += c.seconds
        res[_resolution(it.get("info"), name) + (" 3D" if any(r.stereo for r in c.reels) else "")] += 1
        audio[_audio(it.get("info"), name)] += 1
        std[cpl_standard(c) or "?"] += 1
        subs[_sub_langs(name, any(r.subs for r in c.reels))] += 1
        if status_of:
            status[status_of(it)] += 1
    rows = [("Ταινίες (DCP)", str(len(dcps))),
            ("Αρχεία βίντεο (όχι DCP)", str(len(items) - len(dcps))),
            ("Συνολική διάρκεια", fmt_time(seconds)),
            ("Μέγεθος στον δίσκο", fmt_size(size)),
            ("Είδος", _count_line(kinds)),
            ("Διάρκεια ανά είδος", ", ".join(f"{k}: {fmt_time(v)}" for k, v in by_kind_time.most_common())),
            ("Ανάλυση", _count_line(res)),
            ("Ήχος", _count_line(audio)),
            ("Πρότυπο", _count_line(std)),
            ("Υπότιτλοι", _count_line(subs)),
            ("Κρυπτογραφημένα (θέλουν KDM)", str(sum(it["cpl"].encrypted for it in dcps))),
            ("Με αρχεία που λείπουν", str(sum(bool(it["cpl"].missing) for it in dcps))),
            ("Ελέγχθηκε hash", f"{sum(bool(it.get('hash')) for it in dcps)} από {len(dcps)}"),
            ("Σαρώθηκαν", f"{sum(bool(it.get('scan')) for it in dcps)} από {len(dcps)}")]
    if status:
        rows.append(("Κατάσταση", _count_line(status)))
    if dcps:
        longest = max(dcps, key=lambda it: it["cpl"].seconds)["cpl"]
        rows.append(("Μεγαλύτερη", f"{longest.title} ({fmt_time(longest.seconds)})"))
    return rows
