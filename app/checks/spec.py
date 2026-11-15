"""Προδιαγραφές φεστιβάλ και έλεγχος ταινίας με αυτές."""
from app.checks.dcnc import cpl_standard
from app.checks.langs import norm_lang, track_langs
from app.core.config import get_setting, set_setting
from app.core.related import sibling_cpls


# ======================================================================== ΠΡΟΔΙΑΓΡΑΦΕΣ ΦΕΣΤΙΒΑΛ
DEFAULT_SPEC = {"enabled": False, "res": ["2K", "4K"], "fps": [24, 25], "std": ["SMPTE", "IOP"],
                "audio": "any", "sub_lang": "", "no_encrypted": False, "no_3d": False, "max_minutes": 0}


AUDIO_CHOICES = {"any": "οποιοσδήποτε", "stereo": "τουλάχιστον στέρεο", "51": "τουλάχιστον 5.1"}


def get_spec():
    s = dict(DEFAULT_SPEC)
    s.update(get_setting("festival_spec", {}) or {})
    return s


def set_spec(spec):
    set_setting("festival_spec", spec)


def _sibling_has_lang(cpl, lang):
    if getattr(cpl, "_siblings", None) is None:
        try:
            cpl._siblings = sibling_cpls(cpl)
        except Exception:
            cpl._siblings = []
    return any(lang in track_langs(o) for o in cpl._siblings)


def spec_issues(item, spec):
    if not spec.get("enabled"):
        return []
    c, info = item["cpl"], item.get("info") or {}
    if c.is_file:
        return []
    out = []
    size = info.get("size")
    if size and spec.get("res"):
        real = "2K" if size[0] <= 2048 else "4K"
        if real not in spec["res"]:
            out.append(("warn", f"φεστιβάλ: δεν δέχεται {real}"))
    fps = float(c.rate)
    if spec.get("fps") and not any(abs(fps - f) < 0.01 for f in spec["fps"]):
        out.append(("warn", f"φεστιβάλ: δεν δέχεται {fps:g} fps"))
    std = cpl_standard(c)
    if std and spec.get("std") and std not in spec["std"]:
        out.append(("warn", f"φεστιβάλ: δεν δέχεται {std}"))
    ch = info.get("ch")
    if spec.get("audio") in ("stereo", "51") and info and "pic_err" not in info:
        need = 2 if spec["audio"] == "stereo" else 6
        if not any(r.snd for r in c.reels):
            out.append(("warn", "φεστιβάλ: θέλει ήχο"))
        elif ch and ch < need:
            out.append(("warn", f"φεστιβάλ: θέλει {'στέρεο' if need == 2 else '5.1'}, έχει {ch} κανάλια"))
    lang = norm_lang(spec.get("sub_lang"))
    if lang and lang not in track_langs(c):
        if _sibling_has_lang(c, lang):
            out.append(("info", f"φεστιβάλ: υπότιτλοι {lang.upper()} υπάρχουν σε άλλο CPL (VF)"))
        else:
            out.append(("warn", f"φεστιβάλ: χωρίς υπότιτλους {lang.upper()}"))
    if spec.get("no_encrypted") and c.encrypted:
        out.append(("warn", "φεστιβάλ: δεν δέχεται κρυπτογραφημένα"))
    if spec.get("no_3d") and any(r.stereo for r in c.reels):
        out.append(("warn", "φεστιβάλ: δεν δέχεται 3D"))
    mx = float(spec.get("max_minutes") or 0)
    if mx and c.seconds > mx * 60:
        out.append(("warn", f"φεστιβάλ: διάρκεια πάνω από {mx:g} λεπτά"))
    return out
