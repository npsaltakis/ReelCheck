"""
Παράθυρο αναπαραγωγής (pygame).

Πλήκτρα:
  Space παύση | ← → ±10s (Shift ±60s) | , . καρέ-καρέ | PgUp/PgDn reel | Home αρχή
  F πλήρης οθόνη | H μισή ανάλυση | S υπότιτλοι on/off | L αλλαγή υποτίτλων
  U αλλαγή ήχου (άλλο CPL) | 0 όλα τα κανάλια | 1-9 solo καναλιού
  A μετρητές ήχου (αυτόματα / πάντα / κρυφοί) | M σίγαση
  N νέα σημείωση | J / Shift+J επόμενη/προηγ. σημείωση | Ctrl+Z σβήσιμο τελευταίας σημείωσης
  [ ] επανάληψη A–B (\\ ακύρωση) | Z zoom 2x στο σημείο του ποντικιού
  P screenshot | E αριστερό/δεξί μάτι (3D) | Esc / Q έξοδος
"""
import os
import queue
import sys
import time

from app.core import prefs
from app.core.config import APP_NAME, log, resource
from app.core.i18n import tr
from app.core.dcp import load_target
from app.core.media import probe
from app.core.notes import load_notes, save_notes
from app.core.related import audio_alternatives
from app.core.subtitles import load_tracks, sibling_tracks
from app.core.util import fmt_time
from app.player import osd
from app.player.audio import END
from app.player.playback import Playback
from app.player.subs_render import SubtitleRenderer

METER_MODES = {"auto": "on", "on": "off", "off": "auto"}
METER_LABELS = {"auto": "Μετρητές ήχου: αυτόματα (με τα χειριστήρια)", "on": "Μετρητές ήχου: πάντα",
                "off": "Μετρητές ήχου: κρυφοί"}
OSD_SECONDS = 2.5
WINDOW_SIZE = (1280, 720)


def open_cpl(target):
    """CPL ή αρχείο βίντεο για αναπαραγωγή. Κλείνει με κωδικό 2 (δεν βρέθηκε) ή 3 (κρυπτογραφημένο)."""
    cpl = load_target(target)
    if not cpl:
        log.error("Δεν βρέθηκε CPL/αρχείο: %s", target)
        sys.exit(2)
    if cpl.encrypted:
        log.error("Κρυπτογραφημένο DCP (χρειάζεται KDM): %s", cpl.title)
        sys.exit(3)
    log.info("Άνοιγμα: %s | %d reels | %s | λείπουν: %s", cpl.title, len(cpl.reels), fmt_time(cpl.seconds),
             cpl.missing or "τίποτα")
    return cpl


def auto_lowres(cpl):
    """Μισή ανάλυση για 4K JPEG2000, ώστε να προλαβαίνει η αποκωδικοποίηση."""
    info = probe(cpl)
    return 1 if info.get("size", (0, 0))[0] > 2100 and info.get("codec") == "jpeg2000" else 0


def subtitle_tracks(cpl):
    """Υπότιτλοι του CPL + άλλων CPL του φακέλου με ίδια διάρκεια."""
    tracks = load_tracks(cpl)
    try:
        tracks += sibling_tracks(cpl)
    except Exception:
        log.exception("Σφάλμα σε υπότιτλους άλλων CPL")
    for t in tracks:
        log.info("Υπότιτλοι: %s, %d εμφανίσεις %s", t["name"], len(t["entries"]), t["notes"])
    return tracks


def audio_tracks(cpl):
    """Ήχος του CPL + άλλων CPL ίδιας διάρκειας (π.χ. VF με άλλη γλώσσα)."""
    if cpl.is_file:
        return []
    try:
        audios = audio_alternatives(cpl)
    except Exception:
        log.exception("Σφάλμα σε ήχο άλλων CPL")
        return []
    if len(audios) > 1:
        log.info("Ήχοι: %s", [a[0] for a in audios])
    return audios


class PlayerWindow:
    def __init__(self, cpl, start=0.0, lowres=None, fullscreen=False, autoexit=False, center=None):
        import pygame
        self.pg = pygame
        self.cpl, self.start, self.autoexit = cpl, start, autoexit
        self.total, self.rate = cpl.seconds, cpl.rate
        self.lowres = auto_lowres(cpl) if lowres is None else lowres
        self._init_display(fullscreen, center)
        self.tracks = subtitle_tracks(cpl)
        self.track_i = 0 if self.tracks else -1
        self.show_subs = bool(self.tracks) and prefs.get("subs_on")
        self.subs = SubtitleRenderer(pygame)
        self.audios = audio_tracks(cpl)
        self.audio_i = 0
        self.notes = load_notes(cpl)
        self.stereo = any(r.stereo for r in cpl.reels)
        self.muted, self.eye, self.solo, self.meters = False, 0, None, prefs.get("meters")
        self.toast = ("", 0.0)
        self.current = self.pending = None
        self.video_done = False
        self.last_mouse = time.time()
        self.bar_rect = pygame.Rect(0, 0, 0, 0)
        self.meter_vals = None
        self.note_mode, self.note_text = False, ""
        self.want_shot = False
        self.running, self.ended = True, False
        self.loop_a = self.loop_b = None   # επανάληψη A–B
        self.zoom = False
        self.pb = self.new_playback(start)

    def _init_display(self, fullscreen, center=None):
        pg = self.pg
        if center:  # στην ίδια οθόνη με το κύριο παράθυρο (αλλιώς τα Windows το βάζουν στην κύρια)
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{center[0] - WINDOW_SIZE[0] // 2},{center[1] - WINDOW_SIZE[1] // 2}"
        pg.init()
        pg.display.set_caption(f"{APP_NAME} - {self.cpl.title}")
        if resource("icon.png"):
            try:
                pg.display.set_icon(pg.image.load(resource("icon.png")))
            except Exception:
                pass
        self.screen = pg.display.set_mode(WINDOW_SIZE, pg.RESIZABLE)
        if fullscreen:
            pg.display.toggle_fullscreen()
        self.font = pg.font.SysFont("segoeui,arial", 18)
        self.small = pg.font.SysFont("segoeui,arial", 14)
        self.tiny = pg.font.SysFont("segoeui,arial", 11)
        self.win = [self.screen.get_width(), self.screen.get_height()]

    # ================================================================== αναπαραγωγή
    def new_playback(self, t):
        t = min(max(0.0, t), max(0.0, self.total - 0.01))
        p = Playback(self.cpl, t, self.lowres, lambda: (self.win[0], self.win[1]), eye=self.eye)
        if p.audio:
            p.audio.muted, p.audio.solo = self.muted, self.solo
        return p

    def restart(self, t, paused=None):
        p = self.pb.paused if paused is None else paused
        self.pb.close()
        self.pending, self.video_done = None, False
        self.pb = self.new_playback(t)
        self.pb.want_pause = p

    def now(self):
        return self.current[0] if self.current else self.pb.clock()

    def say(self, msg):
        self.toast = (tr(msg), time.time() + 2.5)
        log.info(msg)

    def osd_visible(self):
        return (self.pb.paused or time.time() - self.last_mouse < OSD_SECONDS or not self.current
                or self.note_mode)

    # ================================================================== γεγονότα
    def handle_events(self):
        pg = self.pg
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                self.running = False
            elif ev.type == pg.VIDEORESIZE:
                self.win[0], self.win[1] = max(320, ev.w), max(180, ev.h)
            elif ev.type == pg.MOUSEMOTION:
                self.last_mouse = time.time()
            elif self.note_mode:
                self.handle_note_input(ev)
            elif ev.type == pg.MOUSEBUTTONDOWN and ev.button == 1:
                if self.bar_rect.w and self.bar_rect.inflate(0, 16).collidepoint(ev.pos):
                    self.restart((ev.pos[0] - self.bar_rect.x) / self.bar_rect.w * self.total)
            elif ev.type == pg.KEYDOWN:
                self.handle_key(ev)
            self.screen = pg.display.get_surface()

    def handle_note_input(self, ev):
        pg = self.pg
        if ev.type == pg.TEXTINPUT:
            self.note_text += ev.text
        elif ev.type == pg.KEYDOWN:
            if ev.key == pg.K_RETURN:
                self.save_note()
                self.end_note_mode()
            elif ev.key == pg.K_ESCAPE:
                self.end_note_mode()
            elif ev.key == pg.K_BACKSPACE:
                self.note_text = self.note_text[:-1]

    def handle_key(self, ev):
        pg = self.pg
        k, shift, ctrl = ev.key, ev.mod & pg.KMOD_SHIFT, ev.mod & pg.KMOD_CTRL
        now = self.now()
        if pg.K_0 <= k <= pg.K_9 or pg.K_KP0 <= k <= pg.K_KP9:
            self.set_solo((k - pg.K_0) if k <= pg.K_9 else (k - pg.K_KP0))
            return
        actions = {
            pg.K_ESCAPE: self.escape,
            pg.K_q: self.quit,
            pg.K_SPACE: self.toggle_pause,
            pg.K_LEFT: lambda: self.restart(now - (60 if shift else 10)),
            pg.K_RIGHT: lambda: self.restart(now + (60 if shift else 10)),
            pg.K_COMMA: lambda: self.restart(now - 1 / float(self.rate) + 0.001, paused=True),
            pg.K_PERIOD: lambda: self.restart(now + 1 / float(self.rate) + 0.001, paused=True),
            pg.K_PAGEUP: lambda: self.jump_reel(now, -1),
            pg.K_PAGEDOWN: lambda: self.jump_reel(now, +1),
            pg.K_HOME: lambda: self.restart(0),
            pg.K_f: self.toggle_fullscreen,
            pg.K_h: lambda: self.toggle_lowres(now),
            pg.K_s: self.toggle_subs,
            pg.K_l: self.next_subs,
            pg.K_u: lambda: self.next_audio(now),
            pg.K_m: self.toggle_mute,
            pg.K_a: self.cycle_meters,
            pg.K_n: self.start_note_mode,
            pg.K_z: lambda: self.undo_note() if ctrl else self.toggle_zoom(),
            pg.K_LEFTBRACKET: lambda: self.set_loop("a", now),
            pg.K_RIGHTBRACKET: lambda: self.set_loop("b", now),
            pg.K_BACKSLASH: self.clear_loop,
            pg.K_j: lambda: self.jump_note(now, back=bool(shift)),
            pg.K_p: self.request_screenshot,
            pg.K_e: lambda: self.toggle_eye(now),
        }
        fn = actions.get(k)
        if fn:
            fn()

    # ================================================================== ενέργειες πλήκτρων
    def escape(self):
        if self.screen.get_flags() & self.pg.FULLSCREEN:
            self.pg.display.toggle_fullscreen()
        else:
            self.running = False

    def quit(self):
        self.running = False

    def toggle_pause(self):
        if self.video_done and self.pb.clock() >= self.total - 0.1:
            self.restart(0, paused=False)
        else:
            self.pb.set_paused(not self.pb.paused)
            self.pb.want_pause = self.pb.paused

    def jump_reel(self, now, direction):
        starts = [r.start for r in self.cpl.reels]
        if direction > 0:
            nxt = [s for s in starts if s > now + 0.5]
            if nxt:
                self.restart(nxt[0])
        else:
            prv = [s for s in starts if s < now - 2]
            self.restart(prv[-1] if prv else 0)

    def toggle_fullscreen(self):
        self.pg.display.toggle_fullscreen()
        self.win[0], self.win[1] = self.pg.display.get_surface().get_size()

    def toggle_lowres(self, now):
        self.lowres = 0 if self.lowres else 1
        self.say("Μισή ανάλυση" if self.lowres else "Πλήρης ανάλυση")
        self.restart(now)

    def toggle_subs(self):
        if self.tracks:
            self.show_subs = not self.show_subs

    def next_subs(self):
        if self.tracks:
            self.track_i = (self.track_i + 1) % len(self.tracks)
            self.show_subs = True
            self.say(f"Υπότιτλοι: {self.tracks[self.track_i]['name']}")

    def next_audio(self, now):
        if len(self.audios) < 2:
            self.say("Δεν υπάρχει άλλος ήχος (άλλο CPL ίδιας διάρκειας)")
            return
        self.audio_i = (self.audio_i + 1) % len(self.audios)
        label, reels = self.audios[self.audio_i]
        for r, (snd, entry, srate, sdur) in zip(self.cpl.reels, reels):
            r.snd, r.snd_entry, r.snd_rate, r.snd_dur = snd, entry, srate, sdur
        self.say(f"Ήχος: {label}")
        self.restart(now)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.pb.audio:
            self.pb.audio.muted = self.muted

    def cycle_meters(self):
        self.meters = METER_MODES[self.meters]
        self.say(METER_LABELS[self.meters])

    def set_solo(self, n):
        nch = self.pb.audio.nch if self.pb.audio else 0
        if n == 0:
            self.solo = None
            self.say("Ήχος: όλα τα κανάλια")
        elif n <= nch:
            self.solo = n - 1
            self.say(f"Ήχος: solo κανάλι {n} ({osd.channel_name(n - 1)})")
        if self.pb.audio:
            self.pb.audio.solo = self.solo
        if n > 0 and self.meters == "auto":
            self.meters = "on"

    def toggle_zoom(self):
        self.zoom = not self.zoom
        self.say("Zoom 2x (κίνησε το ποντίκι)" if self.zoom else "Zoom: όχι")

    def set_loop(self, which, now):
        if which == "a":
            self.loop_a = now
            if self.loop_b is not None and self.loop_b <= now:
                self.loop_b = None
            self.say(f"A: {fmt_time(now, self.rate)}  (] για το τέλος)")
        else:
            if self.loop_a is None or now <= self.loop_a + 0.2:
                self.say("Βάλε πρώτα την αρχή με [")
                return
            self.loop_b = now
            self.say(f"Επανάληψη {fmt_time(self.loop_a, self.rate)} – {fmt_time(now, self.rate)}  (\\ για τέλος)")

    def clear_loop(self):
        if self.loop_a is not None:
            self.loop_a = self.loop_b = None
            self.say("Επανάληψη: όχι")

    def check_loop(self, now):
        if self.loop_a is not None and self.loop_b is not None and now >= self.loop_b and not self.pb.paused:
            self.restart(self.loop_a, paused=False)

    def toggle_eye(self, now):
        if self.stereo:
            self.eye = 1 - self.eye
            self.say("3D: δεξί μάτι" if self.eye else "3D: αριστερό μάτι")
            self.restart(now)

    def request_screenshot(self):
        self.want_shot = True

    # ================================================================== σημειώσεις
    def start_note_mode(self):
        self.note_mode, self.note_text = True, ""
        self.pb.set_paused(True)
        self.pb.want_pause = True
        self.pg.key.start_text_input()

    def end_note_mode(self):
        self.note_mode = False
        self.pg.key.stop_text_input()

    def save_note(self):
        if not self.note_text.strip():
            return
        nt = self.now()
        self.notes.append({"t": round(nt, 3), "text": self.note_text.strip()})
        self.notes.sort(key=lambda n: n["t"])
        save_notes(self.cpl, self.notes)
        self.say(f"Σημείωση στο {fmt_time(nt, self.rate)}")

    def undo_note(self):
        if self.notes:
            n = self.notes.pop()
            save_notes(self.cpl, self.notes)
            self.say(f"Σβήστηκε σημείωση: {n['text'][:40]}")

    def jump_note(self, now, back=False):
        if not self.notes:
            return
        if back:
            prv = [n for n in self.notes if n["t"] < now - 1]
            target = prv[-1] if prv else self.notes[-1]
        else:
            nxt = [n for n in self.notes if n["t"] > now + 0.5]
            target = nxt[0] if nxt else self.notes[0]
        self.restart(max(0.0, target["t"]), paused=True)
        self.say(f"{fmt_time(target['t'], self.rate)}: {target['text']}")

    # ================================================================== καρέ
    def advance_frames(self):
        """Παίρνει από την ουρά όσα καρέ έχει έρθει η ώρα τους."""
        now = self.pb.clock()
        while True:
            if self.pending is None:
                try:
                    self.pending = self.pb.vq.get_nowait()
                except queue.Empty:
                    break
            if self.pending is END:
                self.video_done, self.pending = True, None
                break
            if self.pb.started and self.pending[0] > now:
                break
            self.current, self.pending = self.pending, None
            if not self.pb.started:
                self.pb.started = True
                if self.pb.want_pause:
                    self.pb.set_paused(True)
                    break
        return now

    def check_end(self, now):
        if self.video_done and now >= self.total - 0.05 and not self.pb.paused:
            self.pb.set_paused(True)
            if self.autoexit:
                self.ended, self.running = True, False

    def update_meters(self):
        if not self.pb.audio:
            return
        p = self.pb.audio.take_peaks()
        if self.meter_vals is None or len(self.meter_vals) != len(p):
            self.meter_vals = [0.0] * len(p)
        for i, v in enumerate(p):
            self.meter_vals[i] = max(float(v), self.meter_vals[i] * 0.88)

    # ================================================================== σχεδίαση
    def draw_picture(self):
        """Σχεδιάζει το καρέ και τους υπότιτλους. Επιστρέφει το Rect της εικόνας (ή None)."""
        pg, screen = self.pg, self.screen
        sw, sh = screen.get_size()
        t, data, w, h, fw, _fh = self.current
        surf = pg.image.frombuffer(data, (w, h), "RGB")
        if (w, h) != (sw, sh) and (abs(w - sw) > 2 and abs(h - sh) > 2):
            s = min(sw / w, sh / h)
            surf = pg.transform.scale(surf, (int(w * s), int(h * s)))
        pic = pg.Rect((sw - surf.get_width()) // 2, (sh - surf.get_height()) // 2, surf.get_width(),
                      surf.get_height())
        if self.zoom:
            surf = self.zoomed(surf)
        screen.blit(surf, pic.topleft)
        if self.show_subs and 0 <= self.track_i < len(self.tracks):
            bottom = sh - osd.BAR_H if (self.pb.paused or time.time() - self.last_mouse < OSD_SECONDS
                                       or self.note_mode) else sh
            self.subs.draw(screen, self.tracks[self.track_i], t, pic, fw, bottom)
        return pic

    def zoomed(self, surf):
        """Μεγέθυνση 2x γύρω από το σημείο του ποντικιού (για έλεγχο λεπτομερειών)."""
        pg = self.pg
        w, h = surf.get_size()
        sw, sh = self.screen.get_size()
        mx, my = pg.mouse.get_pos()
        fx = min(max((mx - (sw - w) / 2) / max(1, w), 0.0), 1.0)
        fy = min(max((my - (sh - h) / 2) / max(1, h), 0.0), 1.0)
        cw, ch = w // 2, h // 2
        x = int(min(max(fx * w - cw / 2, 0), w - cw))
        y = int(min(max(fy * h - ch / 2, 0), h - ch))
        return pg.transform.scale(surf.subsurface(pg.Rect(x, y, cw, ch)), (w, h))

    def lufs_text(self):
        lt = self.pb.loudness
        if lt is None:
            return ""
        m, s = lt.at(self.now())
        i = lt.integrated
        fmt = lambda v: f"{v:.1f}" if v is not None else "–"
        return f"LUFS  M {fmt(m)}  S {fmt(s)}  I {fmt(i)}"

    def save_screenshot(self, pic):
        self.want_shot = False
        try:
            shots = prefs.shots_dir()
            os.makedirs(shots, exist_ok=True)
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in self.cpl.title)[:60]
            fn = os.path.join(shots, f"{safe}_{fmt_time(self.current[0], self.rate).replace(':', '-')}.png")
            self.pg.image.save(self.screen.subsurface(pic).copy(), fn)
            self.say(f"Screenshot: {fn}")
        except Exception as e:
            log.exception("Αποτυχία screenshot")
            self.say(f"Αποτυχία screenshot: {e}")

    def status_parts(self):
        """Κείμενα κάτω δεξιά στη μπάρα."""
        out = []
        if self.current:
            out.append(f"{self.current[4]}x{self.current[5]}{' (½)' if self.lowres else ''}")
        if self.stereo:
            out.append("3D " + ("δεξί" if self.eye else "αριστερό") + " (E)")
        if self.tracks:
            out.append(f"υπότ. {self.tracks[self.track_i]['name']} {'ON' if self.show_subs else 'OFF'}")
        if len(self.audios) > 1:
            out.append(f"ήχος {self.audios[self.audio_i][0]} (U)")
        if self.solo is not None:
            out.append(f"solo {osd.channel_name(self.solo)}")
        if self.muted:
            out.append("ΣΙΓΑΣΗ")
        if not self.pb.audio:
            out.append("χωρίς ήχο")
        if self.notes:
            out.append(f"σημ. {len(self.notes)}")
        if self.loop_a is not None:
            b = fmt_time(self.loop_b, self.rate) if self.loop_b is not None else "…"
            out.append(f"A–B {fmt_time(self.loop_a, self.rate)}–{b}")
        if self.zoom:
            out.append("zoom 2x")
        if self.pb.dropped:
            out.append(f"πεταμένα καρέ: {self.pb.dropped}")
        if self.pb.error:
            out.append(self.pb.error)
        return [tr(x) for x in out]

    def draw_osd(self, now):
        pg, screen = self.pg, self.screen
        sw, sh = screen.get_size()
        shown_t = self.current[0] if self.current else now
        reels = self.cpl.reels
        ri = max([i for i, r in enumerate(reels) if r.start <= shown_t + 1e-6] or [0])
        self.bar_rect = osd.draw_progress_bar(pg, screen, reels, self.notes, shown_t, self.total)
        osd.draw_play_icon(pg, screen, self.pb.paused)
        left = f"{fmt_time(shown_t, self.rate)} / {fmt_time(self.total, self.rate)}   Reel {ri + 1}/{len(reels)}"
        screen.blit(self.font.render(left, True, (255, 255, 255)), (40, sh - osd.BAR_H + 26))
        r_txt = self.small.render("   ".join(self.status_parts()), True, (200, 200, 200))
        screen.blit(r_txt, (sw - r_txt.get_width() - 16, sh - osd.BAR_H + 30))
        screen.blit(self.small.render(self.cpl.title, True, (220, 220, 220)), (16, 10))
        near = [n for n in self.notes if abs(n["t"] - shown_t) < 1.5]
        if near:
            screen.blit(self.small.render(tr(f"Σημείωση: {near[0]['text']}"), True, (255, 120, 120)), (16, 32))

    def draw(self, now):
        self.screen.fill((0, 0, 0))
        if self.current:
            pic = self.draw_picture()
            if self.want_shot:
                self.save_screenshot(pic)
        show_osd = self.osd_visible()
        if self.pb.audio and self.meter_vals and (self.meters == "on" or (self.meters == "auto" and show_osd)):
            osd.draw_meters(self.pg, self.screen, self.tiny, self.meter_vals, self.solo, self.lufs_text())
        if show_osd:
            self.draw_osd(now)
        else:
            self.bar_rect = self.pg.Rect(0, 0, 0, 0)
        if self.note_mode:
            osd.draw_note_box(self.pg, self.screen, self.font, self.small, self.now(), self.rate, self.note_text)
        if self.toast[0] and time.time() < self.toast[1]:
            osd.draw_toast(self.pg, self.screen, self.small, self.toast[0])
        self.pg.display.flip()

    def test_snapshot(self):
        """Για δοκιμές (DCP_SNAPSHOT): σώζει την οθόνη και κλείνει."""
        snap = os.environ.get("DCP_SNAPSHOT")
        delay = float(os.environ.get("DCP_SNAPSHOT_DELAY", "0.3"))
        if snap and self.current and self.current[0] >= self.start + delay:
            self.pg.image.save(self.screen, snap)
            self.running = False

    # ================================================================== κύριος βρόχος
    def run(self):
        clock = self.pg.time.Clock()
        while self.running:
            self.handle_events()
            now = self.advance_frames()
            self.check_end(now)
            self.check_loop(now)
            self.update_meters()
            self.draw(now)
            self.test_snapshot()
            clock.tick(120)
        self.pb.close()
        self.pg.quit()
        log.info("Τέλος αναπαραγωγής%s", " (έφτασε στο τέλος)" if self.ended else "")
        if self.pb.error:
            sys.exit(1)
        return "ended" if self.ended else None


def play(target, start=0.0, lowres=None, fullscreen=False, autoexit=False, center=None):
    """Επιστρέφει 'ended' αν η ταινία τελείωσε μόνη της (με autoexit), αλλιώς None."""
    return PlayerWindow(open_cpl(target), start, lowres, fullscreen, autoexit, center).run()
