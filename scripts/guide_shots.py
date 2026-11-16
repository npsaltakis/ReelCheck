"""
Screenshots για τις οδηγίες (φωτεινό θέμα): python scripts/guide_shots.py [el|en]
Γράφει docs/screenshots/guide-*.png (αγγλικά: guide-en-*.png). Δεν αλλάζει ρυθμίσεις ή δεδομένα του χρήστη.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import ImageGrab  # noqa: E402

from app.core import prefs  # noqa: E402
from app.core.i18n import set_language, tr  # noqa: E402
from app.tools import returns as rt, schedule as sch  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshots")
LANG = sys.argv[1] if len(sys.argv) > 1 else "el"
PREFIX = "guide-" if LANG == "el" else f"guide-{LANG}-"
set_language(LANG)
TMP = tempfile.mkdtemp(prefix="reelcheck_shots_")
_orig = prefs.get
prefs.get = lambda k: {"theme": "light", "watch_enabled": False}.get(k, _orig(k))  # μόνο για αυτή τη διεργασία
rt.RETURNS_FILE = os.path.join(TMP, "returns.json")
sch.SCHEDULE_FILE = os.path.join(TMP, "schedule.json")

import app.ui.launcher as L  # noqa: E402
from app.ui.i18n_tk import install as install_translation  # noqa: E402
import app.ui.launcher_tools as LT  # noqa: E402

EXAMPLE = r"E:\Festival 2026"


class _IngestExample(LT.IngestWindow):
    """Ουδέτερες διαδρομές-παράδειγμα (όχι οι φάκελοι του υπολογιστή που τραβά τα screenshots)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.dest.set(EXAMPLE)
        self.catalog.set(EXAMPLE + (r"\κατάλογος.csv" if LANG == "el" else r"\catalogue.csv"))


LT.IngestWindow = _IngestExample
install_translation()  # αγγλικά: όπως στο πρόγραμμα (στα ελληνικά δεν κάνει τίποτα)

lw = L.LauncherWindow(initial=os.path.join(ROOT, "_demo"))
r = lw.root
r.after(2000, lambda: (r.state("normal"), r.geometry("1150x680+40+40")))  # όχι μεγιστοποιημένο


def grab(win, name):
    win.lift()
    win.attributes("-topmost", True)
    win.update()
    if win.winfo_width() <= 1:  # δεν έχει εμφανιστεί ακόμα
        win.wait_visibility()
        win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + win.winfo_width(), y + win.winfo_height()), all_screens=True).save(
        os.path.join(OUT, f"{PREFIX}{name}.png"))
    win.attributes("-topmost", False)
    print("ok", name)


def last_window(title):
    return [w for w in r.winfo_children() if w.winfo_class() == "Toplevel" and w.title() in (title, tr(title))][-1]


def window_shot(open_fn, title, name, delay=900, close=True):
    def go():
        open_fn()
        r.after(delay, lambda: (grab(last_window(title), name), last_window(title).destroy() if close else None))
    return go


DEMO = {"el": ("Αίθουσα 1", "εισαγωγή σκηνοθέτη", "Το Ταξίδι", "Καλοκαίρι", "Διανομή Α", "Σκηνοθέτης"),
        "en": ("Screen 1", "director's intro", "The Journey", "Summer", "Distributor A", "Director")}


def demo_schedule():
    it = lw.items[0]["cpl"]
    room, intro, film1, film2, dist, director = DEMO.get(LANG, DEMO["en"])
    sch.save([sch.new_screening("20/11/2026", "18:00", room, it.title, it.seconds / 60, 10, it.id, intro),
              sch.new_screening("20/11/2026", "20:30", room, film1, 95, 15, None, "Q&A"),
              sch.new_screening("20/11/2026", "21:00", room, film2, 18, 0)])
    rt.save([rt.new_return(film1, "CRU #3", dist, "info@example.com", "25/11/2026"),
             rt.new_return(film2, "USB 1TB", director, "", "16/11/2026")])


STEPS = []


def main_window():
    lw.tree.selection_set("1")
    lw.show_details()
    lw.path_var.set(EXAMPLE)
    r.update()
    grab(r, "main")


STEPS += [main_window,
          window_shot(lambda: lw.open_settings("checks"), "Ρυθμίσεις", "settings"),
          window_shot(lw.open_ingest, "Παραλαβή DCP", "ingest"),
          window_shot(lw.open_convert, "Μετατροπή βίντεο σε DCP", "convert"),
          window_shot(lambda: (demo_schedule(), lw.open_schedule()), "Πρόγραμμα προβολών", "schedule"),
          window_shot(lw.open_returns, "Επιστροφές DCP", "returns"),
          window_shot(lw.open_ident, "Ident / intro φεστιβάλ → DCP", "ident"),
          window_shot(lw.open_stats, "Στατιστικά φεστιβάλ", "stats"),
          window_shot(lambda: (lw.tree.selection_set(["0", "1"]), lw.compare_selected()), "Σύγκριση δύο DCP", "compare",
                      delay=2500)]


def run(k=0):
    if k >= len(STEPS):
        r.after(300, r.destroy)
        return
    if lw.jobs.busy:
        r.after(300, lambda: run(k))
        return
    try:
        STEPS[k]()
    except Exception as e:  # ένα παράθυρο που λείπει δεν σταματά τα υπόλοιπα
        print("FAIL", k, e)
    r.after(3200, lambda: run(k + 1))


r.after(3500, run)
r.mainloop()
