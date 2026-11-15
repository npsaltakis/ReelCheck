"""Παράθυρο «Έλεγχος υποτίτλων .srt»: λίστα με προβλήματα, μετατόπιση χρόνου, αλλαγή fps, αποθήκευση."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from app.core.config import APP_NAME, VIDEO_EXT
from app.core.subtitles import parse_srt
from app.core.util import fmt_time
from app.tools import subcheck

COLUMNS = (("n", "#", 45), ("start", "Αρχή", 95), ("end", "Τέλος", 95), ("dur", "Διάρκ.", 55),
           ("text", "Κείμενο", 380), ("problem", "Πρόβλημα", 260))


def srt_ms(t):
    return subcheck.srt_time(t).replace(",", ".")


class SubtitleWindow:
    def __init__(self, parent, theme, path=None):
        self.theme = theme
        self.path, self.entries, self.duration = None, [], None
        self.changed = False
        self.win = w = tk.Toplevel(parent)
        w.title("Έλεγχος υποτίτλων .srt")
        w.geometry("1000x600")
        w.minsize(800, 450)
        theme.toplevel(w, parent)
        self._build()
        if path:
            self.load(path)

    def _build(self):
        bar = ttk.Frame(self.win, padding=(10, 10, 10, 6))
        bar.pack(fill="x")
        for text, cmd in (("Άνοιγμα .srt…", self.open), ("Σύγκριση με βίντεο…", self.pick_video),
                          ("Μετατόπιση χρόνου…", self.shift), ("Αλλαγή fps…", self.change_fps),
                          ("Αποθήκευση ως…", self.save_as)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.only_problems = tk.IntVar(value=0)
        ttk.Checkbutton(bar, text="Μόνο με πρόβλημα", variable=self.only_problems,
                        command=self.refresh).pack(side="right")
        self.summary_var = tk.StringVar(value="Άνοιξε ένα αρχείο .srt")
        ttk.Label(self.win, textvariable=self.summary_var, padding=(10, 0, 10, 6), wraplength=960,
                  justify="left").pack(fill="x")
        f = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        f.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(f, columns=[c[0] for c in COLUMNS], show="headings")
        for key, text, width in COLUMNS:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w", stretch=key in ("text", "problem"))
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.theme.tag_tree(self.tree, extra=[("warn", "warn"), ("info", "muted")])

    # ------------------------------------------------------------------ δεδομένα
    def load(self, path):
        try:
            self.entries = parse_srt(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Δεν διαβάστηκε το αρχείο:\n{e}", parent=self.win)
            return
        self.path, self.changed = path, False
        self.win.title(f"Έλεγχος υποτίτλων: {os.path.basename(path)}")
        self.refresh()

    def open(self):
        p = filedialog.askopenfilename(parent=self.win, title="Υπότιτλοι", filetypes=[("SRT", "*.srt"), ("Όλα", "*.*")])
        if p:
            self.load(p)

    def pick_video(self):
        """Διάρκεια από βίντεο, για να φανούν υπότιτλοι μετά το τέλος."""
        p = filedialog.askopenfilename(parent=self.win, title="Βίντεο της ταινίας", filetypes=[
            ("Βίντεο", " ".join("*" + e for e in VIDEO_EXT)), ("Όλα", "*.*")])
        if not p:
            return
        from app.tools.mediainfo import probe_media
        info = probe_media(p)
        self.duration = info["duration"] or None
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        problems = subcheck.check_entries(self.entries, self.duration)
        by_n = {}
        for n, level, _t, text in problems:
            by_n.setdefault(n, []).append((level, text))
        for n, (a, b, items) in enumerate(self.entries, 1):
            probs = by_n.get(n, [])
            if self.only_problems.get() and not probs:
                continue
            level = "warn" if any(lv == "warn" for lv, _ in probs) else ("info" if probs else "")
            text = " / ".join(subcheck.entry_text((a, b, items))) or "(εικόνα)"
            self.tree.insert("", "end", iid=str(n), tags=(level,), values=(
                n, srt_ms(a), srt_ms(b), f"{b - a:.2f}", text, "; ".join(t for _, t in probs)))
        warn = sum(1 for p in problems if p[1] == "warn")
        dur = f" · ταινία {fmt_time(self.duration)}" if self.duration else ""
        changed = " · ΑΛΛΑΓΕΣ ΧΩΡΙΣ ΑΠΟΘΗΚΕΥΣΗ" if self.changed else ""
        if not self.entries:
            self.summary_var.set("Δεν βρέθηκαν υπότιτλοι" if self.path else "Άνοιξε ένα αρχείο .srt")
            return
        self.summary_var.set(
            f"{len(self.entries)} υπότιτλοι, {fmt_time(self.entries[0][0])} – {fmt_time(self.entries[-1][1])}{dur}"
            f" · {warn} προβλήματα" + (f": {subcheck.summary(problems)}" if warn else " ✓") + changed +
            f"\nΚανόνες: {subcheck.MIN_SECONDS}–{subcheck.MAX_SECONDS} δευτ., έως {subcheck.MAX_CPS} χαρακτήρες/δευτ., "
            f"έως {subcheck.MAX_LINES} γραμμές των {subcheck.MAX_LINE} χαρακτήρων.")

    # ------------------------------------------------------------------ διορθώσεις
    def shift(self):
        if not self.entries:
            return
        s = simpledialog.askstring(APP_NAME, "Μετατόπιση σε δευτερόλεπτα (π.χ. 1.5 ή -0.8):", parent=self.win)
        if not s:
            return
        try:
            sec = float(s.replace(",", "."))
        except ValueError:
            messagebox.showerror(APP_NAME, "Χρειάζεται αριθμός.", parent=self.win)
            return
        self.entries = subcheck.shift(self.entries, sec)
        self.changed = True
        self.refresh()

    def change_fps(self):
        if not self.entries:
            return
        d = tk.Toplevel(self.win)
        d.title("Αλλαγή fps")
        self.theme.toplevel(d, self.win)
        f = ttk.Frame(d, padding=14)
        f.pack()
        ttk.Label(f, text="Οι υπότιτλοι είναι για βίντεο των").grid(row=0, column=0, sticky="w")
        src = tk.StringVar(value="25")
        dst = tk.StringVar(value="24")
        vals = [f"{x:g}" for x in subcheck.FPS_CHOICES]
        ttk.Combobox(f, textvariable=src, values=vals, width=8).grid(row=0, column=1, padx=6)
        ttk.Label(f, text="fps και το βίντεο τώρα παίζει σε").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(f, textvariable=dst, values=vals, width=8).grid(row=1, column=1, padx=6)
        ttk.Label(f, text="π.χ. ταινία 25 fps που έγινε DCP 24 fps: 25 → 24 (όλα λίγο αργότερα)",
                  wraplength=360).grid(row=2, column=0, columnspan=2, sticky="w")

        def ok():
            try:
                a, b = float(src.get().replace(",", ".")), float(dst.get().replace(",", "."))
            except ValueError:
                return
            self.entries = subcheck.change_fps(self.entries, a, b)
            self.changed = True
            d.destroy()
            self.refresh()

        ttk.Button(f, text="Εφαρμογή", command=ok).grid(row=3, column=1, sticky="e", pady=(10, 0))
        d.grab_set()

    def save_as(self):
        if not self.entries:
            return
        base = os.path.splitext(os.path.basename(self.path or "υπότιτλοι.srt"))[0]
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".srt", initialfile=f"{base}_διορθωμένο.srt",
                                         filetypes=[("SRT", "*.srt")])
        if p:
            subcheck.write_srt(self.entries, p)
            self.path, self.changed = p, False
            self.refresh()
            messagebox.showinfo(APP_NAME, f"Αποθηκεύτηκε:\n{p}", parent=self.win)
