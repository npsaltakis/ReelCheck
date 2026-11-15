"""Στοιχεία αρχείων βίντεο (διάρκεια, fps, ανάλυση, ήχος) και στάθμη ήχου, με PyAV."""
import math
import os

from app.core.avio import frame_to_float
from app.core.config import log
from app.core.util import check_cancel

SUB_EXT = (".srt",)


def find_srt(video):
    """Υπότιτλοι .srt με το ίδιο όνομα δίπλα στο βίντεο (π.χ. ταινία.srt, ταινία.el.srt)."""
    base = os.path.splitext(video)[0]
    folder = os.path.dirname(video) or "."
    name = os.path.basename(base).lower()
    for f in sorted(os.listdir(folder)):
        low = f.lower()
        if low.endswith(SUB_EXT) and (low[:-4] == name or low.startswith(name + ".")):
            return os.path.join(folder, f)
    return None


def probe_media(path):
    """{'duration', 'fps', 'width', 'height', 'channels', 'srt', 'error'}."""
    import av
    info = {"path": path, "duration": 0.0, "fps": None, "width": 0, "height": 0, "channels": 0,
            "srt": find_srt(path), "error": None}
    try:
        with av.open(path) as c:
            if c.duration:
                info["duration"] = c.duration / 1_000_000
            if c.streams.video:
                v = c.streams.video[0]
                info["width"], info["height"] = v.codec_context.width, v.codec_context.height
                rate = v.average_rate or v.base_rate
                info["fps"] = round(float(rate), 3) if rate else None
                if not info["duration"] and v.duration and v.time_base:
                    info["duration"] = float(v.duration * v.time_base)
            if c.streams.audio:
                info["channels"] = c.streams.audio[0].codec_context.channels or 0
    except Exception as e:
        log.exception("Ανάγνωση βίντεο %s", path)
        info["error"] = str(e)
    return info


def audio_level(path, cancel=None):
    """(μέση στάθμη RMS dBFS, μέγιστη στάθμη dBFS) όλου του ήχου, ή (None, None) αν δεν έχει ήχο."""
    import av
    import numpy as np
    sumsq, n, peak = 0.0, 0, 0.0
    with av.open(path) as c:
        if not c.streams.audio:
            return None, None
        st = c.streams.audio[0]
        for fr in c.decode(st):
            check_cancel(cancel)
            a = frame_to_float(fr).astype(np.float64)
            sumsq += float((a ** 2).sum())
            n += a.size
            peak = max(peak, float(np.abs(a).max()) if a.size else 0.0)
    if not n:
        return None, None
    db = lambda v: 20 * math.log10(max(v, 1e-9))
    return round(db(math.sqrt(sumsq / n)), 1), round(db(peak), 1)


def equalize_gains(levels, max_boost=10.0, headroom=-1.0):
    """
    Κέρδος (dB) ανά αρχείο ώστε όλα να έχουν περίπου την ίδια μέση στάθμη (τη διάμεσο), χωρίς να ξεπερνά
    η κορυφή το headroom. levels: [(rms_db, peak_db) ή (None, None)].
    """
    vals = sorted(r for r, _p in levels if r is not None)
    if len(vals) < 2:
        return [0.0] * len(levels)
    target = vals[len(vals) // 2]
    out = []
    for rms, peak in levels:
        if rms is None:
            out.append(0.0)
            continue
        g = max(-max_boost, min(max_boost, target - rms))
        if peak is not None and peak + g > headroom:
            g = headroom - peak
        out.append(round(g, 1))
    return out
