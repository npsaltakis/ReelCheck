"""Αποκωδικοποίηση εικόνας και ήχου σε threads."""
import queue
import threading
import time

from app.core.config import log
from app.core.util import fmt_time
from app.core import prefs
from app.core.avio import ReelPos, reel_audio, seek_seconds
from app.player.audio import AudioOut, END, device_index, put


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
                self.audio = AudioOut(sr, nch, device_index(prefs.get("audio_device")),
                                      prefs.get("start_volume") / 100)
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
        for reel in self.cpl.reels:
            if self.stop.is_set():
                return
            if reel.start + reel.seconds <= self.t0 or not reel.pic:
                continue
            if not self._video_reel(reel):
                return

    def _late(self, t):
        """Η εικόνα της στιγμής t έχει ήδη περάσει (αργεί η αποκωδικοποίηση)."""
        return self.started and not self.paused and t < self.clock() - 0.08

    def _video_reel(self, reel):
        """Τα καρέ ενός reel στην ουρά. False αν πρέπει να σταματήσει η αναπαραγωγή."""
        import av
        off = max(0, int(round((self.t0 - reel.start) * reel.rate)))
        with av.open(reel.pic) as c:
            vs = c.streams.video[0]
            vs.thread_type = "AUTO"
            cc = vs.codec_context
            if self.lowres and cc.name == "jpeg2000":
                cc.options = {"lowres": str(self.lowres)}
            pos = ReelPos(reel, vs, off)
            seek_seconds(c, vs, pos.first / pos.frate)
            intra = cc.name in INTRA_CODECS
            for pkt in c.demux(vs):
                if self.stop.is_set():
                    return False
                if pkt.size == 0:
                    continue
                ts = pkt.pts if pkt.pts is not None else pkt.dts
                if intra and ts is not None:
                    idx = pos.index(ts)
                    if idx < pos.first:
                        continue
                    if idx >= pos.last:
                        break
                    if self._late(pos.time(idx)):  # όλα intra: αν αργούμε, πετάμε πακέτα χωρίς αποκωδικοποίηση
                        self.dropped += 1
                        continue
                elif ts is not None and pos.index(ts) >= pos.last + 32:
                    break
                for fr in cc.decode(pkt):
                    if not self._emit(fr, pos, intra):
                        return False
            for fr in cc.decode(None):  # άδειασμα decoder στο τέλος του reel
                if not self._emit(fr, pos, intra):
                    return False
        return True

    def _emit(self, fr, pos, intra):
        """Ένα αποκωδικοποιημένο καρέ στην ουρά (αν ανήκει στο reel και στο σωστό μάτι). False = τέλος."""
        # ο decoder με threads βγάζει καρέ με καθυστέρηση: ο χρόνος παίρνεται από το ίδιο το καρέ
        idx = pos.index(fr.pts) if fr.pts is not None else pos.next_counter()
        if idx < pos.first or idx >= pos.last or (pos.mult == 2 and idx % 2 != self.eye):
            return True
        t = pos.time(idx)
        if not intra and self._late(t):
            self.dropped += 1  # inter codec: αποκωδικοποιείται αλλά δεν εμφανίζεται
            return True
        W, H = self.size_fn()
        s = min(W / fr.width, H / fr.height)
        w, h = max(2, int(fr.width * s) // 2 * 2), max(2, int(fr.height * s) // 2 * 2)
        img = fr.reformat(width=w, height=h, format="rgb24")
        return put(self.vq, (t, img.to_ndarray().tobytes(), w, h, fr.width, fr.height), self.stop)

    # --- ήχος
    def _audio_worker(self, sr, nch):
        try:
            self._audio_inner(sr, nch)
        except Exception as e:
            log.exception("Σφάλμα αποκωδικοποίησης ήχου")
            self.error = f"Ήχος: {e}"
        put(self.audio.q, END, self.stop)

    def _audio_inner(self, sr, nch):
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
            need = int(round((reel.seconds - skip) * sr))  # δείγματα που πρέπει να βγουν (όσο η εικόνα)
            # ίδιος αριθμός καναλιών σε όλα τα reels· reel χωρίς ήχο δεν δίνει τίποτα και γεμίζει με σιωπή
            for a in reel_audio(reel, sr, skip, limit=reel.seconds - skip, channels=nch, stop=self.stop):
                need -= len(a)
                if not send(np.ascontiguousarray(a)):
                    return
            silence(need)  # ήχος κοντύτερος από την εικόνα
