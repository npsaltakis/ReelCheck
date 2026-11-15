"""Παράθυρο «Παραλαβή DCP»: δίσκος/φάκελος πηγής → αντιγραφή με επαλήθευση → έλεγχοι → κατάλογος → αναφορά."""
import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core.config import APP_NAME, get_setting, set_setting
from app.core import prefs
from app.core.util import fmt_size
from app.tools.ingest import list_dcps
from app.ui.widgets import ask_folder, make_tree

COLUMNS = (("name", "DCP", 420), ("size", "Μέγεθος", 90), ("cpl", "Τίτλοι", 300), ("note", "Σημείωση", 120))


class IngestWindow:
    """on_start(folders, dest ή None, opts) ξεκινά την παραλαβή στο κύριο παράθυρο."""

    def __init__(self, parent, theme, on_start):
        self.on_start = on_start
        self.dcps = []
        self.win = w = tk.Toplevel(parent)
        w.title("Παραλαβή DCP")
        w.geometry("980x600")
        w.minsize(820, 480)
        theme.toplevel(w, parent)
        self._build()

    def _build(self):
        top = ttk.Frame(self.win, padding=(10, 10, 10, 6))
        top.pack(fill="x")
        ttk.Label(top, text="1. Πηγή (ο δίσκος που ήρθε):").pack(side="left")
        self.src = tk.StringVar()
        ttk.Entry(top, textvariable=self.src, width=50).pack(side="left", padx=6)
        ttk.Button(top, text="Διάλεξε…", command=self.choose_src).pack(side="left")
        self.found_var = tk.StringVar(value="Διάλεξε τον δίσκο ή τον φάκελο με τα DCP.")
        ttk.Label(self.win, textvariable=self.found_var, padding=(10, 0)).pack(anchor="w")
        f = ttk.Frame(self.win, padding=(10, 6, 10, 0))
        f.pack(fill="both", expand=True)
        self.tree = make_tree(f, COLUMNS, stretch=("name", "cpl"), selectmode="extended")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_space())

        o = ttk.LabelFrame(self.win, text=" 2. Τι να γίνει ", padding=10)
        o.pack(fill="x", padx=10, pady=8)
        s = get_setting("ingest_opts", {}) or {}
        self.copy = tk.IntVar(value=1 if s.get("copy", True) else 0)
        ttk.Checkbutton(o, text="Αντιγραφή στον φάκελο του φεστιβάλ με επαλήθευση (SHA-1)", variable=self.copy,
                        command=self._toggle_dest).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(o, text="Φάκελος:").grid(row=1, column=0, sticky="w", padx=(20, 6))
        self.dest = tk.StringVar(value=prefs.folder("ingest_dest"))
        self.dest_entry = ttk.Entry(o, textvariable=self.dest, width=55)
        self.dest_entry.grid(row=1, column=1, sticky="w")
        self.dest_btn = ttk.Button(o, text="…", width=3, command=self.choose_dest)
        self.dest_btn.grid(row=1, column=2, sticky="w", padx=4)
        self.scan = tk.IntVar(value=1 if s.get("scan", True) else 0)
        ttk.Checkbutton(o, text="Σάρωση εικόνας / ήχου / υποτίτλων (περίπου 3 λεπτά ανά ταινία 2 ωρών)",
                        variable=self.scan).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.use_catalog = tk.IntVar(value=1 if s.get("catalog") else 0)
        ttk.Checkbutton(o, text="Σύγκριση με τον κατάλογο του φεστιβάλ:", variable=self.use_catalog).grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self.catalog = tk.StringVar(value=s.get("catalog") or get_setting("catalog", "") or "")
        ttk.Entry(o, textvariable=self.catalog, width=55).grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Button(o, text="…", width=3, command=self.choose_catalog).grid(row=3, column=2, sticky="w", padx=4,
                                                                         pady=(6, 0))
        ttk.Label(o, text="Στο τέλος γράφεται αναφορά HTML στον φάκελο (ή δίπλα στην πηγή αν δεν γίνει αντιγραφή).").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        bot = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        bot.pack(fill="x")
        self.space_var = tk.StringVar()
        ttk.Label(bot, textvariable=self.space_var).pack(side="left")
        ttk.Button(bot, text="▶  Έναρξη παραλαβής", command=self.start).pack(side="right")
        self._toggle_dest()

    # ------------------------------------------------------------------ επιλογές
    def _toggle_dest(self):
        state = ["!disabled"] if self.copy.get() else ["disabled"]
        self.dest_entry.state(state)
        self.dest_btn.state(state)
        self._update_space()

    def choose_src(self):
        if ask_folder(self.win, "Ο δίσκος ή ο φάκελος με τα DCP", self.src):
            self.scan_source()

    def choose_dest(self):
        if ask_folder(self.win, "Φάκελος του φεστιβάλ (εκεί θα αντιγραφούν)", self.dest):
            self._update_space()

    def choose_catalog(self):
        p = filedialog.askopenfilename(parent=self.win, title="Κατάλογος (CSV ή TXT)",
                                       filetypes=[("CSV / TXT", "*.csv *.txt"), ("Όλα", "*.*")])
        if p:
            self.catalog.set(p)
            self.use_catalog.set(1)

    def scan_source(self):
        src = self.src.get().strip()
        self.win.configure(cursor="watch")
        self.win.update()
        try:
            self.dcps = list_dcps(src) if os.path.isdir(src) else []
        finally:
            self.win.configure(cursor="")
        self.tree.delete(*self.tree.get_children())
        for n, d in enumerate(self.dcps):
            note = "κρυπτογραφημένο" if d["encrypted"] else (f"λείπουν {d['missing']}" if d["missing"] else "")
            self.tree.insert("", "end", iid=str(n), values=(d["name"], fmt_size(d["size"]),
                                                             " | ".join(d["titles"]) or "χωρίς CPL", note))
        self.tree.selection_set([str(n) for n in range(len(self.dcps))])
        total = sum(d["size"] for d in self.dcps)
        self.found_var.set(f"Βρέθηκαν {len(self.dcps)} DCP, συνολικά {fmt_size(total)}. "
                           "Επιλεγμένα όλα (Ctrl+κλικ για να βγάλεις κάποιο)." if self.dcps
                           else "Δεν βρέθηκαν DCP σε αυτόν τον φάκελο.")
        self._update_space()

    def selected_folders(self):
        return [self.dcps[int(i)]["folder"] for i in self.tree.selection()]

    def _update_space(self):
        dest = self.dest.get().strip()
        if not (self.copy.get() and dest and self.dcps):
            self.space_var.set("")
            return
        need = sum(self.dcps[int(i)]["size"] for i in self.tree.selection())
        try:
            free = shutil.disk_usage(dest if os.path.isdir(dest) else os.path.splitdrive(dest)[0] + os.sep).free
            self.space_var.set(f"Χρειάζονται {fmt_size(need)}, ελεύθερα {fmt_size(free)}"
                               + ("  ⚠ ΔΕΝ ΧΩΡΑΕΙ" if need > free else ""))
        except OSError:
            self.space_var.set("")

    def start(self):
        folders = self.selected_folders()
        if not folders:
            messagebox.showinfo(APP_NAME, "Διάλεξε πηγή με DCP.", parent=self.win)
            return
        dest = self.dest.get().strip() if self.copy.get() else None
        if self.copy.get() and not dest:
            self.choose_dest()
            dest = self.dest.get().strip()
            if not dest:
                return
        catalog = self.catalog.get().strip() if self.use_catalog.get() else None
        if catalog and not os.path.isfile(catalog):
            messagebox.showerror(APP_NAME, "Δεν βρέθηκε το αρχείο του καταλόγου.", parent=self.win)
            return
        opts = {"copy": bool(self.copy.get()), "scan": bool(self.scan.get()), "catalog": catalog}
        set_setting("ingest_opts", opts)
        if dest:
            set_setting("ingest_dest", dest)
        self.on_start(folders, os.path.normpath(dest) if dest else None, opts)
        self.win.destroy()
