"""
Σύγκριση δύο εκδόσεων ενός DCP (π.χ. ο διανομέας έστειλε νέα έκδοση): στοιχεία CPL, reels, εικόνα, ήχος,
υπότιτλοι. Τα αρχεία συγκρίνονται με το SHA-1 του PKL, χωρίς να ξαναδιαβαστούν.
"""
import os

from app.checks.dcnc import cpl_standard
from app.core.dcp import cpl_pkl
from app.core.util import fmt_size, fmt_time

SAME, DIFF, INFO = "same", "diff", "info"


def _asset_ids(cpl):
    """αρχείο -> id asset (τα reels κρατούν διαδρομές)."""
    return {os.path.normcase(f): aid for aid, f in cpl.assets.items() if f}


def _file_key(path, pkl, ids):
    """Ταυτότητα αρχείου για σύγκριση: SHA-1 του PKL, αλλιώς μέγεθος + όνομα."""
    if not path:
        return None, "—"
    aid = ids.get(os.path.normcase(path))
    p = pkl.get(aid) or {}
    size = p.get("size") or (os.path.getsize(path) if os.path.isfile(path) else 0)
    label = f"{os.path.basename(path)} ({fmt_size(size)})"
    return (p.get("hash") or f"{size}:{os.path.basename(path)}"), label


class _Side:
    """Ό,τι χρειάζεται από ένα CPL για τη σύγκριση."""

    def __init__(self, cpl, info=None, subs=None):
        self.cpl, self.info, self.subs = cpl, info or {}, subs
        self.pkl = {} if cpl.is_file else cpl_pkl(cpl)
        self.ids = _asset_ids(cpl)

    def file(self, path):
        return _file_key(path, self.pkl, self.ids)


def _picture(info):
    if "size" not in info:
        return "?"
    w, h = info["size"]
    return f"{w}×{h} {info.get('codec', '')}".strip()


def _sound(info):
    return f"{info['ch']} κανάλια, {info.get('sr', 0) // 1000} kHz" if "ch" in info else "—"


def compare_cpls(a, b, info_a=None, info_b=None, subs_a=None, subs_b=None):
    """
    [(στοιχείο, τιμή A, τιμή B, same|diff|info)]. info_*: αποτέλεσμα του media.probe (προαιρετικό),
    subs_*: περίληψη υποτίτλων (προαιρετική).
    """
    A, B = _Side(a, info_a, subs_a), _Side(b, info_b, subs_b)
    rows = []

    def row(name, va, vb, kind=None):
        rows.append((name, str(va), str(vb), kind or (SAME if va == vb else DIFF)))

    row("Τίτλος (CPL)", a.title, b.title)
    row("Είδος", a.kind, b.kind)
    row("Πρότυπο", cpl_standard(a), cpl_standard(b))
    row("Ίδιο CPL (Id)", a.id or "—", b.id or "—")
    row("Διάρκεια", fmt_time(a.seconds, a.rate), fmt_time(b.seconds, b.rate))
    row("fps", f"{float(a.rate):g}", f"{float(b.rate):g}")
    row("Κρυπτογραφημένο", "ναι" if a.encrypted else "όχι", "ναι" if b.encrypted else "όχι")
    if A.info or B.info:
        row("Εικόνα", _picture(A.info), _picture(B.info))
        row("Ήχος", _sound(A.info), _sound(B.info))
    if subs_a is not None or subs_b is not None:
        row("Υπότιτλοι", subs_a or "όχι", subs_b or "όχι")
    row("Reels", len(a.reels), len(b.reels))
    for i in range(max(len(a.reels), len(b.reels))):
        rows.extend(_reel_rows(i, A, B))
    return rows


def _reel_rows(i, A, B):
    ra = A.cpl.reels[i] if i < len(A.cpl.reels) else None
    rb = B.cpl.reels[i] if i < len(B.cpl.reels) else None
    n = f"Reel {i + 1}"
    out = [(f"{n}: διάρκεια", fmt_time(ra.seconds, ra.rate) if ra else "—",
            fmt_time(rb.seconds, rb.rate) if rb else "—",
            SAME if ra and rb and ra.dur == rb.dur and ra.rate == rb.rate else DIFF)]
    for label, get in (("εικόνα", lambda r: r.pic), ("ήχος", lambda r: r.snd)):
        ka, la = A.file(get(ra) if ra else None)
        kb, lb = B.file(get(rb) if rb else None)
        out.append((f"{n}: {label}", la, lb, SAME if ka == kb else DIFF))
    subs_a = [A.file(s[1]) for s in (ra.subs if ra else [])]
    subs_b = [B.file(s[1]) for s in (rb.subs if rb else [])]
    if subs_a or subs_b:
        out.append((f"{n}: υπότιτλοι", ", ".join(l for _k, l in subs_a) or "—", ", ".join(l for _k, l in subs_b) or "—",
                    SAME if sorted(k for k, _l in subs_a) == sorted(k for k, _l in subs_b) else DIFF))
    if ra and rb and (ra.pic_entry != rb.pic_entry or ra.snd_entry != rb.snd_entry):
        out.append((f"{n}: σημείο έναρξης (EntryPoint)", f"εικόνα {ra.pic_entry}, ήχος {ra.snd_entry}",
                    f"εικόνα {rb.pic_entry}, ήχος {rb.snd_entry}", DIFF))
    return out


def summary(rows):
    """Μία πρόταση: πόσες διαφορές και σε τι."""
    diffs = [r[0] for r in rows if r[3] == DIFF and r[0] not in ("Τίτλος (CPL)", "Ίδιο CPL (Id)")]
    if not diffs:
        return "Ίδιο περιεχόμενο: εικόνα, ήχος και υπότιτλοι ταυτίζονται."
    return f"{len(diffs)} διαφορές: " + ", ".join(diffs[:6]) + (" …" if len(diffs) > 6 else "")
