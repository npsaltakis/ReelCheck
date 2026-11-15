"""Στοιχεία οθόνης του player: μετρητές ήχου, μπάρα χρόνου, πλαίσιο σημείωσης, μηνύματα."""
import math
import time

from app.core.config import CH_NAMES
from app.core.i18n import tr
from app.core.util import fmt_time

BAR_H = 64


def channel_name(i):
    return CH_NAMES[i] if i < len(CH_NAMES) else str(i + 1)


def meter_color(db, dimmed):
    col = (60, 200, 90) if db < -12 else ((230, 200, 40) if db < -3 else (230, 60, 50))
    return tuple(c // 3 for c in col) if dimmed else col


def draw_meters(pg, screen, font, values, solo, lufs_text=""):
    """Στάθμη ανά κανάλι κάτω δεξιά (values: γραμμικές τιμές 0..1), και από πάνω η ένταση σε LUFS."""
    sw, sh = screen.get_size()
    n = len(values)
    bw, gap, mh = 14, 6, 140
    x0 = sw - 16 - n * (bw + gap)
    y0 = sh - BAR_H - 16 - 26 - mh
    lab = font.render(lufs_text, True, (230, 230, 230)) if lufs_text else None
    width = max(n * (bw + gap) + 10, (lab.get_width() + 16) if lab else 0)
    bx = sw - 16 - width + 2
    extra = 20 if lab else 0
    bg = pg.Surface((width, mh + 34 + extra), pg.SRCALPHA)
    bg.fill((0, 0, 0, 150))
    screen.blit(bg, (bx, y0 - 8 - extra))
    if lab:
        screen.blit(lab, (sw - 16 - lab.get_width() - 4, y0 - 4 - extra))
    for i, v in enumerate(values):
        db = 20 * math.log10(max(v, 1e-6))
        lvl = max(0.0, min(1.0, (db + 60) / 60))
        x = x0 + i * (bw + gap)
        pg.draw.rect(screen, (50, 50, 50), (x, y0, bw, mh))
        hh = int(mh * lvl)
        pg.draw.rect(screen, meter_color(db, solo is not None and solo != i), (x, y0 + mh - hh, bw, hh))
        lab = font.render(channel_name(i), True, (255, 230, 120) if solo == i else (200, 200, 200))
        screen.blit(lab, (x + bw // 2 - lab.get_width() // 2, y0 + mh + 3))


def draw_progress_bar(pg, screen, reels, notes, shown_t, total):
    """Μπάρα χρόνου με όρια reel και σημάδια σημειώσεων. Επιστρέφει το Rect της (για κλικ)."""
    sw, sh = screen.get_size()
    ov = pg.Surface((sw, BAR_H), pg.SRCALPHA)
    ov.fill((0, 0, 0, 170))
    screen.blit(ov, (0, sh - BAR_H))
    bar = pg.Rect(16, sh - BAR_H + 10, sw - 32, 6)
    pg.draw.rect(screen, (80, 80, 80), bar)
    if not total:
        return bar
    for r in reels[1:]:
        x = bar.x + int(r.start / total * bar.w)
        pg.draw.line(screen, (200, 200, 200), (x, bar.y - 3), (x, bar.bottom + 2))
    pg.draw.rect(screen, (230, 170, 40), (bar.x, bar.y, int(min(1, shown_t / total) * bar.w), bar.h))
    for nt in notes:
        x = bar.x + int(min(1, nt["t"] / total) * bar.w)
        pg.draw.polygon(screen, (235, 60, 60), [(x, bar.y - 1), (x - 5, bar.y - 9), (x + 5, bar.y - 9)])
    return bar


def draw_play_icon(pg, screen, paused):
    sh = screen.get_height()
    iy = sh - BAR_H + 31
    if paused:
        pg.draw.rect(screen, (255, 255, 255), (16, iy, 5, 16))
        pg.draw.rect(screen, (255, 255, 255), (25, iy, 5, 16))
    else:
        pg.draw.polygon(screen, (255, 255, 255), [(16, iy), (16, iy + 16), (30, iy + 8)])


def draw_note_box(pg, screen, font, small, shown_t, rate, text):
    sw, sh = screen.get_size()
    box = pg.Rect(sw // 2 - 300, sh // 2 - 50, 600, 100)
    pg.draw.rect(screen, (30, 30, 36), box, border_radius=8)
    pg.draw.rect(screen, (230, 170, 40), box, 2, border_radius=8)
    screen.blit(small.render(tr(f"Σημείωση στο {fmt_time(shown_t, rate)}  (Enter = αποθήκευση, Esc = άκυρο)"),
                             True, (200, 200, 200)), (box.x + 14, box.y + 12))
    txt = font.render(text + ("|" if int(time.time() * 2) % 2 else ""), True, (255, 255, 255))
    screen.blit(txt, (box.x + 14, box.y + 50), area=pg.Rect(max(0, txt.get_width() - 570), 0, 572, 40))


def draw_toast(pg, screen, small, text):
    sw = screen.get_width()
    ts = small.render(tr(text), True, (255, 255, 255))
    bg = pg.Surface((ts.get_width() + 20, ts.get_height() + 10), pg.SRCALPHA)
    bg.fill((0, 0, 0, 180))
    screen.blit(bg, (sw // 2 - bg.get_width() // 2, 20))
    screen.blit(ts, (sw // 2 - ts.get_width() // 2, 25))
