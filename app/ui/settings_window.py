"""
Παράθυρο «Ρυθμίσεις» με καρτέλες: Γενικά, Φάκελοι, Αναπαραγωγή, Έλεγχοι, Φεστιβάλ, Δεδομένα.
Κάθε ρύθμιση υπάρχει μόνο εδώ· οι προεπιλογές είναι στο app/core/prefs.py.
"""
import glob
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.checks.scan import SCANS_DIR
from app.checks.spec import get_spec, set_spec
from app.core import prefs
from app.core.config import APP_DIR, APP_NAME, HASH_CACHE_FILE, SETTINGS_FILE, set_setting
from app.core.util import fmt_size
from app.ui.dialogs import SpecForm
from app.ui.i18n_tk import label_key

TABS = ("general", "folders", "playback", "checks", "festival", "data")
THEMES = {"auto": "Αυτόματα (όπως τα Windows)", "light": "Φωτεινό", "dark": "Σκούρο"}
METERS = {"auto": "Αυτόματα (με τα χειριστήρια)", "on": "Πάντα", "off": "Κρυφοί"}
DEFAULT_DEVICE = "Προεπιλογή των Windows"
FOLDERS = (("ingest_dest", "Φάκελος του φεστιβάλ (παραλαβές):"),
           ("convert_out", "Έξοδος «Βίντεο → DCP»:"),
           ("transcode_out", "Έξοδος «Μετατροπή βίντεο» (κενό = δίπλα στο πρωτότυπο):"),
           ("shots_dir", "Screenshots (κενό = Εικόνες\\ReelCheck):"))
NUMBERS = (  # (κλειδί, ετικέτα, ελάχιστο, μέγιστο)
    ("scan_black_min", "Μαύρη εικόνα: εύρημα από (δευτ.):", 2.5, 600),
    ("scan_silence_min", "Σιωπή: εύρημα από (δευτ.):", 3, 600),
    ("scan_edge_start", "Τίτλοι αρχής: πρώτα (λεπτά):", 0, 60),
    ("scan_edge_end", "Τίτλοι τέλους: τελευταία (λεπτά):", 0, 60),
    ("sub_min_seconds", "Υπότιτλος: ελάχιστη διάρκεια (δευτ.):", 0.1, 10),
    ("sub_max_seconds", "Υπότιτλος: μέγιστη διάρκεια (δευτ.):", 1, 60),
    ("sub_max_cps", "Υπότιτλος: έως χαρακτήρες/δευτ.:", 5, 60),
    ("sub_max_line", "Υπότιτλος: έως χαρακτήρες/γραμμή:", 10, 120),
    ("sub_max_lines", "Υπότιτλος: έως γραμμές:", 1, 6),
    ("lufs_target", "Στόχος έντασης (LUFS):", -40, -5),
    ("lufs_tolerance", "Ανοχή (± LU):", 0.5, 20),
)
EXPORT_KEYS = set(prefs.DEFAULTS) | {"festival_spec", "catalog", "convert_opts", "transcode_opts", "ingest_opts"}


class SettingsWindow:
    """on_saved(αλλαγμένα κλειδιά) καλείται μετά την αποθήκευση· tab: ποια καρτέλα να ανοίξει."""

    def __init__(self, parent, theme, on_saved, tab="general"):
        self.on_saved = on_saved
        self.palette = theme.palette
        self.vars = {}
        self.win = w = tk.Toplevel(parent)
        w.title("Ρυθμίσεις")
        w.geometry("720x560")
        w.minsize(640, 500)
        theme.toplevel(w, parent)
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.pages = {}
        for key, title, build in (("general", "Γενικά", self._general), ("folders", "Φάκελοι", self._folders),
                                  ("playback", "Αναπαραγωγή", self._playback), ("checks", "Έλεγχοι", self._checks),
                                  ("festival", "Φεστιβάλ", self._festival), ("data", "Δεδομένα", self._data)):
            page = ttk.Frame(self.nb, padding=14)
            self.nb.add(page, text=title)
            self.pages[key] = page
            build(page)
        self.nb.select(self.pages.get(tab, self.pages["general"]))
        bar = ttk.Frame(w, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Αποθήκευση", command=self.save).pack(side="right")
        ttk.Button(bar, text="Άκυρο", command=w.destroy).pack(side="right", padx=6)
        w.grab_set()

    # ================================================================== βοηθητικά
    def _check(self, page, key, text, row):
        var = tk.IntVar(value=1 if prefs.get(key) else 0)
        ttk.Checkbutton(page, text=text, variable=var).grid(row=row, column=0, columnspan=3, sticky="w", pady=3)
        self.vars[key] = ("bool", var)

    def _combo(self, page, key, label, labels, row, width=32):
        ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=labels.get(prefs.get(key), list(labels.values())[0]))
        ttk.Combobox(page, textvariable=var, values=list(labels.values()), state="readonly", width=width).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=3)
        self.vars[key] = ("choice", var, labels)

    def _number(self, page, key, label, row, lo, hi):
        ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=f"{prefs.get(key):g}")
        ttk.Entry(page, textvariable=var, width=8).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(page, text=f"(προεπιλογή {prefs.DEFAULTS[key]:g})").grid(row=row, column=2, sticky="w", padx=8)
        self.vars[key] = ("number", var, lo, hi, label)

    # ================================================================== καρτέλες
    def _general(self, page):
        box = ttk.LabelFrame(page, text=" Γλώσσα / Language ", padding=10)
        box.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 10))
        current = prefs.get("language") if prefs.get("language") in ("el", "en") else "el"
        lang = tk.StringVar(value=current)
        for key, text in (("el", "Ελληνικά"), ("en", "English")):
            rb = ttk.Radiobutton(box, value=key, variable=lang)
            rb.pack(anchor="w", pady=2)
            rb.tk.call(rb._w, "configure", "-text", text)  # τα ονόματα γλωσσών δεν μεταφράζονται
        ttk.Label(box, text="Ισχύει μετά από επανεκκίνηση.").pack(anchor="w", pady=(4, 0))
        self.vars["language"] = ("raw", lang)
        self._combo(page, "theme", "Θέμα:", THEMES, 1)
        self._check(page, "update_auto", "Έλεγχος για νέα έκδοση μία φορά την ημέρα", 2)
        self._check(page, "open_last_folder", "Άνοιγμα στον τελευταίο φάκελο κατά την εκκίνηση", 3)

    def _folders(self, page):
        page.columnconfigure(0, weight=1)
        r = 0
        for key, label in FOLDERS:
            ttk.Label(page, text=label).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 2))
            var = tk.StringVar(value=prefs.get(key))
            ttk.Entry(page, textvariable=var).grid(row=r + 1, column=0, sticky="we")
            ttk.Button(page, text="…", width=3, command=lambda v=var: self._pick_folder(v)).grid(
                row=r + 1, column=1, padx=4)
            self.vars[key] = ("folder", var)
            r += 2

    def _pick_folder(self, var):
        d = filedialog.askdirectory(parent=self.win, initialdir=var.get() or None)
        if d:
            var.set(os.path.normpath(d))

    def _playback(self, page):
        from app.player.audio import output_devices
        self._check(page, "play_fullscreen", "Ξεκινά σε πλήρη οθόνη", 0)
        self._check(page, "play_half", "Ξεκινά σε μισή ανάλυση (για αργά PC)", 1)
        devices = {"": DEFAULT_DEVICE, **{d: d for d in output_devices()}}
        saved = prefs.get("audio_device")
        if saved and saved not in devices:  # αποσυνδεδεμένη συσκευή: φαίνεται, αλλά παίζει η προεπιλεγμένη
            devices[saved] = f"{saved} (δεν είναι συνδεδεμένη)"
        self._combo(page, "audio_device", "Συσκευή ήχου:", devices, 2, width=48)
        ttk.Label(page, text="Αρχική ένταση (%):").grid(row=3, column=0, sticky="w", pady=3)
        vol = tk.IntVar(value=prefs.get("start_volume"))
        p = self.palette
        tk.Scale(page, from_=0, to=100, orient="horizontal", variable=vol, length=240, showvalue=True,
                 highlightthickness=0, bg=p["bg"], fg=p["fg"], troughcolor=p["alt"], activebackground=p["accent"],
                 bd=0).grid(row=3, column=1, columnspan=2, sticky="w")
        self.vars["start_volume"] = ("int", vol)
        self._check(page, "subs_on", "Υπότιτλοι ανοιχτοί στην αρχή", 4)
        self._combo(page, "meters", "Μετρητές ήχου:", METERS, 5)
        self._number(page, "jump_preroll", "Κλικ σε εύρημα: ξεκινά τόσα δευτ. πριν:", 6, 0, 60)

    def _checks(self, page):
        r = 0
        for key, label, lo, hi in NUMBERS:
            if key == "lufs_target":
                self._check(page, "lufs_check", "Προειδοποίηση όταν η ένταση είναι εκτός στόχου", r)
                r += 1
            self._number(page, key, label, r, lo, hi)
            r += 1
        ttk.Button(page, text="Επαναφορά προεπιλογών", command=self._reset_checks).grid(
            row=r, column=0, sticky="w", pady=(10, 0))

    def _reset_checks(self):
        for key, *_ in NUMBERS:
            self.vars[key][1].set(f"{prefs.DEFAULTS[key]:g}")
        self.vars["lufs_check"][1].set(1 if prefs.DEFAULTS["lufs_check"] else 0)

    def _festival(self, page):
        self.spec_form = SpecForm(page, get_spec())

    def _data(self, page):
        ttk.Label(page, text="Φάκελος δεδομένων (log, σημειώσεις, σαρώσεις, ρυθμίσεις):").pack(anchor="w")
        ttk.Label(page, text=APP_DIR).pack(anchor="w", pady=(0, 8))
        for text, cmd in (("Άνοιγμα φακέλου δεδομένων", lambda: os.startfile(APP_DIR)),
                          (self._hash_label(), self.clear_hash), (self._scans_label(), self.clear_scans),
                          ("Εξαγωγή ρυθμίσεων…", self.export_settings), ("Εισαγωγή ρυθμίσεων…", self.import_settings)):
            ttk.Button(page, text=text, command=cmd).pack(anchor="w", pady=3, fill="x")
        ttk.Label(page, text="Η εξαγωγή / εισαγωγή μεταφέρει τις ρυθμίσεις σε άλλον υπολογιστή.",
                  wraplength=600).pack(anchor="w", pady=(8, 0))

    # ================================================================== δεδομένα
    @staticmethod
    def _hash_label():
        size = os.path.getsize(HASH_CACHE_FILE) if os.path.isfile(HASH_CACHE_FILE) else 0
        return f"Καθαρισμός cache των hash ({fmt_size(size)})"

    @staticmethod
    def _scans_label():
        return f"Καθαρισμός αποθηκευμένων σαρώσεων ({len(glob.glob(os.path.join(SCANS_DIR, '*.json')))})"

    def clear_hash(self):
        if messagebox.askyesno(APP_NAME, "Να σβηστεί η cache των hash; Ο επόμενος έλεγχος hash θα ξαναδιαβάσει τα αρχεία.",
                               parent=self.win):
            try:
                os.remove(HASH_CACHE_FILE)
            except OSError:
                pass
            self.on_saved({"hash_cleared"})

    def clear_scans(self):
        files = glob.glob(os.path.join(SCANS_DIR, "*.json"))
        if files and messagebox.askyesno(APP_NAME, f"Να σβηστούν {len(files)} αποθηκευμένες σαρώσεις;", parent=self.win):
            for f in files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            self.on_saved({"scans_cleared"})

    def export_settings(self):
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".json", initialfile="ReelCheck_ρυθμίσεις.json",
                                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        with open(p, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in data.items() if k in EXPORT_KEYS}, f, ensure_ascii=False, indent=1)
        messagebox.showinfo(APP_NAME, f"Αποθηκεύτηκε:\n{p}", parent=self.win)

    def import_settings(self):
        p = filedialog.askopenfilename(parent=self.win, filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, ValueError):
            messagebox.showerror(APP_NAME, "Δεν είναι αρχείο ρυθμίσεων του ReelCheck.", parent=self.win)
            return
        keys = {k for k in data if k in EXPORT_KEYS}
        for k in keys:
            set_setting(k, data[k])
        self.win.destroy()
        self.on_saved(keys | {"imported"})

    # ================================================================== αποθήκευση
    def _values(self):
        """{κλειδί: τιμή} από τη φόρμα· ValueError (μήνυμα, καρτέλα) αν κάτι δεν είναι σωστό."""
        out = {}
        for key, spec in self.vars.items():
            kind, var = spec[0], spec[1]
            if kind == "bool":
                out[key] = bool(var.get())
            elif kind in ("raw", "folder"):
                out[key] = var.get().strip()
            elif kind == "int":
                out[key] = int(var.get())
            elif kind == "choice":
                out[key] = label_key(var, spec[2])
            elif kind == "number":
                lo, hi, label = spec[2], spec[3], spec[4]
                try:
                    v = float(var.get().replace(",", "."))
                except ValueError:
                    raise ValueError((f"«{label.rstrip(':')}» θέλει αριθμό.", "checks" if key != "jump_preroll"
                                      else "playback"))
                if not lo <= v <= hi:
                    raise ValueError((f"«{label.rstrip(':')}»: από {lo:g} έως {hi:g}.",
                                      "checks" if key != "jump_preroll" else "playback"))
                out[key] = int(v) if isinstance(prefs.DEFAULTS[key], int) else v
        try:
            out["festival_spec"] = self.spec_form.values()
        except ValueError as e:
            raise ValueError((str(e), "festival"))
        return out

    def save(self):
        try:
            values = self._values()
        except ValueError as e:
            msg, tab = e.args[0]
            self.nb.select(self.pages[tab])
            messagebox.showerror(APP_NAME, msg, parent=self.win)
            return
        spec = values.pop("festival_spec")
        changed = {k for k, v in values.items() if v != prefs.get(k)}
        if spec != get_spec():
            set_spec(spec)
            changed.add("festival_spec")
        for k in changed - {"festival_spec"}:
            prefs.set(k, values[k])
        self.win.destroy()
        if changed:
            self.on_saved(changed)


