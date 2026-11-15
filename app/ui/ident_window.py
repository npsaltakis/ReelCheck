"""Παράθυρο «Ident φεστιβάλ»: λογότυπο, μουσική, κείμενο, διάρκεια → DCP."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core import prefs
from app.core.config import APP_NAME, get_setting, set_setting
from app.tools.ident import music_seconds
from app.ui.widgets import ask_folder, labeled_combo
from app.ui.i18n_tk import label_key

RATIOS = {"185": "Flat 1.85 (1998×1080)", "239": "Scope 2.39 (2048×858)"}
KINDS = {"XSN": "XSN (ident / μεταβατικό)", "PSA": "PSA (ανακοίνωση)", "ADV": "ADV (διαφήμιση)",
         "TLR": "TLR (τρέιλερ)"}


class IdentWindow:
    """on_start(opts): opts = {logo, music, text, seconds, ratio, kind, name, out_dir}."""

    def __init__(self, parent, theme, on_start, has_dcpomatic):
        self.on_start = on_start
        saved = get_setting("ident_opts", {}) or {}
        self.win = w = tk.Toplevel(parent)
        w.title("Ident / intro φεστιβάλ → DCP")
        w.resizable(False, False)
        theme.toplevel(w, parent)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        self.logo = tk.StringVar(value=saved.get("logo", ""))
        self.music = tk.StringVar(value=saved.get("music", ""))
        self.text = tk.StringVar(value=saved.get("text", ""))
        self.seconds = tk.StringVar(value=str(saved.get("seconds", 8)))
        self.name = tk.StringVar(value=saved.get("name", "Festival_Ident"))
        self.out_dir = tk.StringVar(value=prefs.folder("convert_out"))
        rows = (("Λογότυπο (PNG με διαφάνεια ή JPG):", self.logo, self._pick_logo),
                ("Μουσική (προαιρετικά: MP3, WAV, ...):", self.music, self._pick_music),
                ("Κείμενο κάτω από το λογότυπο:", self.text, None),
                ("Όνομα DCP:", self.name, None),
                ("Φάκελος εξόδου:", self.out_dir, lambda: ask_folder(self.win, "Πού να γραφτεί το DCP;", self.out_dir)))
        for r, (label, var, pick) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 8))
            ttk.Entry(f, textvariable=var, width=50).grid(row=r, column=1, sticky="w")
            if pick:
                ttk.Button(f, text="…", width=3, command=pick).grid(row=r, column=2, padx=4)
        r = len(rows)
        ttk.Label(f, text="Διάρκεια (δευτ.):").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.seconds, width=8).grid(row=r, column=1, sticky="w")
        self.ratio, _ = labeled_combo(f, r + 1, 0, "Κάδρο:", RATIOS, saved.get("ratio", "185"), width=30, pady=4)
        self.kind, _ = labeled_combo(f, r + 2, 0, "Είδος (DCNC):", KINDS, saved.get("kind", "XSN"), width=30, pady=4)
        ttk.Label(f, text="Fade in / out 1 δευτ. Η μουσική κόβεται στη διάρκεια με σβήσιμο στο τέλος.",
                  foreground="gray").grid(row=r + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.grid(row=r + 4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Άκυρο", command=w.destroy).pack(side="right")
        start = ttk.Button(b, text="▶  Δημιουργία DCP", command=self.start)
        start.pack(side="right", padx=6)
        if not has_dcpomatic:
            start.state(["disabled"])
            ttk.Label(f, text="Χρειάζεται το DCP-o-matic (Εργαλεία → Μετατροπή βίντεο σε DCP).",
                      foreground="#c5221f").grid(row=r + 5, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _pick_logo(self):
        p = filedialog.askopenfilename(parent=self.win, title="Λογότυπο",
                                       filetypes=[("Εικόνες", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("Όλα", "*.*")])
        if p:
            self.logo.set(os.path.normpath(p))

    def _pick_music(self):
        p = filedialog.askopenfilename(parent=self.win, title="Μουσική",
                                       filetypes=[("Ήχος", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg"), ("Όλα", "*.*")])
        if not p:
            return
        self.music.set(os.path.normpath(p))
        try:
            secs = music_seconds(p)
            if secs and secs < float(self.seconds.get().replace(",", ".") or 0):
                self.seconds.set(f"{secs:.0f}")
        except (OSError, ValueError):
            pass  # η διάρκεια μένει όπως είναι

    def start(self):
        try:
            seconds = float(self.seconds.get().replace(",", "."))
        except ValueError:
            seconds = 0
        problems = []
        if not os.path.isfile(self.logo.get()):
            problems.append("Διάλεξε λογότυπο.")
        if self.music.get() and not os.path.isfile(self.music.get()):
            problems.append("Το αρχείο μουσικής δεν βρέθηκε.")
        if not 3 <= seconds <= 120:
            problems.append("Διάρκεια από 3 έως 120 δευτ.")
        if not self.out_dir.get():
            problems.append("Διάλεξε φάκελο εξόδου.")
        if problems:
            messagebox.showerror(APP_NAME, "\n".join(problems), parent=self.win)
            return
        opts = {"logo": self.logo.get(), "music": self.music.get(), "text": self.text.get().strip(),
                "seconds": seconds, "ratio": label_key(self.ratio, RATIOS), "kind": label_key(self.kind, KINDS),
                "name": self.name.get().strip() or "Festival_Ident", "out_dir": self.out_dir.get()}
        set_setting("ident_opts", {k: v for k, v in opts.items() if k != "out_dir"})
        self.win.destroy()
        self.on_start(opts)
