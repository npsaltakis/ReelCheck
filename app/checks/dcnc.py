"""Όνομα DCP κατά Digital Cinema Naming Convention και σύγκριση με το περιεχόμενο."""
import re

from app.checks.langs import norm_lang, track_langs
from app.core.util import xml_root


# ======================================================================== ΟΝΟΜΑ DCNC
_KINDS = ("FTR", "TLR", "TSR", "PRO", "TST", "RTG", "SHR", "ADV", "XSN", "PSA", "POL", "CLP", "PRV", "EPS", "SPL",
          "PRM", "TRL")


_FPS_VALUES = {25, 30, 48, 50, 60, 96, 120}


def parse_dcnc(name):
    """
    Όνομα κατά Digital Cinema Naming Convention, π.χ.
    LACHTARA_FTR-25_S-239_PL-XX_INT-NR_51_2K_MWM_20260721_BOM_SMPTE_OV
    Επιστρέφει {} αν δεν μοιάζει με DCNC.
    """
    parts = [p for p in (name or "").strip().split("_") if p]
    if len(parts) < 4:
        return {}
    d = {"title": parts[0]}
    for p in parts[1:]:
        P = p.upper()
        bits = P.split("-")
        if "kind" not in d and bits[0] in _KINDS:
            d["kind"] = bits[0]
            for b in bits[1:]:
                if b == "3D":
                    d["3d"] = True
                elif b.isdigit() and int(b) in _FPS_VALUES:
                    d["fps"] = int(b)
                elif b.isdigit():
                    d["version"] = int(b)
        elif "aspect" not in d and re.fullmatch(r"(F|S|C)(-\d+)?", P):
            d["aspect"] = P[0]
        elif re.fullmatch(r"[A-Z]{2,3}(-[A-Z]{2,4})+", P) and ("lang" not in d or "territory" not in d):
            if "lang" not in d:
                d["lang"] = P
                d["audio_lang"] = bits[0]
                d["sub_lang"] = bits[1]
                d["ccap"] = "CCAP" in bits
                d["ocap"] = "OCAP" in bits
            else:
                d["territory"] = P
        elif "audio" not in d and re.fullmatch(r"(10|20|51|71|MOS)(-.*)?", P):
            d["audio"] = bits[0]
            d["immersive"] = any(b in ("ATMOS", "IAB", "DTSX", "AURO") for b in bits[1:])
        elif P in ("2K", "4K"):
            d["res"] = P
        elif re.fullmatch(r"\d{8}", P):
            d["date"] = P
        elif bits[0] in ("SMPTE", "IOP"):
            d["std"] = bits[0]
            if "3D" in bits:
                d["3d"] = True
        elif P in ("OV", "VF"):
            d["pkg"] = P
        elif P == "3D":
            d["3d"] = True
    if sum(k in d for k in ("kind", "res", "std", "audio", "pkg", "lang")) < 3:
        return {}
    return d


def cpl_standard(cpl):
    """'SMPTE' | 'IOP' | '' από το namespace του CPL."""
    if getattr(cpl, "_std", None) is not None:
        return cpl._std
    std = ""
    if not cpl.is_file:
        root = xml_root(cpl.path)
        tag = root.tag if root is not None else ""
        if "smpte-ra.org" in tag:
            std = "SMPTE"
        elif "digicine.com" in tag:
            std = "IOP"
    cpl._std = std
    return std


_ASPECTS = {"F": (1.80, 1.90, "1.85 (Flat)"), "S": (2.30, 2.45, "2.39 (Scope)"), "C": (1.88, 1.92, "1.90 (Full)")}


def name_issues(item):
    """Διαφορές ανάμεσα στο όνομα DCNC και στο πραγματικό περιεχόμενο."""
    c, info = item["cpl"], item.get("info") or {}
    d = parse_dcnc(c.title)
    if not d or c.is_file:
        return []
    out = []
    size, ch = info.get("size"), info.get("ch")
    if size and d.get("res"):
        real = "2K" if size[0] <= 2048 else "4K"
        if real != d["res"]:
            out.append(("warn", f"όνομα: {d['res']}, εικόνα {size[0]}x{size[1]}"))
    if size and d.get("aspect") in _ASPECTS:
        lo, hi, label = _ASPECTS[d["aspect"]]
        ar = size[0] / max(1, size[1])
        if not lo <= ar <= hi:
            out.append(("warn", f"όνομα: {label}, εικόνα {ar:.2f}:1"))
    if "audio" in d and info and "pic_err" not in info:
        need = {"10": 1, "20": 2, "51": 6, "71": 8}.get(d["audio"])
        has_snd = any(r.snd for r in c.reels)
        if d["audio"] == "MOS" and has_snd:
            out.append(("info", "όνομα: MOS (χωρίς ήχο), αλλά έχει ήχο"))
        elif need and not has_snd:
            out.append(("warn", f"όνομα: ήχος {d['audio']}, αλλά δεν έχει ήχο"))
        elif need and ch and ch < need:
            out.append(("warn", f"όνομα: ήχος {d['audio'][0]}.{d['audio'][1]}, αλλά έχει {ch} κανάλια"))
    if d.get("sub_lang"):
        langs = track_langs(c)
        want = norm_lang(d["sub_lang"])
        if d["sub_lang"] != "XX" and want not in langs:
            out.append(("warn", f"όνομα: υπότιτλοι {d['sub_lang']}, αλλά " +
                        (f"βρέθηκαν {', '.join(sorted(langs)).upper()}" if langs else "δεν βρέθηκαν υπότιτλοι")))
        elif d["sub_lang"] == "XX" and langs:
            out.append(("info", f"όνομα: χωρίς υπότιτλους (XX), αλλά έχει {', '.join(sorted(langs)).upper()}"))
    fps = float(c.rate)
    if d.get("fps") and abs(d["fps"] - fps) > 0.01:
        out.append(("warn", f"όνομα: {d['fps']} fps, το CPL έχει {fps:g}"))
    std = cpl_standard(c)
    if d.get("std") and std and d["std"] != std:
        out.append(("warn", f"όνομα: {d['std']}, το CPL είναι {std}"))
    if d.get("pkg") == "OV" and c.external:
        out.append(("warn", "όνομα: OV, αλλά παίρνει αρχεία από άλλο DCP (είναι VF)"))
    stereo = any(r.stereo for r in c.reels)
    if d.get("3d") and not stereo:
        out.append(("warn", "όνομα: 3D, αλλά η εικόνα είναι 2D"))
    elif stereo and not d.get("3d"):
        out.append(("info", "η εικόνα είναι 3D, το όνομα δεν το γράφει"))
    for r in c.reels:  # ήχος και εικόνα ίδιας διάρκειας
        if r.snd and r.pic and r.snd_rate and r.rate:
            diff = abs(float(r.snd_dur / r.snd_rate) - float(r.dur / r.rate))
            if diff > 1.5 / float(r.rate):
                out.append(("warn", f"reel {c.reels.index(r) + 1}: ήχος και εικόνα διαφέρουν {diff:.2f} δευτ."))
                break
    return out


def dcnc_summary(cpl):
    d = parse_dcnc(cpl.title)
    if not d:
        return ""
    parts = []
    if d.get("kind"):
        parts.append({"FTR": "ταινία", "SHR": "μικρού μήκους", "TLR": "τρέιλερ", "TSR": "teaser", "ADV": "διαφήμιση",
                      "PSA": "ανακοίνωση", "TST": "test", "RTG": "rating"}.get(d["kind"], d["kind"]))
    if d.get("lang"):
        parts.append(f"ήχος {d['audio_lang']}" + (f", υπότ. {d['sub_lang']}" if d["sub_lang"] != "XX" else ""))
    for k in ("audio", "res", "std", "pkg"):
        if d.get(k):
            parts.append(d[k] if k != "audio" or d[k] == "MOS" else f"{d[k][0]}.{d[k][1]}")
    if d.get("date"):
        parts.append(f"{d['date'][6:]}/{d['date'][4:6]}/{d['date'][:4]}")
    return " · ".join(parts)
