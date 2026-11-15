"""
Συγχρονισμός ήχου-εικόνας: βρίσκει «φλας» στην εικόνα (καρέ πολύ φωτεινότερο από τα γύρω, όπως στο
sync test / 2-pop / κλακέτα) και σύντομα «μπιπ» στον ήχο, τα ζευγαρώνει και μετρά πόσα ms διαφέρουν.
Θετικός αριθμός = ο ήχος έρχεται αργότερα από την εικόνα.
"""
import math
import statistics

from app.core.avio import reel_audio, reel_frames
from app.core.util import check_cancel, fmt_time

FLASH_JUMP = 60         # η φωτεινότητα (0-255) ανεβαίνει τουλάχιστον τόσο πάνω από τη γύρω μέση
FLASH_MAX_FRAMES = 3    # φλας = έως τόσα καρέ (όχι αλλαγή πλάνου)
BEEP_RISE_DB = 25       # το «μπιπ» είναι τόσα dB πάνω από τη στάθμη πριν
BEEP_MAX_SEC = 0.6      # και σύντομο
WINDOW = 0.002          # ανάλυση ήχου: 2 ms
PAIR_MAX = 0.5          # φλας και μπιπ σε απόσταση έως μισό δευτ. θεωρούνται ζευγάρι
OK_MS, WARN_MS = 22, 45  # μισό καρέ στα 24 fps ≈ 21 ms


def _luma(frame):
    """Μέση φωτεινότητα του καρέ (σε μικρογραφία, γκρι)."""
    a = frame.reformat(width=64, height=max(2, int(64 * frame.height / frame.width)) // 2 * 2,
                       format="gray").to_ndarray()
    return float(a.mean())


def find_flashes(samples):
    """samples: [(χρόνος, φωτεινότητα)] -> [χρόνοι φλας]."""
    out, i, n = [], 0, len(samples)
    while i < n:
        t, v = samples[i]
        before = [s[1] for s in samples[max(0, i - 12):i]] or [v]
        base = statistics.median(before)
        if v - base >= FLASH_JUMP:
            j = i
            while j + 1 < n and samples[j + 1][1] - base >= FLASH_JUMP / 2:
                j += 1
            if j - i + 1 <= FLASH_MAX_FRAMES:
                out.append(t)
            i = j + 1
            continue
        i += 1
    return out


def find_beeps(levels, step=WINDOW):
    """levels: στάθμη σε dB ανά παράθυρο `step` -> [χρόνοι έναρξης σύντομων ήχων μετά από ησυχία]."""
    out, i, n = [], 0, len(levels)
    look = int(0.1 / step)  # 100 ms πριν
    max_len = int(BEEP_MAX_SEC / step)
    while i < n:
        before = levels[max(0, i - look):i]
        base = max(before) if before else levels[i]
        if before and levels[i] - base >= BEEP_RISE_DB:
            j = i
            while j < n and levels[j] - base >= BEEP_RISE_DB / 2:
                j += 1
            if j - i <= max_len:
                out.append(i * step)
            i = j + look
            continue
        i += 1
    return out


def _audio_levels(reel, skip, limit, cancel):
    import numpy as np
    rate = 48000
    win = int(rate * WINDOW)
    levels, rest = [], None
    for a in reel_audio(reel, rate, skip, limit, stop=cancel):
        x = np.abs(a).max(axis=1)  # το πιο δυνατό κανάλι
        if rest is not None:
            x = np.concatenate([rest, x])
        k = len(x) // win
        peaks = x[:k * win].reshape(k, win).max(axis=1)
        levels.extend(20 * math.log10(max(float(p), 1e-6)) for p in peaks)
        rest = x[k * win:]
    return levels


def _reel_at(cpl, start):
    for r in cpl.reels:
        if r.pic and r.snd and r.start <= start < r.start + r.seconds:
            return r
    return next((r for r in cpl.reels if r.pic and r.snd), None)


def check_sync(cpl, start=0.0, seconds=120.0, progress=None, cancel=None):
    """
    Ανάλυση από τη στιγμή start για `seconds` δευτ. (μέσα σε ένα reel).
    Επιστρέφει {"pairs": [(χρόνος, ms)], "offset_ms", "flashes", "beeps", "level", "text"}.
    """
    reel = _reel_at(cpl, start)
    if reel is None:
        return {"pairs": [], "offset_ms": None, "flashes": 0, "beeps": 0, "level": "info",
                "text": "Χρειάζεται εικόνα και ήχος στο ίδιο reel."}
    skip = max(0.0, start - reel.start)
    limit = min(seconds, reel.seconds - skip)
    samples = []
    for t, fr in reel_frames(reel, skip, limit, stop=cancel, lowres="2"):
        samples.append((t, _luma(fr)))
        if progress and len(samples) % 24 == 0:
            progress(0.8 * (t - skip) / max(1.0, limit), f"Εικόνα {fmt_time(reel.start + t)}")
    check_cancel(cancel)
    if progress:
        progress(0.85, "Ήχος")
    levels = _audio_levels(reel, skip, limit, cancel)
    check_cancel(cancel)
    flashes = find_flashes(samples)
    beeps = [skip + b for b in find_beeps(levels)]
    return _result(reel, flashes, beeps)


def _result(reel, flashes, beeps):
    pairs = []
    for f in flashes:
        near = min(beeps, key=lambda b: abs(b - f), default=None)
        if near is not None and abs(near - f) <= PAIR_MAX:
            pairs.append((reel.start + f, round((near - f) * 1000)))
    res = {"pairs": pairs, "flashes": len(flashes), "beeps": len(beeps)}
    if not pairs:
        res.update(offset_ms=None, level="info",
                   text=f"Δεν βρέθηκαν ζευγάρια φλας + μπιπ (φλας: {len(flashes)}, μπιπ: {len(beeps)}). "
                        "Χρειάζεται sync test, 2-pop ή κλακέτα στο κομμάτι που ελέγχεται.")
        return res
    off = round(statistics.median(ms for _t, ms in pairs))
    frame_ms = 1000 / float(reel.rate)
    level = "ok" if abs(off) <= OK_MS else ("warn" if abs(off) <= WARN_MS else "err")
    where = "αργότερα" if off > 0 else "νωρίτερα"
    text = (f"Ο ήχος έρχεται {abs(off)} ms {where} από την εικόνα ({abs(off) / frame_ms:.1f} καρέ), "
            f"από {len(pairs)} μετρήσεις." if off else f"Ήχος και εικόνα συγχρονισμένα ({len(pairs)} μετρήσεις).")
    spread = max(ms for _t, ms in pairs) - min(ms for _t, ms in pairs)
    if len(pairs) > 1 and spread > frame_ms * 1.5:
        text += f" Οι μετρήσεις διαφέρουν μεταξύ τους κατά {spread} ms (ο συγχρονισμός «γλιστράει»;)."
    res.update(offset_ms=off, level=level, text=text)
    return res
