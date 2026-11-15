"""Φόρμες και διάλογοι: προδιαγραφές φεστιβάλ, κατάλογος φεστιβάλ."""
import os
import tkinter as tk
from tkinter import filedialog, ttk

from app.checks.spec import AUDIO_CHOICES
from app.core.util import write_excel_csv
from app.ui.i18n_tk import label_key
from app.ui.widgets import make_tree


def _num_list(text):
    return [float(x) for x in text.replace(";", ",").split(",") if x.strip()]


class SpecForm:
    """Φόρμα προδιαγραφών φεστιβάλ μέσα σε ένα frame (καρτέλα «Φεστιβάλ» των Ρυθμίσεων)."""

    def __init__(self, frame, spec):
        self.frame, self.spec = frame, dict(spec)
        self._build()

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

    def values(self):
        """Οι προδιαγραφές της φόρμας· ValueError αν fps ή διάρκεια δεν είναι αριθμοί."""
        try:
            fps = _num_list(self.fps.get())
            mx = float(self.max_min.get().replace(",", ".") or 0)
        except ValueError:
            raise ValueError("Τα fps και η διάρκεια θέλουν αριθμούς.")
        return {"enabled": bool(self.enabled.get()), "res": [k for k, v in self.res.items() if v.get()],
                "std": [k for k, v in self.std.items() if v.get()], "fps": fps,
                "audio": label_key(self.audio, AUDIO_CHOICES),
                "sub_lang": self.sub_lang.get().strip().upper(), "max_minutes": mx,
                "no_encrypted": bool(self.no_enc.get()), "no_3d": bool(self.no_3d.get())}


class TableWindow:
    """
    Παράθυρο με πίνακα αποτελεσμάτων και «Εξαγωγή CSV». columns: [(τίτλος, πλάτος)].
    tag_of(row): ετικέτα χρώματος της γραμμής (π.χ. 'err', 'ok'), tags: [(ετικέτα, κλειδί παλέτας)].
    """

    def __init__(self, parent, theme, title, summary, columns, rows, csv_name="Πίνακας.csv", stretch=1,
                 tag_of=None, tags=(), size="1000x520"):
        self.rows, self.header, self.csv_name = rows, [c[0] for c in columns], csv_name
        self.win = w = tk.Toplevel(parent)
        w.title(title)
        w.geometry(size)
        theme.toplevel(w, parent)
        ttk.Label(w, padding=8, text=summary, wraplength=960, justify="left").pack(anchor="w")
        keys = [f"c{i}" for i in range(len(columns))]
        tv = make_tree(w, [(k, t, wd) for k, (t, wd) in zip(keys, columns)], stretch=(keys[stretch],))
        theme.tag_tree(tv, extra=list(tags))
        for r in rows:
            tv.insert("", "end", values=r, tags=(tag_of(r),) if tag_of else ())
        tv.pack(fill="both", expand=True, padx=8)
        ttk.Button(w, text="Εξαγωγή CSV", command=self._export).pack(anchor="e", padx=8, pady=8)
        self.tree = tv

    def _export(self):
        out = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile=self.csv_name,
                                           filetypes=[("CSV (Excel)", "*.csv")])
        if out:
            write_excel_csv(out, self.header, self.rows)
            os.startfile(out)


class CatalogWindow(TableWindow):
    """Αποτέλεσμα αντιστοίχισης καταλόγου φεστιβάλ με τα DCP. rows: [(τίτλος, DCP, κατάσταση)]."""

    HEADER = ("Τίτλος καταλόγου", "DCP που βρέθηκε", "Κατάσταση")

    def __init__(self, parent, theme, title, summary, rows):
        super().__init__(parent, theme, f"Κατάλογος φεστιβάλ: {title}", summary,
                         list(zip(self.HEADER, (260, 560, 100))), rows, "Κατάλογος_έλεγχος.csv",
                         tag_of=lambda r: r[2], tags=[("ΛΕΙΠΕΙ", "err")])
