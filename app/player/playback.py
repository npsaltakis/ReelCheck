"""Αποκωδικοποίηση εικόνας και ήχου σε threads."""
import queue
import threading
import time
from fractions import Fraction

from app.core.config import log
from app.core.util import fmt_time
from app.player.audio import AudioOut, END, put


INTRA_CODECS = {"jpeg2000", "prores", "dnxhd", "mjpeg", "ffv1", "huffyuv", "cfhd", "rawvideo", "v210", "png", "tiff"}


class LoudnessTrack:
    """
    Ζωντανή ένταση (LUFS) για την αναπαραγωγή. Μετριέται στο thread του ήχου, που τρέχει λίγο μπροστά
    από ό,τι ακούγεται, οπότε κρατάμε ιστορικό (χρόνος, momentary, short-term) και δείχνουμε την τιμή
    της στιγμής που ακούγεται.
    """

    MAX_HISTORY = 20000

    def __init__(self, t0, sr, nch):
        from app.checks.loudness import LoudnessMeter
        self.meter = LoudnessMeter(6 if nch >= 6 else nch, sr)
        self.t0, self.sr, self.done = t0, sr, 0
        self.times, self.values = [], []

    def add(self, a):
        self.meter.add(a)
        self.done += len(a)
        if len(self.times) > self.MAX_HISTORY:
            del self.times[:self.MAX_HISTORY // 2]
            del self.values[:self.MAX_HISTORY // 2]
        self.values.append((self.meter.momentary, self.meter.short_term))
        self.times.append(self.t0 + self.done / self.sr)

    def at(self, t):
        """(momentary, short-term) τη στιγμή t, ή (None, None)."""
        import bisect
        i = bisect.bisect_left(self.times, t)
        if i >= len(self.values):
            i = len(self.values) - 1
        return self.values[i] if i >= 0 else (None, None)

    @property
    def integrated(self):
        return self.meter.result()["integrated"]


class Playback:
    """Μία «διαδρομή» αναπαραγωγής από τη στιγμή t0. Για seek φτιάχνεται καινούρια."""

    def __init__(self, cpl, t0, lowres, size_fn, eye=0, want_audio=True):
        self.cpl, self.t0, self.lowres, self.size_fn, self.eye = cpl, t0, lowres, size_fn, eye
        self.stop = threading.Event()
        self.vq = queue.Queue(maxsize=6)
        self.paused = False
        self.started = False
        self.audio = None
        self.error = None
        self.dropped = 0
        self.want_pause = False
        self.loudness = None  # LoudnessTrack, όταν ξεκινήσει ο ήχος
        sr, nch = 48000, 2
        if want_audio:
            try:
                import av
                r = next((r for r in cpl.reels if r.snd), None)
                if r:
                    with av.open(r.snd) as c:
                        a = c.streams.audio[0].codec_context
                        sr, nch = a.sample_rate or 48000, max(1, min(16, a.channels or 2))
                self.audio = AudioOut(sr, nch)
            except Exception:
                log.exception("Δεν άνοιξε η έξοδος ήχου, συνέχεια χωρίς ήχο")
                self.audio = None
        log.info("Αναπαραγωγή από %s, lowres=%s, ήχος=%s", fmt_time(t0), lowres,
                 f"{sr} Hz {nch}ch" if self.audio else "όχι")
        self._wall0 = None
        self._wall_acc = 0.0
        self.threads = [threading.Thread(target=self._video, daemon=True, name="video")]
        if self.audio:
            self.threads.append(threading.Thread(target=self._audio_worker, args=(sr, nch), daemon=True,
                                                 name="audio"))
        for t in self.threads:
            t.start()

    # --- ρολόι
    def clock(self):
        if self.audio and not self.audio.ended:
            return self.t0 + self.audio.seconds()
        if self._wall0 is None:  # χωρίς ήχο / μετά το τέλος του ήχου: ρολόι τοίχου
            if not self.started:
                return self.t0
            self._wall_acc = self.audio.seconds() if self.audio else 0.0
            self._wall0 = time.perf_counter()
        if self.paused:
            return self.t0 + self._wall_acc
        return self.t0 + self._wall_acc + (time.perf_counter() - self._wall0)

    def set_paused(self, p):
        if p == self.paused:
            return
        if self._wall0 is not None:
            if p:
                self._wall_acc += time.perf_counter() - self._wall0
            else:
                self._wall0 = time.perf_counter()
        self.paused = p
        if self.audio:
            self.audio.paused = p

    def close(self):
        if self.dropped:
            log.warning("Πεταμένα καρέ (αργή αποκωδικοποίηση): %d", self.dropped)
        self.stop.set()
        for q in (self.vq, self.audio.q if self.audio else None):
            if q:
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
        for t in self.threads:
            t.join(timeout=2)
        if self.audio:
            self.audio.close()

    # --- εικόνα
    def _video(self):
        try:
            self._video_inner()
        except Exception as e:
            log.exception("Σφάλμα αποκωδικοποίησης εικόνας")
            self.error = f"Εικόνα: {e}"
        put(self.vq, END, self.stop)

    def _video_inner(self):
        import av
        for reel in self.cpl.reels:
            if self.stop.is_set():
                return
            if reel.start + reel.seconds <= self.t0 or not reel.pic:
                continue
            mult = 2 if reel.stereo else 1
            off = max(0, int(round((self.t0 - reel.start) * reel.rate)))
            first, last = (reel.pic_entry + off) * mult, (reel.pic_entry + reel.dur) * mult
            with av.open(reel.pic) as c:
                vs = c.streams.video[0]
                vs.thread_type = "AUTO"
                cc = vs.codec_context
                intra = cc.name in INTRA_CODECS
                if self.lowres and cc.name == "jpeg2000":
                    cc.options = {"lowres": str(self.lowres)}
                tb = vs.time_base
                st0 = vs.start_time or 0
                frate = reel.rate * mult
                if first > 0:
                    try:
                        c.seek(int(Fraction(first) / frate / tb) + st0, stream=vs, backward=True)
                    except Exception:
                        pass
                counter = [first - 1]

                def emit(fr):
                    # ο decoder με threads βγάζει καρέ με καθυστέρηση: ο χρόνος παίρνεται από το ίδιο το καρέ
                    if fr.pts is not None:
                        idx = int(round((fr.pts - st0) * tb * frate))
                    else:
                        counter[0] += 1
                        idx = counter[0]
                    if idx < first or idx >= last or (mult == 2 and idx % 2 != self.eye):
                        return True
                    t = reel.start + (idx // mult - reel.pic_entry) / float(reel.rate)
                    if not intra and self.started and not self.paused and t < self.clock() - 0.08:
                        self.dropped += 1  # inter codec: αποκωδικοποιείται αλλά δεν εμφανίζεται
                        return True
                    W, H = self.size_fn()
                    s = min(W / fr.width, H / fr.height)
                    w, h = max(2, int(fr.width * s) // 2 * 2), max(2, int(fr.height * s) // 2 * 2)
                    img = fr.reformat(width=w, height=h, format="rgb24")
                    return put(self.vq, (t, img.to_ndarray().tobytes(), w, h, fr.width, fr.height), self.stop)

                for pkt in c.demux(vs):
                    if self.stop.is_set():
                        return
                    if pkt.size == 0:
                        continue
                    ts = pkt.pts if pkt.pts is not None else pkt.dts
                    if intra and ts is not None:
                        idx = int(round((ts - st0) * tb * frate))
                        if idx < first:
                            continue
                        if idx >= last:
                            break
                        t = reel.start + (idx // mult - reel.pic_entry) / float(reel.rate)
                        # όλα intra: αν αργούμε, πετάμε πακέτα χωρίς αποκωδικοποίηση
                        if self.started and not self.paused and t < self.clock() - 0.08:
                            self.dropped += 1
                            continue
                    elif ts is not None and int(round((ts - st0) * tb * frate)) >= last + 32:
                        break
                    for fr in cc.decode(pkt):
                        if not emit(fr):
                            return
                for fr in cc.decode(None):  # άδειασμα decoder στο τέλος του reel
                    if not emit(fr):
                        return

    # --- ήχος
    def _audio_worker(self, sr, nch):
        try:
            self._audio_inner(sr, nch)
        except Exception as e:
            log.exception("Σφάλμα αποκωδικοποίησης ήχου")
            self.error = f"Ήχος: {e}"
        put(self.audio.q, END, self.stop)

    def _audio_inner(self, sr, nch):
        import av
        import numpy as np

        try:
            self.loudness = LoudnessTrack(self.t0, sr, nch)
        except Exception:
            log.exception("Μετρητής LUFS")
            self.loudness = None

        def send(a):
            if self.loudness is not None:
                self.loudness.add(a)
            return put(self.audio.q, a, self.stop)

        def silence(n):
            while n > 0 and not self.stop.is_set():
                k = min(n, 4800)
                send(np.zeros((k, nch), np.float32))
                n -= k

        for reel in self.cpl.reels:
            if self.stop.is_set():
                return
            if reel.start + reel.seconds <= self.t0:
                continue
            skip = max(0.0, self.t0 - reel.start)
            need = int(round((reel.seconds - skip) * sr))  # δείγματα που πρέπει να βγουν
            if not reel.snd:
                silence(need)  # reel χωρίς ήχο
                continue
            start_s = float(reel.snd_entry / reel.snd_rate) + skip
            with av.open(reel.snd) as c:
                st = c.streams.audio[0]
                a0 = float((st.start_time or 0) * st.time_base) if st.time_base else 0.0
                if start_s > 0:
                    try:
                        c.seek(int(start_s / st.time_base) + (st.start_time or 0), stream=st, backward=True)
                    except Exception:
                        pass
                start_sample = int(round(start_s * sr))
                pos = None
                for fr in c.decode(st):
                    if self.stop.is_set() or need <= 0:
                        break
                    a = fr.to_ndarray()
                    ch = fr.layout.nb_channels if hasattr(fr.layout, "nb_channels") else len(fr.layout.channels)
                    a = a.T if fr.format.is_planar else a.reshape(-1, ch)
                    if a.dtype == np.int32:
                        a = a.astype(np.float32) / 2147483648.0
                    elif a.dtype == np.int16:
                        a = a.astype(np.float32) / 32768.0
                    elif a.dtype == np.uint8:
                        a = (a.astype(np.float32) - 128) / 128.0
                    else:
                        a = a.astype(np.float32)
                    if ch != nch:  # ίδιος αριθμός καναλιών σε όλα τα reels
                        b = np.zeros((len(a), nch), np.float32)
                        k = min(ch, nch)
                        b[:, :k] = a[:, :k]
                        a = b
                    if fr.time is not None:
                        fs = int(round((fr.time - a0) * sr))
                    else:
                        fs = pos if pos is not None else start_sample
                    pos = fs + len(a)
                    if pos <= start_sample:
                        continue
                    if fs < start_sample:
                        a = a[start_sample - fs:]
                    a = a[:need]
                    need -= len(a)
                    if not send(np.ascontiguousarray(a)):
                        return
            silence(need)  # ήχος κοντύτερος από την εικόνα
