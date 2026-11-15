"""Διάλογοι του παραθύρου επιλογής: προδιαγραφές φεστιβάλ, κατάλογος φεστιβάλ."""
import csv
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.checks.spec import AUDIO_CHOICES, set_spec
from app.core.config import APP_NAME


def _num_list(text):
    return [float(x) for x in text.replace(";", ",").split(",") if x.strip()]


class SpecDialog:
    """Επεξεργασία των προδιαγραφών φεστιβάλ. on_save(spec) καλείται μετά την αποθήκευση."""

    def __init__(self, parent, theme, spec, on_save):
        self.spec, self.on_save = dict(spec), on_save
        self.win = w = tk.Toplevel(parent)
        w.title("Προδιαγραφές φεστιβάλ")
        theme.toplevel(w, parent)
        self.frame = ttk.Frame(w, padding=14)
        self.frame.pack(fill="both", expand=True)
        self._build()
        w.grab_set()

    def _row(self, r, label):
        ttk.Label(self.frame, text=label).grid(row=r, column=0, sticky="w")

    def _checks(self, r, label, options, selected):
        self._row(r, label)
        vars_ = {k: tk.IntVar(value=k in selected) for k in options}
        for j, (k, text) in enumerate(options.items()):
            ttk.Checkbutton(self.frame, text=text, variable=vars_[k]).grid(row=r, column=1 + j, sticky="w")
        return vars_

    def _entry(self, r, label, value, width):
        self._row(r, label)
        var = tk.StringVar(value=value)
        ttk.Entry(self.frame, textvariable=var, width=width).grid(row=r, column=1, columnspan=2, sticky="w")
        return var

    def _build(self):
        s, f = self.spec, self.frame
        self.enabled = tk.IntVar(value=1 if s["enabled"] else 0)
        ttk.Checkbutton(f, text="Έλεγχος με τις προδιαγραφές του φεστιβάλ", variable=self.enabled).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        self.res = self._checks(1, "Ανάλυση:", {"2K": "2K", "4K": "4K"}, s["res"])
        self.std = self._checks(2, "Πρότυπο:", {"SMPTE": "SMPTE", "IOP": "Interop"}, s["std"])
        self.fps = self._entry(3, "fps (με κόμμα):", ", ".join(f"{x:g}" for x in s["fps"]), 18)
        self._row(4, "Ήχος:")
        self.audio = tk.StringVar(value=AUDIO_CHOICES[s["audio"]])
        ttk.Combobox(f, textvariable=self.audio, values=list(AUDIO_CHOICES.values()), state="readonly",
                     width=20).grid(row=4, column=1, columnspan=2, sticky="w")
        self.sub_lang = self._entry(5, "Υπότιτλοι (γλώσσα, π.χ. EN):", s["sub_lang"], 8)
        self.max_min = self._entry(6, "Μέγιστη διάρκεια (λεπτά, 0 = όχι):", f"{float(s['max_minutes'] or 0):g}", 8)
        self.no_enc = tk.IntVar(value=1 if s["no_encrypted"] else 0)
        self.no_3d = tk.IntVar(value=1 if s["no_3d"] else 0)
        ttk.Checkbutton(f, text="Όχι κρυπτογραφημένα", variable=self.no_enc).grid(row=7, column=0, sticky="w",
                                                                                 pady=(6, 0))
        ttk.Checkbutton(f, text="Όχι 3D", variable=self.no_3d).grid(row=7, column=1, columnspan=2, sticky="w",
                                                                   pady=(6, 0))
        for r in range(8):
            f.grid_rowconfigure(r, pad=6)
        bf = ttk.Frame(f)
        bf.grid(row=8, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(bf, text="Άκυρο", command=self.win.destroy).pack(side="right")
        ttk.Button(bf, text="Αποθήκευση", command=self._save).pack(side="right", padx=6)

    def _save(self):
        try:
            fps = _num_list(self.fps.get())
            mx = float(self.max_min.get().replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror(APP_NAME, "Τα fps και η διάρκεια θέλουν αριθμούς.", parent=self.win)
            return
        spec = {"enabled": bool(self.enabled.get()), "res": [k for k, v in self.res.items() if v.get()],
                "std": [k for k, v in self.std.items() if v.get()], "fps": fps,
                "audio": next(k for k, v in AUDIO_CHOICES.items() if v == self.audio.get()),
                "sub_lang": self.sub_lang.get().strip().upper(), "max_minutes": mx,
                "no_encrypted": bool(self.no_enc.get()), "no_3d": bool(self.no_3d.get())}
        set_spec(spec)
        self.win.destroy()
        self.on_save(spec)


def write_table_csv(path, header, rows):
    """CSV που ανοίγει σωστά στο ελληνικό Excel."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("sep=;\n")
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


class CatalogWindow:
    """Αποτέλεσμα αντιστοίχισης καταλόγου φεστιβάλ με τα DCP. rows: [(τίτλος, DCP, κατάσταση)]."""

    HEADER = ("Τίτλος καταλόγου", "DCP που βρέθηκε", "Κατάσταση")

    def __init__(self, parent, theme, title, summary, rows):
        self.rows = rows
        self.win = w = tk.Toplevel(parent)
        w.title(f"Κατάλογος φεστιβάλ: {title}")
        w.geometry("1000x520")
        theme.toplevel(w, parent)
        ttk.Label(w, padding=8, text=summary).pack(anchor="w")
        tv = ttk.Treeview(w, columns=("a", "b", "c"), show="headings")
        for col, txt, wd in zip(("a", "b", "c"), self.HEADER, (260, 560, 100)):
            tv.heading(col, text=txt)
            tv.column(col, width=wd, anchor="w", stretch=col == "b")
        theme.tag_tree(tv, extra=[("ΛΕΙΠΕΙ", "err")])
        for r in rows:
            tv.insert("", "end", values=r, tags=(r[2],))
        tv.pack(fill="both", expand=True, padx=8)
        ttk.Button(w, text="Εξαγωγή CSV", command=self._export).pack(anchor="e", padx=8, pady=8)

    def _export(self):
        out = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv",
                                           initialfile="Κατάλογος_έλεγχος.csv", filetypes=[("CSV (Excel)", "*.csv")])
        if out:
            write_table_csv(out, self.HEADER, self.rows)
            os.startfile(out)
