"""
Ένταση κατά EBU R128 (LUFS) με το φίλτρο ebur128 του FFmpeg (μέσα στο PyAV):
integrated (όλο το πρόγραμμα), LRA (εύρος), true peak (dBTP), και momentary / short-term για ζωντανή ένδειξη.
Στο 5.1 το LFE δεν μετράει, όπως ορίζει το πρότυπο.
"""
import math
from fractions import Fraction

from app.core.avio import fit_channels, frame_to_float
from app.core.util import check_cancel

LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}


def _db(lin):
    try:
        v = float(lin)
    except (TypeError, ValueError):
        return None
    return round(20 * math.log10(v), 1) if v > 0 else None


def _num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return None if v <= -70 or math.isinf(v) else round(v, 1)  # κάτω από την πύλη = σιωπή


class LoudnessMeter:
    """Δέχεται κομμάτια ήχου numpy (n, κανάλια) float32 και μετρά LUFS."""

    def __init__(self, channels, sample_rate=48000):
        import av
        self.av = av
        self.rate = sample_rate
        self.nch = channels if channels in LAYOUTS else (2 if channels < 6 else 6)
        self.layout = LAYOUTS[self.nch]
        self.samples = 0
        self.meta = {}
        g = av.filter.Graph()
        src = g.add_abuffer(sample_rate=sample_rate, format="fltp", layout=self.layout,
                            time_base=Fraction(1, sample_rate))
        g.link_nodes(src, g.add("ebur128", "metadata=1:peak=true"), g.add("abuffersink")).configure()
        self.graph = g

    def add(self, a):
        """a: numpy (n, κανάλια) float32 σε [-1, 1]."""
        import numpy as np
        if not len(a):
            return
        data = np.ascontiguousarray(fit_channels(a, self.nch).T.astype(np.float32))
        f = self.av.AudioFrame.from_ndarray(data, format="fltp", layout=self.layout)
        f.sample_rate = self.rate
        f.pts, f.time_base = self.samples, Fraction(1, self.rate)
        self.samples += len(a)
        self.graph.push(f)
        self._drain()

    def add_frame(self, frame):
        """Κατευθείαν av.AudioFrame (μετατρέπεται σε float)."""
        self.add(frame_to_float(frame))

    def _drain(self):
        while True:
            try:
                out = self.graph.pull()
            except (self.av.BlockingIOError, self.av.EOFError, BlockingIOError, EOFError):
                return
            if out.metadata:
                self.meta = dict(out.metadata)

    @property
    def momentary(self):
        return _num(self.meta.get("lavfi.r128.M"))

    @property
    def short_term(self):
        return _num(self.meta.get("lavfi.r128.S"))

    def result(self):
        """{'integrated', 'lra', 'true_peak'} (LUFS, LU, dBTP) ή None όπου δεν μετρήθηκε."""
        m = self.meta
        return {"integrated": _num(m.get("lavfi.r128.I")),
                "lra": round(float(m["lavfi.r128.LRA"]), 1) if m.get("lavfi.r128.LRA") else None,
                "true_peak": _db(m.get("lavfi.r128.true_peak"))}


def measure_file(path, cancel=None):
    """Integrated LUFS, LRA και true peak όλου του ήχου ενός αρχείου βίντεο, ή None αν δεν έχει ήχο."""
    import av
    with av.open(path) as c:
        if not c.streams.audio:
            return None
        st = c.streams.audio[0]
        meter = LoudnessMeter(st.codec_context.channels or 2, 48000)
        resampler = av.AudioResampler(format="fltp", layout=meter.layout, rate=48000)
        for fr in c.decode(st):
            check_cancel(cancel)
            for rf in resampler.resample(fr):
                meter.add_frame(rf)
        for rf in resampler.resample(None):
            meter.add_frame(rf)
    return meter.result()


def describe(r):
    """Κείμενο π.χ. «-23.1 LUFS, LRA 8.2 LU, true peak -1.5 dBTP»."""
    if not r or r.get("integrated") is None:
        return "ένταση: δεν μετρήθηκε (σιωπή;)"
    parts = [f"{r['integrated']:.1f} LUFS"]
    if r.get("lra") is not None:
        parts.append(f"LRA {r['lra']:.1f} LU")
    if r.get("true_peak") is not None:
        parts.append(f"true peak {r['true_peak']:.1f} dBTP")
    return "ένταση " + ", ".join(parts)
