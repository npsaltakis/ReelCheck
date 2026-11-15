"""Παράθυρο «Πρόγραμμα προβολών»: προσθήκη/αλλαγή/διαγραφή προβολών, συγκρούσεις, κατάσταση DCP, εξαγωγή."""
import datetime
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core.config import APP_NAME
from app.tools import schedule as sch
from app.ui.widgets import make_tree

COLUMNS = (("date", "Ημερομηνία", 90), ("time", "Ώρα", 55), ("end", "Λήξη", 55), ("room", "Αίθουσα", 110),
           ("title", "Ταινία", 360), ("dcp", "DCP", 110), ("notes", "Σημειώσεις", 220))


class ScheduleWindow:
    """
    films(): [(τίτλος, cpl_id, λεπτά)] από τα DCP του κύριου παραθύρου.
    status_of(cpl_id): 'OK' | 'ΠΡΟΣΟΧΗ' | 'ΣΦΑΛΜΑ' | '' (αν δεν είναι φορτωμένο).
    details_of(cpl_id): {"format", "audio", "subs", "kdm", "duration"} για τα φύλλα προβολής.
    """

    def __init__(self, parent, theme, films, status_of, details_of=None):
        self.films, self.status_of, self.details_of = films, status_of, details_of
        self.items = sch.load()
        self.editing = None
        self.win = w = tk.Toplevel(parent)
        w.title("Πρόγραμμα προβολών")
        w.geometry("1080x640")
        w.minsize(900, 520)
        theme.toplevel(w, parent)
        self.theme = theme
        self._build_list()
        self._build_form()
        self.refresh()

    # ------------------------------------------------------------------ κατασκευή
    def _build_list(self):
        f = ttk.Frame(self.win, padding=(10, 10, 10, 0))
        f.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("Διαγραφή", self.delete), ("Εκτύπωση / HTML…", self.export_html),
                          ("Φύλλα μηχανικού προβολής…", self.export_sheets), ("Ημερολόγιο (.ics)…", self.export_ics),
                          ("Εξαγωγή CSV…", self.export_csv)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.info_var).pack(side="right")
        self.tree = make_tree(f, COLUMNS, stretch=("title", "notes"), selectmode="extended")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.edit_selected)
        self.theme.tag_tree(self.tree, extra=[("clash", "err")])

    def _build_form(self):
        o = ttk.LabelFrame(self.win, text=" Προβολή ", padding=10)
        o.pack(fill="x", padx=10, pady=10)
        today = datetime.date.today().strftime(sch.DATE_FMT)
        self.date, self.time = tk.StringVar(value=today), tk.StringVar(value="20:30")
        self.room, self.film = tk.StringVar(value="Αίθουσα 1"), tk.StringVar()
        self.minutes, self.extra, self.notes = tk.StringVar(), tk.StringVar(value="0"), tk.StringVar()
        row = [("Ημερομηνία (ΗΗ/ΜΜ/ΕΕΕΕ):", self.date, 12), ("Ώρα:", self.time, 7)]
        for c, (label, var, width) in enumerate(row):
            ttk.Label(o, text=label).grid(row=0, column=c * 2, sticky="w", padx=(0, 6))
            ttk.Entry(o, textvariable=var, width=width).grid(row=0, column=c * 2 + 1, sticky="w", padx=(0, 16))
        ttk.Label(o, text="Αίθουσα:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.room_cb = ttk.Combobox(o, textvariable=self.room, width=16)
        self.room_cb.grid(row=0, column=5, sticky="w")
        ttk.Label(o, text="Ταινία:").grid(row=1, column=0, sticky="w", pady=6)
        self.film_cb = ttk.Combobox(o, textvariable=self.film, width=70)
        self.film_cb.grid(row=1, column=1, columnspan=5, sticky="w", pady=6)
        self.film_cb.bind("<<ComboboxSelected>>", self._film_chosen)
        ttk.Label(o, text="Διάρκεια (λεπτά):").grid(row=2, column=0, sticky="w")
        ttk.Entry(o, textvariable=self.minutes, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(o, text="Επιπλέον (εισαγωγή, Q&A):").grid(row=2, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(o, textvariable=self.extra, width=6).grid(row=2, column=3, sticky="w")
        ttk.Label(o, text="Σημειώσεις:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(o, textvariable=self.notes, width=70).grid(row=3, column=1, columnspan=5, sticky="w", pady=(6, 0))
        bf = ttk.Frame(o)
        bf.grid(row=4, column=0, columnspan=6, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Καθαρισμός", command=self.clear_form).pack(side="right")
        self.save_btn = ttk.Button(bf, text="Προσθήκη", command=self.save_form)
        self.save_btn.pack(side="right", padx=6)

    # ------------------------------------------------------------------ λίστα
    def _film_list(self):
        self._films = {t: (cid, m) for t, cid, m in self.films()}
        return list(self._films)

    def _film_chosen(self, _e=None):
        cid_min = getattr(self, "_films", {}).get(self.film.get())
        if cid_min:
            self.minutes.set(f"{cid_min[1]:.0f}")

    def dcp_status(self, s):
        if not s.get("cpl_id"):
            return "χωρίς DCP"
        return self.status_of(s["cpl_id"]) or "δεν φορτώθηκε"

    def refresh(self):
        self.film_cb.configure(values=self._film_list())
        self.room_cb.configure(values=sorted({s["room"] for s in self.items} | {"Αίθουσα 1"}))
        clash = sch.conflicts(self.items)
        self.tree.delete(*self.tree.get_children())
        for s in sorted(self.items, key=sch.sort_key):
            try:
                end = sch.start_end(s)[1].strftime(sch.TIME_FMT)
            except ValueError:
                end = "?"
            st = self.dcp_status(s)
            notes = ("ΣΥΓΚΡΟΥΣΗ ΩΡΑΣ · " if s["id"] in clash else "") + s["notes"]
            tag = "clash" if s["id"] in clash else (st.split()[0] if st else "")
            self.tree.insert("", "end", iid=s["id"], tags=(tag,),
                             values=(s["date"], s["time"], end, s["room"], s["title"], st, notes))
        n_clash = len(clash)
        self.info_var.set(f"{len(self.items)} προβολές" + (f" · {n_clash} με σύγκρουση ώρας" if n_clash else ""))

    # ------------------------------------------------------------------ επεξεργασία
    def edit_selected(self, _e=None):
        sel = self.tree.selection()
        if len(sel) != 1:
            return
        s = next(x for x in self.items if x["id"] == sel[0])
        self.editing = s["id"]
        for var, key in ((self.date, "date"), (self.time, "time"), (self.room, "room"), (self.film, "title"),
                         (self.notes, "notes")):
            var.set(s[key])
        self.minutes.set(f"{s['minutes']:.0f}")
        self.extra.set(f"{s['extra']:.0f}")
        self.save_btn.configure(text="Αποθήκευση αλλαγών")

    def clear_form(self):
        self.editing = None
        self.film.set("")
        self.minutes.set("")
        self.notes.set("")
        self.save_btn.configure(text="Προσθήκη")
        self.tree.selection_remove(self.tree.selection())

    def save_form(self):
        try:
            sch.parse_when(self.date.get(), self.time.get())
            minutes = float(self.minutes.get().replace(",", ".") or 0)
            extra = float(self.extra.get().replace(",", ".") or 0)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e) if "ΗΗ" in str(e) else "Η διάρκεια θέλει αριθμό λεπτών.",
                                 parent=self.win)
            return
        if not self.film.get().strip() or not self.room.get().strip():
            messagebox.showerror(APP_NAME, "Συμπλήρωσε ταινία και αίθουσα.", parent=self.win)
            return
        cid = getattr(self, "_films", {}).get(self.film.get(), (None, 0))[0]
        s = sch.new_screening(self.date.get().strip(), self.time.get().strip(), self.room.get(), self.film.get(),
                              minutes, extra, cid, self.notes.get())
        if self.editing:
            s["id"] = self.editing
            self.items = [s if x["id"] == self.editing else x for x in self.items]
        else:
            self.items.append(s)
        sch.save(self.items)
        self.clear_form()
        self.refresh()

    def delete(self):
        ids = set(self.tree.selection())
        if not ids or not messagebox.askyesno(APP_NAME, f"Διαγραφή {len(ids)} προβολών;", parent=self.win):
            return
        self.items = [x for x in self.items if x["id"] not in ids]
        sch.save(self.items)
        self.clear_form()
        self.refresh()

    # ------------------------------------------------------------------ εξαγωγή
    def export_html(self):
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".html", initialfile="Πρόγραμμα_προβολών.html",
                                         filetypes=[("HTML", "*.html")])
        if p:
            sch.write_html(self.items, p, self.dcp_status)
            os.startfile(p)

    def export_sheets(self):
        p = self._ask_save("Φύλλα_προβολής.html", ("HTML", "*.html"))
        if p:
            sch.write_day_sheets(self.items, p, self._sheet_details)
            os.startfile(p)

    def _sheet_details(self, s):
        d = dict(self.details_of(s["cpl_id"]) or {}) if self.details_of and s.get("cpl_id") else {}
        d["status"] = self.dcp_status(s)
        return d

    def export_ics(self):
        p = self._ask_save("Πρόγραμμα_προβολών.ics", ("Ημερολόγιο", "*.ics"))
        if p:
            sch.write_ics(self.items, p, self.dcp_status)
            messagebox.showinfo(APP_NAME, "Έτοιμο. Το .ics ανοίγει στο Outlook ή εισάγεται στο Google Calendar "
                                          "(Ρυθμίσεις → Εισαγωγή).", parent=self.win)

    def _ask_save(self, name, ftype):
        return filedialog.asksaveasfilename(parent=self.win, defaultextension=os.path.splitext(name)[1],
                                            initialfile=name, filetypes=[ftype])

    def export_csv(self):
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile="Πρόγραμμα_προβολών.csv",
                                         filetypes=[("CSV (Excel)", "*.csv")])
        if p:
            sch.write_csv(self.items, p, self.dcp_status)
            os.startfile(p)
