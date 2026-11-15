"""
Παράθυρο αναπαραγωγής (pygame). Εικόνα με PyAV/FFmpeg, ήχος με sounddevice.

Πλήκτρα:
  Space παύση | ← → ±10s (Shift ±60s) | , . καρέ-καρέ | PgUp/PgDn reel | Home αρχή
  F πλήρης οθόνη | H μισή ανάλυση | S υπότιτλοι on/off | L αλλαγή υποτίτλων
  0 όλα τα κανάλια | 1-9 solo καναλιού | A μετρητές ήχου (αυτόματα / πάντα / κρυφοί) | M σίγαση
  N νέα σημείωση | J / Shift+J επόμενη/προηγ. σημείωση | Ctrl+Z σβήσιμο τελευταίας σημείωσης
  P screenshot | E αριστερό/δεξί μάτι (3D) | Esc / Q έξοδος
"""
import io
import os
import queue
import sys
import threading
import time
from fractions import Fraction

from dcp_core import (CH_NAMES, SHOTS_DIR, fmt_time, load_notes, load_target, load_tracks, log, probe,
                      resource, save_notes, sibling_tracks)

END = object()  # τέλος ροής
INTRA_CODECS = {"jpeg2000", "prores", "dnxhd", "mjpeg", "ffv1", "huffyuv", "cfhd", "rawvideo", "v210", "png", "tiff"}


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


class AudioOut:
    """
    Ήχος μέσω sounddevice. Στην ουρά μπαίνουν πολυκάναλα κομμάτια (n, ch) και η μίξη
    γίνεται εδώ (όλα / solo καναλιού). Το ρολόι βγαίνει από τα δείγματα που παίχτηκαν.
    """

    def __init__(self, sr, nch):
        import numpy as np
        import sounddevice as sd
        self.np = np
        self.sr, self.nch = sr, nch
        self.q = queue.Queue(maxsize=200)
        self.buf = None
        self.pos = 0
        self.played = 0
        self.paused = False
        self.ended = False
        self.muted = False
        self.solo = None             # None = όλα, αλλιώς δείκτης καναλιού
        self.peaks = np.zeros(nch, np.float32)
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

        def silence(n):
            while n > 0 and not self.stop.is_set():
                k = min(n, 4800)
                put(self.audio.q, np.zeros((k, nch), np.float32), self.stop)
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
                    if not put(self.audio.q, np.ascontiguousarray(a), self.stop):
                        return
            silence(need)  # ήχος κοντύτερος από την εικόνα


# ======================================================================== ΠΑΡΑΘΥΡΟ
def play(target, start=0.0, lowres=None, fullscreen=False):
    import pygame
    cpl = load_target(target)
    if not cpl:
        log.error("Δεν βρέθηκε CPL/αρχείο: %s", target)
        sys.exit(2)
    if cpl.encrypted:
        log.error("Κρυπτογραφημένο DCP (χρειάζεται KDM): %s", cpl.title)
        sys.exit(3)
    log.info("Άνοιγμα: %s | %d reels | %s | λείπουν: %s", cpl.title, len(cpl.reels), fmt_time(cpl.seconds),
             cpl.missing or "τίποτα")
    total = cpl.seconds
    rate = cpl.rate
    if lowres is None:
        info = probe(cpl)
        lowres = 1 if info.get("size", (0, 0))[0] > 2100 and info.get("codec") == "jpeg2000" else 0

    pygame.init()
    pygame.display.set_caption(f"ReelCheck - {cpl.title}")
    if resource("icon.png"):
        try:
            pygame.display.set_icon(pygame.image.load(resource("icon.png")))
        except Exception:
            pass
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    if fullscreen:
        pygame.display.toggle_fullscreen()
    font = pygame.font.SysFont("segoeui,arial", 18)
    small = pygame.font.SysFont("segoeui,arial", 14)
    tiny = pygame.font.SysFont("segoeui,arial", 11)
    win = [screen.get_width(), screen.get_height()]
    size_fn = lambda: (win[0], win[1])

    # υπότιτλοι: του CPL + άλλων CPL του φακέλου με ίδια διάρκεια
    tracks = load_tracks(cpl)
    try:
        tracks += sibling_tracks(cpl)
    except Exception:
        log.exception("Σφάλμα σε υπότιτλους άλλων CPL")
    track_i = 0 if tracks else -1
    show_subs = bool(tracks)
    for t in tracks:
        log.info("Υπότιτλοι: %s, %d εμφανίσεις %s", t["name"], len(t["entries"]), t["notes"])
    sub_cache, sub_fonts = {}, {}

    notes = load_notes(cpl)
    stereo = any(r.stereo for r in cpl.reels)
    state = {"muted": False, "eye": 0, "solo": None, "meters": "auto"}  # auto: με το OSD | on | off
    toast = ["", 0.0]

    def say(msg):
        toast[0], toast[1] = msg, time.time() + 2.5
        log.info(msg)

    def text_surface(line, italic, size):
        key = (line, italic, size)
        s = sub_cache.get(key)
        if s is None:
            f = sub_fonts.get((size, italic))
            if f is None:
                f = pygame.font.SysFont("arial,segoeui", size, bold=True, italic=italic)
                sub_fonts[(size, italic)] = f
            fg = f.render(line, True, (255, 255, 255))
            bg = f.render(line, True, (0, 0, 0))
            o = max(1, size // 14)
            s = pygame.Surface((fg.get_width() + 2 * o, fg.get_height() + 2 * o), pygame.SRCALPHA)
            for dx in (-o, 0, o):
                for dy in (-o, 0, o):
                    if dx or dy:
                        s.blit(bg, (o + dx, o + dy))
            s.blit(fg, (o, o))
            if len(sub_cache) > 300:
                sub_cache.clear()
            sub_cache[key] = s
        return s

    def image_surface(png, scale):
        key = (id(png), round(scale, 3))
        s = sub_cache.get(key)
        if s is None:
            s = pygame.image.load(io.BytesIO(png), "sub.png").convert_alpha()
            if abs(scale - 1) > 0.01:
                s = pygame.transform.smoothscale(s, (max(1, int(s.get_width() * scale)),
                                                     max(1, int(s.get_height() * scale))))
            sub_cache[key] = s
        return s

    def draw_subs(t, pic, src_w, bottom):
        if not (show_subs and 0 <= track_i < len(tracks)):
            return
        if bottom < pic.bottom:  # η μπάρα καλύπτει το κάτω μέρος: ανεβάζουμε τους υπότιτλους
            pic = pygame.Rect(pic.x, pic.y, pic.w, max(10, bottom - pic.y))
        active = [e for e in tracks[track_i]["entries"] if e[0] <= t < e[1]]
        if not active:
            return
        size = max(14, int(pic.h * 0.055))
        for _a, _b, items in active:
            texts = [it for it in items if it["type"] == "text"]
            for it in items:
                if it["type"] != "image":
                    continue
                s = image_surface(it["png"], pic.w / max(1, src_w))
                if it["halign"] == "left":
                    x = pic.x + int(pic.w * it["hpos"] / 100)
                elif it["halign"] == "right":
                    x = pic.right - int(pic.w * it["hpos"] / 100) - s.get_width()
                else:
                    x = pic.centerx - s.get_width() // 2 + int(pic.w * it["hpos"] / 100)
                if it["valign"] == "top":
                    y = pic.y + int(pic.h * it["vpos"] / 100)
                elif it["valign"] == "center":
                    y = pic.centery - s.get_height() // 2 + int(pic.h * it["vpos"] / 100)
                else:
                    y = pic.bottom - int(pic.h * it["vpos"] / 100) - s.get_height()
                screen.blit(s, (x, y))
            if texts:
                surfs = [text_surface(it["text"], it["italic"], size) for it in texts]
                top_aligned = all(it["y"] < 40 for it in texts)
                if top_aligned:
                    y = pic.y + int(pic.h * 0.06)
                else:
                    y = pic.bottom - int(pic.h * 0.06) - sum(s.get_height() for s in surfs)
                for s in surfs:
                    screen.blit(s, (pic.centerx - s.get_width() // 2, y))
                    y += s.get_height()

    def new_pb(t):
        t = min(max(0.0, t), max(0.0, total - 0.01))
        p = Playback(cpl, t, lowres, size_fn, eye=state["eye"])
        if p.audio:
            p.audio.muted = state["muted"]
            p.audio.solo = state["solo"]
        return p

    pb = new_pb(start)
    current = None
    pending = None
    video_done = False
    last_mouse = time.time()
    clock = pygame.time.Clock()
    bar_rect = pygame.Rect(0, 0, 0, 0)
    meter_vals = None
    note_mode = False
    note_text = ""
    want_shot = False

    def restart(t, paused=None):
        nonlocal pb, pending, video_done
        p = pb.paused if paused is None else paused
        pb.close()
        pending, video_done = None, False
        pb = new_pb(t)
        pb.want_pause = p

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                win[0], win[1] = max(320, ev.w), max(180, ev.h)
            elif ev.type == pygame.MOUSEMOTION:
                last_mouse = time.time()
            elif note_mode:
                if ev.type == pygame.TEXTINPUT:
                    note_text += ev.text
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_RETURN:
                        if note_text.strip():
                            nt = current[0] if current else pb.clock()
                            notes.append({"t": round(nt, 3), "text": note_text.strip()})
                            notes.sort(key=lambda n: n["t"])
                            save_notes(cpl, notes)
                            say(f"Σημείωση στο {fmt_time(nt, rate)}")
                        note_mode = False
                        pygame.key.stop_text_input()
                    elif ev.key == pygame.K_ESCAPE:
                        note_mode = False
                        pygame.key.stop_text_input()
                    elif ev.key == pygame.K_BACKSPACE:
                        note_text = note_text[:-1]
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if bar_rect.w and bar_rect.inflate(0, 16).collidepoint(ev.pos):
                    restart((ev.pos[0] - bar_rect.x) / bar_rect.w * total)
            elif ev.type == pygame.KEYDOWN:
                k, shift, ctrl = ev.key, ev.mod & pygame.KMOD_SHIFT, ev.mod & pygame.KMOD_CTRL
                now = current[0] if current else pb.clock()
                if k == pygame.K_ESCAPE:
                    if screen.get_flags() & pygame.FULLSCREEN:
                        pygame.display.toggle_fullscreen()
                    else:
                        running = False
                elif k == pygame.K_q:
                    running = False
                elif k == pygame.K_SPACE:
                    if video_done and pb.clock() >= total - 0.1:
                        restart(0, paused=False)
                    else:
                        pb.set_paused(not pb.paused)
                        pb.want_pause = pb.paused
                elif k in (pygame.K_LEFT, pygame.K_RIGHT):
                    d = (60 if shift else 10) * (1 if k == pygame.K_RIGHT else -1)
                    restart(now + d)
                elif k in (pygame.K_PERIOD, pygame.K_COMMA):
                    d = 1 / float(rate) * (1 if k == pygame.K_PERIOD else -1)
                    restart(now + d + 0.001, paused=True)
                elif k in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                    starts = [r.start for r in cpl.reels]
                    if k == pygame.K_PAGEDOWN:
                        nxt = [s for s in starts if s > now + 0.5]
                        if nxt:
                            restart(nxt[0])
                    else:
                        prv = [s for s in starts if s < now - 2]
                        restart(prv[-1] if prv else 0)
                elif k == pygame.K_HOME:
                    restart(0)
                elif k == pygame.K_f:
                    pygame.display.toggle_fullscreen()
                    win[0], win[1] = pygame.display.get_surface().get_size()
                elif k == pygame.K_h:
                    lowres = 0 if lowres else 1
                    say("Μισή ανάλυση" if lowres else "Πλήρης ανάλυση")
                    restart(now)
                elif k == pygame.K_s and tracks:
                    show_subs = not show_subs
                elif k == pygame.K_l and tracks:
                    track_i = (track_i + 1) % len(tracks)
                    show_subs = True
                    say(f"Υπότιτλοι: {tracks[track_i]['name']}")
                elif k == pygame.K_m:
                    state["muted"] = not state["muted"]
                    if pb.audio:
                        pb.audio.muted = state["muted"]
                elif k == pygame.K_a:
                    nxt = {"auto": "on", "on": "off", "off": "auto"}[state["meters"]]
                    state["meters"] = nxt
                    say({"auto": "Μετρητές ήχου: αυτόματα (με τα χειριστήρια)",
                         "on": "Μετρητές ήχου: πάντα", "off": "Μετρητές ήχου: κρυφοί"}[nxt])
                elif pygame.K_0 <= k <= pygame.K_9 or pygame.K_KP0 <= k <= pygame.K_KP9:
                    n = (k - pygame.K_0) if k <= pygame.K_9 else (k - pygame.K_KP0)
                    nch = pb.audio.nch if pb.audio else 0
                    if n == 0:
                        state["solo"] = None
                        say("Ήχος: όλα τα κανάλια")
                    elif n <= nch:
                        state["solo"] = n - 1
                        say(f"Ήχος: solo κανάλι {n} ({CH_NAMES[n - 1] if n <= len(CH_NAMES) else n})")
                    if pb.audio:
                        pb.audio.solo = state["solo"]
                    if n > 0 and state["meters"] == "auto":
                        state["meters"] = "on"
                elif k == pygame.K_n:
                    note_mode, note_text = True, ""
                    pb.set_paused(True)
                    pb.want_pause = True
                    pygame.key.start_text_input()
                elif k == pygame.K_z and ctrl and notes:
                    n = notes.pop()
                    save_notes(cpl, notes)
                    say(f"Σβήστηκε σημείωση: {n['text'][:40]}")
                elif k == pygame.K_j and notes:
                    if shift:
                        prv = [n for n in notes if n["t"] < now - 1]
                        target_n = prv[-1] if prv else notes[-1]
                    else:
                        nxt = [n for n in notes if n["t"] > now + 0.5]
                        target_n = nxt[0] if nxt else notes[0]
                    restart(max(0.0, target_n["t"]), paused=True)
                    say(f"{fmt_time(target_n['t'], rate)}: {target_n['text']}")
                elif k == pygame.K_p:
                    want_shot = True
                elif k == pygame.K_e and stereo:
                    state["eye"] = 1 - state["eye"]
                    say("3D: δεξί μάτι" if state["eye"] else "3D: αριστερό μάτι")
                    restart(now)
            screen = pygame.display.get_surface()

        # επόμενα καρέ
        now = pb.clock()
        while True:
            if pending is None:
                try:
                    pending = pb.vq.get_nowait()
                except queue.Empty:
                    break
            if pending is END:
                video_done = True
                pending = None
                break
            if not pb.started or pending[0] <= now:
                current = pending
                pending = None
                if not pb.started:
                    pb.started = True
                    if pb.want_pause:
                        pb.set_paused(True)
                        break
                continue
            break

        screen.fill((0, 0, 0))
        sw, sh = screen.get_size()
        if current:
            t, data, w, h, fw, fh = current
            surf = pygame.image.frombuffer(data, (w, h), "RGB")
            if (w, h) != (sw, sh) and (abs(w - sw) > 2 and abs(h - sh) > 2):
                s = min(sw / w, sh / h)
                surf = pygame.transform.scale(surf, (int(w * s), int(h * s)))
            pic = pygame.Rect((sw - surf.get_width()) // 2, (sh - surf.get_height()) // 2,
                              surf.get_width(), surf.get_height())
            screen.blit(surf, pic.topleft)
            osd_now = pb.paused or time.time() - last_mouse < 2.5 or note_mode
            draw_subs(t, pic, fw, sh - 64 if osd_now else sh)
            if want_shot:
                want_shot = False
                try:
                    os.makedirs(SHOTS_DIR, exist_ok=True)
                    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in cpl.title)[:60]
                    fn = os.path.join(SHOTS_DIR, f"{safe}_{fmt_time(t, rate).replace(':', '-')}.png")
                    pygame.image.save(screen.subsurface(pic).copy(), fn)
                    say(f"Screenshot: {fn}")
                except Exception as e:
                    log.exception("Αποτυχία screenshot")
                    say(f"Αποτυχία screenshot: {e}")

        if video_done and now >= total - 0.05 and not pb.paused:
            pb.set_paused(True)

        # μετρητές ήχου
        if pb.audio:
            p = pb.audio.take_peaks()
            if meter_vals is None or len(meter_vals) != len(p):
                meter_vals = [0.0] * len(p)
            for i, v in enumerate(p):
                meter_vals[i] = max(float(v), meter_vals[i] * 0.88)
        show_osd = pb.paused or time.time() - last_mouse < 2.5 or not current or note_mode
        if pb.audio and meter_vals and (state["meters"] == "on" or (state["meters"] == "auto" and show_osd)):
            import math
            n = len(meter_vals)
            bw, gap, mh = 14, 6, 140
            x0 = sw - 16 - n * (bw + gap)
            y0 = sh - 64 - 16 - 26 - mh
            bg = pygame.Surface((n * (bw + gap) + 10, mh + 34), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 150))
            screen.blit(bg, (x0 - 8, y0 - 8))
            for i, v in enumerate(meter_vals):
                db = 20 * math.log10(max(v, 1e-6))
                lvl = max(0.0, min(1.0, (db + 60) / 60))
                x = x0 + i * (bw + gap)
                pygame.draw.rect(screen, (50, 50, 50), (x, y0, bw, mh))
                hh = int(mh * lvl)
                col = (60, 200, 90) if db < -12 else ((230, 200, 40) if db < -3 else (230, 60, 50))
                if state["solo"] is not None and state["solo"] != i:
                    col = tuple(c // 3 for c in col)
                pygame.draw.rect(screen, col, (x, y0 + mh - hh, bw, hh))
                name = CH_NAMES[i] if i < len(CH_NAMES) else str(i + 1)
                lab = tiny.render(name, True, (255, 230, 120) if state["solo"] == i else (200, 200, 200))
                screen.blit(lab, (x + bw // 2 - lab.get_width() // 2, y0 + mh + 3))

        # OSD
        if show_osd:
            shown_t = current[0] if current else now
            ri = max([i for i, r in enumerate(cpl.reels) if r.start <= shown_t + 1e-6] or [0])
            bar_h = 64
            ov = pygame.Surface((sw, bar_h), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 170))
            screen.blit(ov, (0, sh - bar_h))
            bar_rect = pygame.Rect(16, sh - bar_h + 10, sw - 32, 6)
            pygame.draw.rect(screen, (80, 80, 80), bar_rect)
            for r in cpl.reels[1:]:
                x = bar_rect.x + int(r.start / total * bar_rect.w)
                pygame.draw.line(screen, (200, 200, 200), (x, bar_rect.y - 3), (x, bar_rect.bottom + 2))
            if total:
                pygame.draw.rect(screen, (230, 170, 40),
                                 (bar_rect.x, bar_rect.y, int(min(1, shown_t / total) * bar_rect.w), bar_rect.h))
                for nt in notes:
                    x = bar_rect.x + int(min(1, nt["t"] / total) * bar_rect.w)
                    pygame.draw.polygon(screen, (235, 60, 60), [(x, bar_rect.y - 1), (x - 5, bar_rect.y - 9),
                                                                (x + 5, bar_rect.y - 9)])
            iy = sh - bar_h + 31
            if pb.paused:
                pygame.draw.rect(screen, (255, 255, 255), (16, iy, 5, 16))
                pygame.draw.rect(screen, (255, 255, 255), (25, iy, 5, 16))
            else:
                pygame.draw.polygon(screen, (255, 255, 255), [(16, iy), (16, iy + 16), (30, iy + 8)])
            left = f"{fmt_time(shown_t, rate)} / {fmt_time(total, rate)}   Reel {ri + 1}/{len(cpl.reels)}"
            screen.blit(font.render(left, True, (255, 255, 255)), (40, sh - bar_h + 26))
            extra = []
            if current:
                extra.append(f"{current[4]}x{current[5]}{' (½)' if lowres else ''}")
            if stereo:
                extra.append("3D " + ("δεξί" if state["eye"] else "αριστερό") + " (E)")
            if tracks:
                extra.append(f"υπότ. {tracks[track_i]['name']} {'ON' if show_subs else 'OFF'}")
            if state["solo"] is not None:
                extra.append(f"solo {CH_NAMES[state['solo']] if state['solo'] < len(CH_NAMES) else state['solo'] + 1}")
            if state["muted"]:
                extra.append("ΣΙΓΑΣΗ")
            if not pb.audio:
                extra.append("χωρίς ήχο")
            if notes:
                extra.append(f"σημ. {len(notes)}")
            if pb.dropped:
                extra.append(f"πεταμένα καρέ: {pb.dropped}")
            if pb.error:
                extra.append(pb.error)
            r_txt = small.render("   ".join(extra), True, (200, 200, 200))
            screen.blit(r_txt, (sw - r_txt.get_width() - 16, sh - bar_h + 30))
            screen.blit(small.render(cpl.title, True, (220, 220, 220)), (16, 10))
            # σημείωση κοντά στον τρέχοντα χρόνο
            near = [n for n in notes if abs(n["t"] - shown_t) < 1.5]
            if near:
                nt = small.render(f"Σημείωση: {near[0]['text']}", True, (255, 120, 120))
                screen.blit(nt, (16, 32))
        else:
            bar_rect = pygame.Rect(0, 0, 0, 0)

        if note_mode:
            box = pygame.Rect(sw // 2 - 300, sh // 2 - 50, 600, 100)
            pygame.draw.rect(screen, (30, 30, 36), box, border_radius=8)
            pygame.draw.rect(screen, (230, 170, 40), box, 2, border_radius=8)
            shown_t = current[0] if current else now
            screen.blit(small.render(f"Σημείωση στο {fmt_time(shown_t, rate)}  (Enter = αποθήκευση, Esc = άκυρο)",
                                     True, (200, 200, 200)), (box.x + 14, box.y + 12))
            txt = font.render(note_text + ("|" if int(time.time() * 2) % 2 else ""), True, (255, 255, 255))
            screen.blit(txt, (box.x + 14, box.y + 50), area=pygame.Rect(max(0, txt.get_width() - 570), 0, 572, 40))

        if toast[0] and time.time() < toast[1]:
            ts = small.render(toast[0], True, (255, 255, 255))
            bg = pygame.Surface((ts.get_width() + 20, ts.get_height() + 10), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 180))
            screen.blit(bg, (sw // 2 - bg.get_width() // 2, 20))
            screen.blit(ts, (sw // 2 - ts.get_width() // 2, 25))

        pygame.display.flip()
        snap = os.environ.get("DCP_SNAPSHOT")  # για δοκιμές: σώζει την οθόνη και κλείνει
        if snap and current and current[0] >= start + float(os.environ.get("DCP_SNAPSHOT_DELAY", "0.3")):
            pygame.image.save(screen, snap)
            running = False
        clock.tick(120)

    pb.close()
    pygame.quit()
    log.info("Τέλος αναπαραγωγής")
    if pb.error:
        sys.exit(1)
