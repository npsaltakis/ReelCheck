"""Έξοδος ήχου (sounddevice) και downmix."""
import queue

from app.core.config import log

END = object()  # τέλος ροής


def downmix(x):
    """x: (n, ch) float32 -> (n, 2). Σειρά: L R C LFE Ls Rs ..."""
    import numpy as np
    ch = x.shape[1]
    if ch == 1:
        return np.repeat(x, 2, axis=1)
    if ch == 2:
        return x
    L, R = x[:, 0], x[:, 1]
    C = x[:, 2] if ch > 2 else 0
    Ls = x[:, 4] if ch > 5 else 0
    Rs = x[:, 5] if ch > 5 else 0
    out = np.empty((x.shape[0], 2), np.float32)
    out[:, 0] = L + 0.707 * C + 0.707 * Ls
    out[:, 1] = R + 0.707 * C + 0.707 * Rs
    out *= 0.7
    np.clip(out, -1, 1, out=out)
    return out


def put(q, item, stop):
    while not stop.is_set():
        try:
            q.put(item, timeout=0.1)
            return True
        except queue.Full:
            pass
    return False


def _outputs():
    """[(δείκτης, όνομα)] των συσκευών εξόδου, π.χ. (5, «Speakers (Realtek) [Windows WASAPI]»)."""
    try:
        import sounddevice as sd
        apis = sd.query_hostapis()
        return [(i, f"{d['name']} [{apis[d['hostapi']]['name']}]") for i, d in enumerate(sd.query_devices())
                if d["max_output_channels"] > 0]
    except Exception:  # χωρίς κάρτα ήχου / χαλασμένος driver: η αναπαραγωγή συνεχίζει χωρίς ήχο
        log.exception("Λίστα συσκευών ήχου")
        return []


def output_devices():
    """Ονόματα συσκευών εξόδου ήχου (για τις Ρυθμίσεις)."""
    return [name for _i, name in _outputs()]


def device_index(label):
    """Δείκτης συσκευής από το όνομα των Ρυθμίσεων· None αν είναι κενό ή δεν υπάρχει πια (π.χ. αποσυνδέθηκε)."""
    if not label:
        return None
    return next((i for i, name in _outputs() if name == label), None)


class AudioOut:
    """
    Ήχος μέσω sounddevice. Στην ουρά μπαίνουν πολυκάναλα κομμάτια (n, ch) και η μίξη
    γίνεται εδώ (όλα / solo καναλιού). Το ρολόι βγαίνει από τα δείγματα που παίχτηκαν.
    """

    def __init__(self, sr, nch, device=None, volume=1.0):
        """device: δείκτης συσκευής του sounddevice (None = προεπιλογή των Windows). volume: 0..1."""
        import numpy as np
        import sounddevice as sd
        self.np = np
        self.sr, self.nch = sr, nch
        self.volume = max(0.0, min(1.0, float(volume)))
        self.q = queue.Queue(maxsize=200)
        self.buf = None
        self.pos = 0
        self.played = 0
        self.paused = False
        self.ended = False
        self.muted = False
        self.solo = None             # None = όλα, αλλιώς δείκτης καναλιού
        self.peaks = np.zeros(nch, np.float32)
        try:
            self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32", device=device,
                                          callback=self._cb, latency="high")
        except Exception:
            if device is None:
                raise
            log.exception("Η συσκευή ήχου %s δεν άνοιξε, χρήση της προεπιλεγμένης", device)
            self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32",
                                          callback=self._cb, latency="high")
        self.latency = self.stream.latency
        self.stream.start()

    def _cb(self, out, frames, _t, _s):
        np = self.np
        if self.paused:
            out.fill(0)
            return
        block = np.zeros((frames, self.nch), np.float32)
        filled = 0
        while filled < frames:
            if self.buf is None or self.pos >= len(self.buf):
                try:
                    b = self.q.get_nowait()
                except queue.Empty:
                    break
                if b is END:
                    self.ended = True
                    self.buf = None
                    break
                self.buf, self.pos = b, 0
            n = min(frames - filled, len(self.buf) - self.pos)
            block[filled:filled + n] = self.buf[self.pos:self.pos + n]
            self.pos += n
            filled += n
        if filled:
            self.peaks = np.maximum(self.peaks, np.abs(block[:filled]).max(axis=0))
        if self.muted:
            out.fill(0)
        elif self.solo is not None and self.solo < self.nch:
            out[:, 0] = block[:, self.solo]
            out[:, 1] = block[:, self.solo]
        else:
            out[:] = downmix(block)
        if self.volume < 0.999 and not self.muted:
            out *= self.volume
        self.played += filled

    def take_peaks(self):
        p = self.peaks
        self.peaks = self.np.zeros(self.nch, self.np.float32)
        return p

    def seconds(self):
        return max(0.0, self.played / self.sr - self.latency)

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
