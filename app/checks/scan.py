"""Σάρωση όλης της ταινίας: μαύρη εικόνα, σιωπή, κανάλια, clipping, υπότιτλοι."""
import datetime
import json
import math
import os
from fractions import Fraction

from app.core.config import APP_DIR, CH_NAMES, log
from app.core.hashing import Cancelled
from app.core.subtitles import load_tracks
from app.core.util import fmt_time


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


def scan_cpl(cpl, progress=None, cancel=None):
    """
    Σάρωση όλης της ταινίας χωρίς προβολή: μαύρα καρέ (δείγμα κάθε λίγα δευτ.), σιωπή, κανάλια χωρίς σήμα,
    clipping, μέση στάθμη, υπότιτλοι εκτός διάρκειας. Αποθηκεύεται στο %APPDATA%\\ReelCheck\\scans.
    """
    import av
    import numpy as np
    total = cpl.seconds
    res = {"version": 1, "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), "seconds": total}

    def tick(frac, msg):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress(frac, msg)

    # --- εικόνα: μαύρα καρέ
    step = max(2.0, total / 3000.0)
    flags = []
    for reel in cpl.reels:
        if not reel.pic:
            continue
        with av.open(reel.pic) as c:
            vs = c.streams.video[0]
            if vs.codec_context.name == "jpeg2000":
                vs.codec_context.options = {"lowres": "2"}
            mult = 2 if reel.stereo else 1
            st0 = vs.start_time or 0
            intra = vs.codec_context.name in ("jpeg2000", "prores", "dnxhd", "mjpeg")
            t = 0.0
            while t < reel.seconds:
                tick(0.6 * (reel.start + t) / max(1, total), f"Σάρωση εικόνας {fmt_time(reel.start + t)}")
                frame_no = (reel.pic_entry + int(t * float(reel.rate))) * mult
                target = Fraction(frame_no) / (reel.rate * mult)
                try:
                    c.seek(int(target / vs.time_base) + st0, stream=vs, backward=True)
                except Exception:
                    pass
                mean = None
                for fr in c.decode(vs):
                    if not intra and fr.time is not None and fr.time - float(st0 * vs.time_base) + 0.001 < float(target):
                        continue
                    a = fr.reformat(width=96, height=max(2, int(96 * fr.height / fr.width)) // 2 * 2,
                                    format="rgb24").to_ndarray()
                    mean, peak = float(a.mean()), float(np.percentile(a, 99))
                    break
                if mean is not None:
                    flags.append((reel.start + t, mean < 6 and peak < 30))
                t += step
    black = _runs(flags, step, 2 * step)
    res["black"] = black
    res["pic_step"] = step

    # --- ήχος
    peaks, clip, sec_rms, nch, sr = None, None, [], 0, 48000
    sumsq_all = None
    n_all = 0
    for reel in cpl.reels:
        if not reel.snd:
            continue
        with av.open(reel.snd) as c:
            st = c.streams.audio[0]
            sr = st.codec_context.sample_rate or 48000
            start_s = float(reel.snd_entry / reel.snd_rate)
            need = int(round(float(reel.snd_dur / reel.snd_rate) * sr))
            if start_s > 0:
                try:
                    c.seek(int(start_s / st.time_base) + (st.start_time or 0), stream=st, backward=True)
                except Exception:
                    pass
            a0 = float((st.start_time or 0) * st.time_base) if st.time_base else 0.0
            start_sample = int(round(start_s * sr))
            acc, acc_n, pos = None, 0, None
            done = 0
            for fr in c.decode(st):
                if need <= 0:
                    break
                a = fr.to_ndarray()
                ch = fr.layout.nb_channels if hasattr(fr.layout, "nb_channels") else len(fr.layout.channels)
                a = a.T if fr.format.is_planar else a.reshape(-1, ch)
                if a.dtype == np.int32:
                    a = a.astype(np.float32) / 2147483648.0
                elif a.dtype == np.int16:
                    a = a.astype(np.float32) / 32768.0
                else:
                    a = a.astype(np.float32)
                fs = int(round((fr.time - a0) * sr)) if fr.time is not None else (pos or start_sample)
                pos = fs + len(a)
                if pos <= start_sample:
                    continue
                if fs < start_sample:
                    a = a[start_sample - fs:]
                a = a[:need]
                need -= len(a)
                if peaks is None:
                    nch = a.shape[1]
                    peaks = np.zeros(nch, np.float32)
                    clip = np.zeros(nch, np.int64)
                    sumsq_all = np.zeros(nch, np.float64)
                if a.shape[1] != nch:
                    b = np.zeros((len(a), nch), np.float32)
                    k = min(nch, a.shape[1])
                    b[:, :k] = a[:, :k]
                    a = b
                ab = np.abs(a)
                peaks = np.maximum(peaks, ab.max(axis=0))
                clip += (ab >= 0.9999).sum(axis=0)
                sq = (a.astype(np.float64) ** 2)
                sumsq_all += sq.sum(axis=0)
                n_all += len(a)
                # RMS ανά δευτερόλεπτο (όλα τα κανάλια)
                i = 0
                while i < len(sq):
                    take = min(len(sq) - i, sr - acc_n)
                    s = sq[i:i + take].sum(axis=0)
                    acc = s if acc is None else acc + s
                    acc_n += take
                    i += take
                    if acc_n >= sr:
                        sec_rms.append(float(np.sqrt(acc.max() / acc_n)))
                        acc, acc_n = None, 0
                done += len(a)
                if done >= sr * 20:
                    done = 0
                    tick(0.6 + 0.38 * min(1.0, (len(sec_rms)) / max(1, total)),
                         f"Σάρωση ήχου {fmt_time(len(sec_rms))}")
            if acc is not None and acc_n:
                sec_rms.append(float(np.sqrt(acc.max() / acc_n)))
    if peaks is not None:
        db = lambda v: round(20 * math.log10(max(float(v), 1e-9)), 1)
        res["nch"] = nch
        res["peak_db"] = [db(p) for p in peaks]
        res["clip"] = [int(x) for x in clip]
        main = [i for i in range(min(nch, 6)) if i != 3]  # L R C Ls Rs (χωρίς LFE)
        res["dead"] = [CH_NAMES[i] for i in main if peaks[i] < 10 ** (-70 / 20)]
        res["lfe_empty"] = nch > 3 and bool(peaks[3] < 10 ** (-70 / 20))
        pw = [sumsq_all[i] / max(1, n_all) for i in main] or [0]
        res["level_db"] = db(math.sqrt(sum(pw)))
        silent = [(float(i), v < 10 ** (-60 / 20)) for i, v in enumerate(sec_rms)]
        res["silence"] = _runs(silent, 1.0, 10.0)
        res["audio_seconds"] = len(sec_rms)

    # --- υπότιτλοι
    tick(0.99, "Έλεγχος υποτίτλων")
    out_of_range = short = 0
    for t in load_tracks(cpl):
        for e in t["entries"]:
            if e[0] < -0.01 or e[1] > total + 0.5:
                out_of_range += 1
            elif e[1] - e[0] < 0.25:
                short += 1
    res["subs_out"], res["subs_short"] = out_of_range, short
    res["issues"] = scan_findings(res)
    try:
        os.makedirs(SCANS_DIR, exist_ok=True)
        with open(_scan_path(cpl), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    except OSError:
        pass
    log.info("Σάρωση %s: %s", cpl.title, res["issues"])
    return res


def scan_findings(res):
    """[(err|warn|info, κείμενο)] από το αποτέλεσμα της σάρωσης."""
    out, total = [], res.get("seconds", 0)
    ft = fmt_time
    mid_black = [(a, b) for a, b in res.get("black", []) if a > 300 and b < total - 600]
    edge_black = [(a, b) for a, b in res.get("black", []) if (a, b) not in mid_black]
    for a, b in mid_black[:5]:
        out.append(("warn", f"μαύρη εικόνα {ft(a)}–{ft(b)} ({b - a:.0f} δευτ.)"))
    if len(mid_black) > 5:
        out.append(("warn", f"και άλλα {len(mid_black) - 5} σημεία με μαύρη εικόνα"))
    if edge_black:
        out.append(("info", "μαύρο στην αρχή/τέλος (τίτλοι;): " + ", ".join(f"{ft(a)}–{ft(b)}" for a, b in edge_black[:3])))
    if "peak_db" in res:
        mid_sil = [(a, b) for a, b in res.get("silence", []) if b - a >= 10 and a > 300 and b < total - 600]
        for a, b in mid_sil[:5]:
            out.append(("warn", f"σιωπή {ft(a)}–{ft(b)} ({b - a:.0f} δευτ.)"))
        if len(mid_sil) > 5:
            out.append(("warn", f"και άλλα {len(mid_sil) - 5} σημεία με σιωπή"))
        if res.get("dead"):
            out.append(("warn", f"κανάλια χωρίς σήμα σε όλη την ταινία: {', '.join(res['dead'])}"))
        if res.get("lfe_empty"):
            out.append(("info", "το LFE (subwoofer) δεν έχει σήμα"))
        clipped = [(CH_NAMES[i] if i < len(CH_NAMES) else str(i + 1), n)
                   for i, n in enumerate(res.get("clip", [])) if n > 100]
        if clipped:
            out.append(("warn", "clipping (ψαλίδισμα): " + ", ".join(f"{c} {n}" for c, n in clipped)))
        mx = max(res["peak_db"][:6] or [-99])
        out.append(("info", f"μέγιστη στάθμη {mx:.1f} dBFS, μέση {res.get('level_db', 0):.1f} dBFS (RMS)"))
        if res.get("audio_seconds") and abs(res["audio_seconds"] - total) > 2:
            out.append(("warn", f"ο ήχος κρατά {ft(res['audio_seconds'])}, η εικόνα {ft(total)}"))
    if res.get("subs_out"):
        out.append(("warn", f"{res['subs_out']} υπότιτλοι εκτός διάρκειας ταινίας"))
    if res.get("subs_short"):
        out.append(("info", f"{res['subs_short']} υπότιτλοι διαρκούν λιγότερο από 0,25 δευτ."))
    if not out:
        out.append(("info", "σάρωση: κανένα πρόβλημα"))
    return out
