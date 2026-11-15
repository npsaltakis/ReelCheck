"""Παράθυρο «Επιστροφές DCP»: τι πρέπει να επιστραφεί, σε ποιον και πότε, με σήμανση «επιστράφηκε»."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core.config import APP_NAME
from app.tools import returns as rt
from app.ui.widgets import make_tree

COLUMNS = (("title", "Ταινία", 280), ("disk", "Δίσκος", 120), ("to", "Επιστροφή σε", 170),
           ("due", "Προθεσμία", 85), ("state", "Κατάσταση", 110), ("returned", "Επιστράφηκε", 90),
           ("notes", "Σημειώσεις", 200))
STATE_TAGS = [("late", "err"), ("soon", "warn"), ("returned", "muted")]
FORM = (("Ταινία:", "title", 60), ("Δίσκος (π.χ. CRU #3):", "disk", 24), ("Επιστροφή σε:", "to", 40),
        ("Επικοινωνία (email / τηλ.):", "contact", 40), ("Προθεσμία (ΗΗ/ΜΜ/ΕΕΕΕ):", "due", 12),
        ("Σημειώσεις:", "notes", 60))


class ReturnsWindow:
    """films(): [(τίτλος, cpl_id, φάκελος)] από το κύριο παράθυρο· preset: ίδια μορφή, προσθέτονται αμέσως."""

    def __init__(self, parent, theme, films, preset=()):
        self.films = films
        self.items = rt.load()
        self.editing = None
        self.win = w = tk.Toplevel(parent)
        w.title("Επιστροφές DCP")
        w.geometry("1100x620")
        w.minsize(900, 500)
        theme.toplevel(w, parent)
        self.theme = theme
        self._build_list()
        self._build_form()
        if preset:
            self.add_films(preset)
        self.refresh()

    def _build_list(self):
        f = ttk.Frame(self.win, padding=(10, 10, 10, 0))
        f.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("✔ Επιστράφηκε", self.mark_returned), ("Αναίρεση", self.unmark), ("Διαγραφή", self.delete),
                          ("Εξαγωγή CSV…", self.export_csv)):
            ttk.Button(bar, text=text, command=cmd, width=max(10, len(text) + 2)).pack(side="left", padx=(0, 4))
        self.info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.info_var).pack(side="right")
        self.tree = make_tree(f, COLUMNS, stretch=("title", "notes"), selectmode="extended")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.edit_selected)
        self.theme.tag_tree(self.tree, extra=STATE_TAGS)

    def _build_form(self):
        o = ttk.LabelFrame(self.win, text=" Επιστροφή ", padding=10)
        o.pack(fill="x", padx=10, pady=10)
        self.vars = {key: tk.StringVar() for _l, key, _w in FORM}
        for i, (label, key, width) in enumerate(FORM):
            r, c = divmod(i, 2)
            ttk.Label(o, text=label).grid(row=r, column=c * 2, sticky="w", padx=(0 if c == 0 else 16, 6), pady=3)
            if key == "title":
                self.title_cb = ttk.Combobox(o, textvariable=self.vars[key], width=width - 4)
                self.title_cb.grid(row=r, column=1, sticky="w")
            else:
                ttk.Entry(o, textvariable=self.vars[key], width=width).grid(row=r, column=c * 2 + 1, sticky="w")
        bf = ttk.Frame(o)
        bf.grid(row=3, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Καθαρισμός", command=self.clear_form).pack(side="right")
        self.save_btn = ttk.Button(bf, text="Προσθήκη", command=self.save_form)
        self.save_btn.pack(side="right", padx=6)

    # ------------------------------------------------------------------ λίστα
    def refresh(self):
        self._films = {t: (cid, folder) for t, cid, folder in self.films()}
        self.title_cb.configure(values=list(self._films))
        self.tree.delete(*self.tree.get_children())
        for it in sorted(self.items, key=rt.sort_key):
            st = rt.state(it)
            self.tree.insert("", "end", iid=it["id"], tags=(st,),
                             values=(it["title"], it["disk"], it["to"], it["due"], rt.STATE_LABELS[st],
                                     it["returned"], it["notes"]))
        self.info_var.set(rt.summary(self.items))

    def add_films(self, films):
        """Προσθήκη ταινιών (χωρίς διπλές εκκρεμείς εγγραφές για το ίδιο CPL)."""
        open_ids = {it.get("cpl_id") for it in self.items if not it.get("returned")}
        for title, cid, folder in films:
            if cid and cid in open_ids:
                continue
            self.items.append(rt.new_return(title, cpl_id=cid, folder=folder))
        self._save()

    def _save(self):
        try:
            rt.save(self.items)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Δεν αποθηκεύτηκε: {e}", parent=self.win)

    def _selected(self):
        ids = set(self.tree.selection())
        return [it for it in self.items if it["id"] in ids]

    def mark_returned(self):
        for it in self._selected():
            rt.mark_returned(it)
        self._save()
        self.refresh()

    def unmark(self):
        for it in self._selected():
            it["returned"] = ""
        self._save()
        self.refresh()

    def delete(self):
        sel = self._selected()
        if not sel or not messagebox.askyesno(APP_NAME, f"Διαγραφή {len(sel)} εγγραφών;", parent=self.win):
            return
        ids = {it["id"] for it in sel}
        self.items = [it for it in self.items if it["id"] not in ids]
        self._save()
        self.clear_form()
        self.refresh()

    # ------------------------------------------------------------------ φόρμα
    def edit_selected(self, _e=None):
        sel = self._selected()
        if len(sel) != 1:
            return
        self.editing = sel[0]["id"]
        for key, var in self.vars.items():
            var.set(sel[0].get(key, ""))
        self.save_btn.configure(text="Αποθήκευση αλλαγών")

    def clear_form(self):
        self.editing = None
        for var in self.vars.values():
            var.set("")
        self.save_btn.configure(text="Προσθήκη")
        self.tree.selection_remove(self.tree.selection())

    def save_form(self):
        values = {k: v.get().strip() for k, v in self.vars.items()}
        try:
            rt.parse_date(values["due"])
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.win)
            return
        if not values["title"]:
            messagebox.showerror(APP_NAME, "Συμπλήρωσε την ταινία.", parent=self.win)
            return
        if self.editing:
            it = next(x for x in self.items if x["id"] == self.editing)
            it.update(values)
        else:
            cid, folder = self._films.get(values["title"], (None, ""))
            it = rt.new_return(values["title"], values["disk"], values["to"], values["contact"], values["due"],
                               values["notes"], cid, folder)
            self.items.append(it)
        self._save()
        self.clear_form()
        self.refresh()

    def export_csv(self):
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile="Επιστροφές_DCP.csv",
                                         filetypes=[("CSV (Excel)", "*.csv")])
        if p:
            rt.write_csv(self.items, p)
            os.startfile(p)
