"""
Μετατροπή βίντεο (MP4, MOV, ...) σε DCP με το DCP-o-matic:
όλα σε ένα DCP με τη σειρά (ένα reel ανά ταινία, κενό ανάμεσα) ή ξεχωριστό DCP ανά αρχείο.
"""
import os
import re
import shutil
import time

from app.core.config import log
from app.core.hashing import Cancelled
from app.tools import dcpomatic
from app.checks.loudness import measure_file
from app.core.subtitles import parse_srt
from app.tools.mediainfo import audio_level, equalize_gains
from app.tools.subcheck import check_entries, summary

WORK_DIR_NAME = "_ReelCheck_work"  # μόνο λατινικά: το DCP-o-matic δεν γράφει σε διαδρομές με ελληνικά
DEFAULT_OPTS = {"mode": "single", "name": "Program", "kind": "SHR", "ratio": "185", "standard": "SMPTE",
                "fps": None, "gap": 2.0, "sub_lang": "", "normalize": True, "reel_per_film": True}


_GREEK_LATIN = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i",
                "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
                "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}


def latin(text):
    """Ελληνικά σε λατινικούς χαρακτήρες (τα ονόματα DCP δέχονται μόνο λατινικά): Το Ταξίδι -> To Taxidi."""
    import unicodedata
    plain = "".join(ch for ch in unicodedata.normalize("NFD", text or "") if unicodedata.category(ch) != "Mn")
    plain = plain.replace("ΟΥ", "OU").replace("Ου", "Ou").replace("ου", "ou")
    out = []
    for ch in plain:
        low = ch.lower()
        rep = _GREEK_LATIN.get(low)
        if rep is None:
            out.append(ch)
        elif ch != low:
            out.append(rep.upper() if plain.isupper() else rep.capitalize())
        else:
            out.append(rep)
    s = "".join(out).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()


def film_name(path):
    """Όνομα ταινίας από το όνομα αρχείου (χωρίς κατάληξη και σύμβολα, με λατινικούς χαρακτήρες)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return latin(re.sub(r"[_\-.]+", " ", stem)) or "Film"


def fps_values(infos):
    return sorted({round(i["fps"], 2) for i in infos if i.get("fps")})


def warnings_for(infos, opts):
    """Τι να προσέξει ο χρήστης πριν ξεκινήσει: διαφορετικά fps, αρχεία που δεν διαβάζονται, χωρίς ήχο."""
    out = []
    bad = [os.path.basename(i["path"]) for i in infos if i.get("error")]
    if bad:
        out.append("Δεν διαβάζονται: " + ", ".join(bad))
    fps = fps_values(infos)
    target = opts.get("fps") or (fps[0] if len(fps) == 1 else None)
    if opts.get("mode") == "single" and len(fps) > 1:
        out.append(f"Διαφορετικά fps ({', '.join(f'{f:g}' for f in fps)}): όλα θα γίνουν "
                   f"{target or 'ίδια'} fps (ελαφρώς πιο γρήγορα/αργά ή με επανάληψη καρέ).")
    silent = [os.path.basename(i["path"]) for i in infos if not i.get("channels") and not i.get("error")]
    if silent:
        out.append("Χωρίς ήχο: " + ", ".join(silent))
    out += srt_warnings(infos)
    return out


def srt_warnings(infos):
    """Προβλήματα στα .srt που θα μπουν (επικαλύψεις, γρήγοροι υπότιτλοι, μετά το τέλος κ.λπ.)."""
    out = []
    for i in infos:
        if not i.get("srt") or i.get("error"):
            continue
        try:
            problems = [p for p in check_entries(parse_srt(i["srt"]), i.get("duration") or None) if p[1] == "warn"]
        except Exception:
            out.append(f"Δεν διαβάζεται: {os.path.basename(i['srt'])}")
            continue
        if problems:
            out.append(f"{os.path.basename(i['srt'])}: {summary(problems)} (Εργαλεία → Έλεγχος υποτίτλων)")
    return out


def is_ascii(path):
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def work_dir_for(out_dir):
    """
    Προσωρινός φάκελος στον ίδιο δίσκο με την έξοδο (για να χωράνε τα προσωρινά καρέ και η μεταφορά να είναι
    γρήγορη), πάντα με λατινικούς χαρακτήρες: αν η έξοδος έχει ελληνικά, στη ρίζα του δίσκου.
    """
    out_dir = os.path.abspath(out_dir)
    if is_ascii(out_dir):
        return os.path.join(out_dir, WORK_DIR_NAME)
    return os.path.join(os.path.splitdrive(out_dir)[0] + os.sep, WORK_DIR_NAME)


def check_space(out_dir, seconds):
    need = dcpomatic.estimate_bytes(seconds)
    free = shutil.disk_usage(out_dir).free
    if need > free:
        gb = 1024 ** 3
        raise OSError(f"Δεν χωράει: χρειάζονται περίπου {need / gb:.0f} GB, ελεύθερα {free / gb:.0f} GB")


class Progress:
    """Συνολική πρόοδος από φάσεις με βάρη (π.χ. ανάλυση ήχου 5%, κωδικοποίηση 95%)."""

    def __init__(self, report, total_weight):
        self.report, self.total, self.done = report, max(total_weight, 1e-9), 0.0

    def phase(self, weight, prefix=""):
        start = self.done

        def p(frac, msg):
            if self.report:
                self.report(min(1.0, (start + weight * frac) / self.total), f"{prefix}{msg}")
        self.done += weight
        return p


def convert(infos, opts, out_dir, progress=None, cancel=None):
    """
    infos: probe_media() των αρχείων με τη σειρά προβολής. Επιστρέφει λίστα με τους φακέλους DCP που βγήκαν.
    """
    opts = dict(DEFAULT_OPTS, **opts)
    infos = [i for i in infos if not i.get("error")]
    if not infos:
        raise ValueError("Δεν υπάρχουν αρχεία για μετατροπή.")
    total_sec = sum(i["duration"] for i in infos)
    os.makedirs(out_dir, exist_ok=True)
    check_space(out_dir, total_sec)
    tools = dcpomatic.find_dcpomatic()
    if not tools:
        raise RuntimeError("Δεν βρέθηκε το DCP-o-matic.")
    prog = Progress(progress, total_sec * (1.05 if opts["normalize"] else 1.0))
    gains = _gains(infos, opts, prog, cancel)
    entries = [{"path": i["path"], "srt": i.get("srt"), "gain_db": g} for i, g in zip(infos, gains)]
    work = work_dir_for(out_dir)
    os.makedirs(work, exist_ok=True)
    jobs = ([(latin(opts["name"]) or "Program", entries)] if opts["mode"] == "single"
            else [(film_name(e["path"]), [e]) for e in entries])
    results = []
    for n, (name, ents) in enumerate(jobs, 1):
        secs = sum(i["duration"] for i in infos if i["path"] in {e["path"] for e in ents})
        label = f"[{n}/{len(jobs)}] {name[:30]}: " if len(jobs) > 1 else ""
        results.append(_one_dcp(tools, name, ents, opts, work, out_dir, prog.phase(secs, label), cancel))
    shutil.rmtree(work, ignore_errors=True)
    return results


def _loudness(info, cancel):
    """(ένταση LUFS, true peak dBTP) για την εξίσωση· αν το LUFS αποτύχει, μέση στάθμη RMS και κορυφή."""
    if not info.get("channels"):
        return None, None
    try:
        r = measure_file(info["path"], cancel)
        if r and r["integrated"] is not None:
            return r["integrated"], r["true_peak"]
    except Cancelled:
        raise
    except Exception:
        log.exception("Μέτρηση LUFS %s", info["path"])
    return audio_level(info["path"], cancel)


def _gains(infos, opts, prog, cancel):
    if not opts["normalize"] or len(infos) < 2:
        return [0.0] * len(infos)
    total = sum(i["duration"] for i in infos)
    p = prog.phase(total * 0.05, "Ανάλυση έντασης: ")
    levels = []
    for k, i in enumerate(infos):
        p(k / len(infos), os.path.basename(i["path"])[:40])
        levels.append(_loudness(i, cancel))
    gains = equalize_gains(levels)
    log.info("Εξίσωση έντασης: %s", list(zip([os.path.basename(i["path"]) for i in infos], levels, gains)))
    return gains


def _one_dcp(tools, name, entries, opts, work, out_dir, progress, cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    film_dir = os.path.join(work, f"{re.sub(r'[^A-Za-z0-9]+', '_', name)[:40]}_{int(time.time())}")
    progress(0, "Προετοιμασία project")
    dcpomatic.create_project(tools, film_dir, entries, dict(opts, name=name))
    try:
        dcp = dcpomatic.make_dcp(tools, film_dir, progress, cancel)
    except Cancelled:
        shutil.rmtree(film_dir, ignore_errors=True)
        raise
    final = dcpomatic.finish_output(dcp, out_dir, film_dir)
    log.info("DCP έτοιμο: %s", final)
    return final
