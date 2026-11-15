"""Σάρωση όλης της ταινίας: μαύρη εικόνα, σιωπή, κανάλια, clipping, υπότιτλοι."""
import datetime
import json
import math
import os

from app.checks.loudness import LoudnessMeter, describe as describe_loudness
from app.core import prefs
from app.core.avio import fit_channels, frame_at, reel_audio
from app.core.config import APP_DIR, CH_NAMES, log
from app.core.util import check_cancel
from app.core.subtitles import load_tracks
from app.core.util import fmt_time
from app.tools.subcheck import check_entries, summary as subs_summary


SCANS_DIR = os.path.join(APP_DIR, "scans")


# ======================================================================== ΣΑΡΩΣΗ ΤΑΙΝΙΑΣ
def _scan_path(cpl):
    return os.path.join(SCANS_DIR, f"{cpl.id or 'x'}.json")


def load_scan(cpl):
    try:
        with open(_scan_path(cpl), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _runs(flags, step, min_len):
    """flags: [(t, bool)] -> [(αρχή, τέλος)] συνεχόμενων True με διάρκεια >= min_len."""
    out, start, last = [], None, None
    for t, f in flags:
        if f and start is None:
            start = t
        if not f and start is not None:
            if t - start >= min_len:
                out.append((start, t))
            start = None
        last = t
    if start is not None and last is not None and last + step - start >= min_len:
        out.append((start, last + step))
    return out


SILENCE_DB = -60      # κάτω από αυτό (RMS ανά δευτ., στο πιο δυνατό κανάλι) θεωρείται σιωπή
DEAD_DB = -70         # κανάλι που δεν ξεπερνά ποτέ αυτή τη στάθμη: χωρίς σήμα
CLIP_LEVEL = 0.9999   # δείγματα σε πλήρη κλίμακα
BLACK_MEAN, BLACK_PEAK = 6, 30   # μέση φωτεινότητα / 99ο εκατοστημόριο (0-255) για «μαύρο καρέ»


def scan_cpl(cpl, progress=None, cancel=None):
    """
    Σάρωση όλης της ταινίας χωρίς προβολή: μαύρα καρέ (δείγμα κάθε λίγα δευτ.), σιωπή, κανάλια χωρίς σήμα,
    clipping, ένταση LUFS, υπότιτλοι. Αποθηκεύεται στο %APPDATA%\\ReelCheck\\scans.
    """
    res = {"version": 1, "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), "seconds": cpl.seconds}

    def tick(frac, msg):
        check_cancel(cancel)
        if progress:
            progress(frac, msg)

    res.update(_scan_picture(cpl, lambda f, m: tick(0.6 * f, m)))
    res.update(_scan_audio(cpl, lambda f, m: tick(0.6 + 0.38 * f, m)))
    tick(0.99, "Έλεγχος υποτίτλων")
    res.update(_scan_subtitles(cpl))
    res["issues"] = scan_findings(res)
    _save_scan(cpl, res)
    log.info("Σάρωση %s: %s", cpl.title, res["issues"])
    return res


def _save_scan(cpl, res):
    try:
        os.makedirs(SCANS_DIR, exist_ok=True)
        with open(_scan_path(cpl), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    except OSError:
        log.exception("Αποθήκευση σάρωσης")


def _is_black(frame):
    """Μαύρο καρέ: πολύ χαμηλή μέση φωτεινότητα και σχεδόν κανένα φωτεινό σημείο (σε μικρογραφία)."""
    import numpy as np
    a = frame.reformat(width=96, height=max(2, int(96 * frame.height / frame.width)) // 2 * 2,
                       format="rgb24").to_ndarray()
    return float(a.mean()) < BLACK_MEAN and float(np.percentile(a, 99)) < BLACK_PEAK


def _scan_picture(cpl, progress):
    """Μαύρη εικόνα: δείγμα κάθε `step` δευτ. (περίπου 3000 δείγματα για όλη την ταινία)."""
    import av
    total = cpl.seconds
    step = max(2.0, total / 3000.0)
    flags = []
    for reel in cpl.reels:
        if not reel.pic:
            continue
        with av.open(reel.pic) as c:
            vs = c.streams.video[0]
            if vs.codec_context.name == "jpeg2000":
                vs.codec_context.options = {"lowres": "2"}  # μικρή ανάλυση: αρκεί για τη φωτεινότητα
            intra = vs.codec_context.name in ("jpeg2000", "prores", "dnxhd", "mjpeg")
            t = 0.0
            while t < reel.seconds:
                progress((reel.start + t) / max(1, total), f"Σάρωση εικόνας {fmt_time(reel.start + t)}")
                fr = frame_at(c, vs, reel, t, exact=not intra)
                if fr is not None:
                    flags.append((reel.start + t, _is_black(fr)))
                t += step
    return {"black": _runs(flags, step, step), "pic_step": step}  # το όριο για εύρημα είναι στις Ρυθμίσεις


class _AudioStats:
    """Στατιστικά ήχου όλης της ταινίας: κορυφές, clipping, ενέργεια, RMS ανά δευτερόλεπτο, LUFS."""

    def __init__(self, nch, rate):
        import numpy as np
        self.np, self.nch, self.rate = np, nch, rate
        self.peaks = np.zeros(nch, np.float32)
        self.clip = np.zeros(nch, np.int64)
        self.sumsq = np.zeros(nch, np.float64)
        self.n = 0
        self.sec_rms, self.acc, self.acc_n = [], None, 0
        # στα DCP τα 6 πρώτα κανάλια είναι L R C LFE Ls Rs (τα υπόλοιπα HI/VI κ.λπ.)
        self.meter = LoudnessMeter(6 if nch >= 6 else nch, rate)

    def add(self, a):
        np = self.np
        a = fit_channels(a, self.nch)
        self.meter.add(a)
        ab = np.abs(a)
        self.peaks = np.maximum(self.peaks, ab.max(axis=0))
        self.clip += (ab >= CLIP_LEVEL).sum(axis=0)
        sq = a.astype(np.float64) ** 2
        self.sumsq += sq.sum(axis=0)
        self.n += len(a)
        i = 0
        while i < len(sq):  # RMS ανά δευτερόλεπτο (το πιο δυνατό κανάλι)
            take = min(len(sq) - i, self.rate - self.acc_n)
            s = sq[i:i + take].sum(axis=0)
            self.acc = s if self.acc is None else self.acc + s
            self.acc_n += take
            i += take
            if self.acc_n >= self.rate:
                self._close_second()

    def _close_second(self):
        self.sec_rms.append(float(self.np.sqrt(self.acc.max() / self.acc_n)))
        self.acc, self.acc_n = None, 0

    def result(self):
        if self.acc is not None and self.acc_n:
            self._close_second()
        db = lambda v: round(20 * math.log10(max(float(v), 1e-9)), 1)
        main = [i for i in range(min(self.nch, 6)) if i != 3]  # L R C Ls Rs (χωρίς LFE)
        dead = 10 ** (DEAD_DB / 20)
        power = [self.sumsq[i] / max(1, self.n) for i in main] or [0]
        silent = [(float(i), v < 10 ** (SILENCE_DB / 20)) for i, v in enumerate(self.sec_rms)]
        return {"nch": self.nch, "peak_db": [db(p) for p in self.peaks], "clip": [int(x) for x in self.clip],
                "dead": [CH_NAMES[i] for i in main if self.peaks[i] < dead],
                "lfe_empty": self.nch > 3 and bool(self.peaks[3] < dead),
                "level_db": db(math.sqrt(sum(power))), "lufs": self.meter.result(),
                "silence": _runs(silent, 1.0, 3.0),  # το όριο για εύρημα είναι στις Ρυθμίσεις
                "audio_seconds": len(self.sec_rms)}


def _scan_audio(cpl, progress):
    """Ήχος όλων των reels στη σειρά (όση διάρκεια δηλώνει το CPL για τον ήχο)."""
    import av
    total, stats, done = cpl.seconds, None, 0
    for reel in cpl.reels:
        if not reel.snd:
            continue
        with av.open(reel.snd) as c:
            rate = c.streams.audio[0].codec_context.sample_rate or 48000
        for a in reel_audio(reel, rate):
            if stats is None:
                stats = _AudioStats(a.shape[1], rate)
            stats.add(a)
            done += len(a)
            if done >= rate * 20:
                done = 0
                progress(min(1.0, len(stats.sec_rms) / max(1, total)), f"Σάρωση ήχου {fmt_time(len(stats.sec_rms))}")
    return stats.result() if stats else {}


def _scan_subtitles(cpl):
    """Υπότιτλοι εκτός διάρκειας, πολύ σύντομοι, και οι έλεγχοι υποτίτλων (επικαλύψεις, ταχύτητα κ.λπ.)."""
    total, out_of_range, short, checks = cpl.seconds, 0, 0, []
    for t in load_tracks(cpl):
        for e in t["entries"]:
            if e[0] < -0.01 or e[1] > total + 0.5:
                out_of_range += 1
            elif e[1] - e[0] < 0.25:
                short += 1
        problems = check_entries(t["entries"], total)
        if problems:
            first = [(p[2], p[3]) for p in problems if p[1] == "warn"][:3]
            checks.append({"track": t["name"], "summary": subs_summary(problems), "first": first})
    out = {"subs_out": out_of_range, "subs_short": short}
    if checks:
        out["subs_check"] = checks
    return out


MAX_LISTED = 5  # πόσα σημεία μαύρης εικόνας / σιωπής αναφέρονται ένα-ένα


def _span(a, b):
    return f"{fmt_time(a)}–{fmt_time(b)}"


def _listed(runs, what):
    """Ευρήματα για τα πρώτα σημεία (μαύρη εικόνα, σιωπή) και πόσα ακόμα υπάρχουν."""
    out = [("warn", f"{what} {_span(a, b)} ({b - a:.0f} δευτ.)") for a, b in runs[:MAX_LISTED]]
    if len(runs) > MAX_LISTED:
        out.append(("warn", f"και άλλα {len(runs) - MAX_LISTED} σημεία με {what}"))
    return out


def _picture_findings(res, inside):
    black = [(a, b) for a, b in res.get("black", []) if b - a >= prefs.get("scan_black_min")]
    mid = [r for r in black if inside(r)]
    edge = [r for r in black if not inside(r)]
    out = _listed(mid, "μαύρη εικόνα")
    if edge:
        out.append(("info", "μαύρο στην αρχή/τέλος (τίτλοι;): " + ", ".join(_span(a, b) for a, b in edge[:3])))
    return out


def _loudness_findings(res):
    mx = max(res["peak_db"][:6] or [-99])
    lufs = res.get("lufs")
    if not lufs:  # παλιές σαρώσεις, πριν από τη μέτρηση LUFS
        return [("info", f"μέγιστη στάθμη {mx:.1f} dBFS, μέση {res.get('level_db', 0):.1f} dBFS (RMS)")]
    out = [("info", describe_loudness(lufs) + f", μέγιστη στάθμη {mx:.1f} dBFS")]
    integ = lufs.get("integrated")
    if prefs.get("lufs_check") and integ is not None:  # στόχος έντασης από τις Ρυθμίσεις
        target, tol = prefs.get("lufs_target"), prefs.get("lufs_tolerance")
        goal = f"(στόχος {target:g} ±{tol:g})"
        if integ > target + tol:
            out.append(("warn", f"πολύ δυνατή: {integ:.1f} LUFS {goal}"))
        elif integ < target - tol:
            out.append(("warn", f"πολύ σιγανή: {integ:.1f} LUFS {goal}"))
    return out


def _audio_findings(res, inside, total):
    if "peak_db" not in res:
        return []
    silence = [r for r in res.get("silence", []) if r[1] - r[0] >= prefs.get("scan_silence_min") and inside(r)]
    out = _listed(silence, "σιωπή")
    if res.get("dead"):
        out.append(("warn", f"κανάλια χωρίς σήμα σε όλη την ταινία: {', '.join(res['dead'])}"))
    if res.get("lfe_empty"):
        out.append(("info", "το LFE (subwoofer) δεν έχει σήμα"))
    clipped = [(CH_NAMES[i] if i < len(CH_NAMES) else str(i + 1), n)
               for i, n in enumerate(res.get("clip", [])) if n > 100]
    if clipped:
        out.append(("warn", "clipping (ψαλίδισμα): " + ", ".join(f"{c} {n}" for c, n in clipped)))
    out += _loudness_findings(res)
    if res.get("audio_seconds") and abs(res["audio_seconds"] - total) > 2:
        out.append(("warn", f"ο ήχος κρατά {fmt_time(res['audio_seconds'])}, η εικόνα {fmt_time(total)}"))
    return out


def _subtitle_findings(res):
    out = []
    if res.get("subs_out"):
        out.append(("warn", f"{res['subs_out']} υπότιτλοι εκτός διάρκειας ταινίας"))
    if res.get("subs_short"):
        out.append(("info", f"{res['subs_short']} υπότιτλοι διαρκούν λιγότερο από 0,25 δευτ."))
    for sc in res.get("subs_check", []):
        if sc["summary"]:
            out.append(("warn", f"υπότιτλοι {sc['track']}: {sc['summary']}"))
        out += [("info", f"  υπότιτλος {fmt_time(t)}: {text}") for t, text in sc.get("first", [])]
    return out


def scan_findings(res):
    """[(err|warn|info, κείμενο)] από το αποτέλεσμα της σάρωσης."""
    total = res.get("seconds", 0)
    head, tail = prefs.get("scan_edge_start") * 60, prefs.get("scan_edge_end") * 60  # τίτλοι αρχής / τέλους

    def inside(run):  # όχι στους τίτλους αρχής / τέλους
        return run[0] > head and run[1] < total - tail

    out = _picture_findings(res, inside) + _audio_findings(res, inside, total) + _subtitle_findings(res)
    return out or [("info", "σάρωση: κανένα πρόβλημα")]
