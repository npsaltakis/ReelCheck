"""Σχετικά CPL (ίδια διάρκεια) και εναλλακτικός ήχος."""
import os

from app.checks.dcnc import parse_dcnc
from app.core.config import log
from app.core.dcp import load_dcp


# ======================================================================== ΣΧΕΤΙΚΑ CPL / ΕΝΑΛΛΑΚΤΙΚΟΣ ΗΧΟΣ
def sibling_cpls(cpl, tolerance=1.0):
    """CPL ίδιας διάρκειας στον ίδιο φάκελο και στα διπλανά DCP."""
    if cpl.is_file:
        return []
    folders = [cpl.folder]
    parent = os.path.dirname(cpl.folder)
    try:
        for n in sorted(os.listdir(parent)):
            d = os.path.join(parent, n)
            if os.path.normcase(d) != os.path.normcase(cpl.folder) and os.path.isdir(d) and \
                    any(f.upper().startswith("ASSETMAP") for f in os.listdir(d)):
                folders.append(d)
    except OSError:
        pass
    out = []
    for f in folders:
        for o in load_dcp(f):
            if o.id != cpl.id and abs(o.seconds - cpl.seconds) <= tolerance:
                out.append(o)
    return out


def _snd_tuple(cpl):
    return [(r.snd, r.snd_entry, r.snd_rate, r.snd_dur) for r in cpl.reels]


def audio_label(cpl, fallback="κύριος"):
    d = parse_dcnc(cpl.title)
    if d.get("audio_lang") and d["audio_lang"] != "XX":
        lab = d["audio_lang"]
        if d.get("audio"):
            lab += f" {d['audio'][0]}.{d['audio'][1]}" if d["audio"] != "MOS" else " MOS"
        return lab
    return fallback


def audio_alternatives(cpl):
    """[(ετικέτα, [(snd, entry, rate, dur) ανά reel])]: πρώτος ο ήχος του ίδιου του CPL."""
    own = _snd_tuple(cpl)
    out = [(audio_label(cpl), own)]
    key_of = lambda paths: tuple(os.path.normcase(os.path.abspath(p)) if p else "" for p in paths)
    seen = {key_of(s[0] for s in own)}
    try:
        others = sibling_cpls(cpl)
    except Exception:
        log.exception("Αναζήτηση ήχου σε άλλα CPL")
        others = []
    for o in others:
        if len(o.reels) != len(cpl.reels) or not all(r.snd for r in o.reels):
            continue
        key = key_of(r.snd for r in o.reels)
        if key in seen:
            continue
        seen.add(key)
        label = audio_label(o, o.title[:30])
        if any(label == x[0] for x in out):  # ίδια ετικέτα: προσθήκη τίτλου για να ξεχωρίζουν
            label += f" ({o.title[:20]})"
        out.append((label, _snd_tuple(o)))
    return out
