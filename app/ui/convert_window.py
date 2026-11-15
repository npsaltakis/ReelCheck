"""Παράθυρο «Μετατροπή βίντεο σε DCP»: λίστα αρχείων με σειρά, ρυθμίσεις και έναρξη."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.ui.i18n_tk import label_key
from app.core.config import APP_NAME, VIDEO_EXT, get_setting, set_setting
from app.core.util import fmt_time
from app.tools.convert import DEFAULT_OPTS, warnings_for
from app.tools.dcpomatic import CONTENT_TYPES
from app.tools.mediainfo import probe_media

KIND_LABELS = {"SHR": "Μικρού μήκους (SHR)", "FTR": "Μεγάλου μήκους (FTR)", "TLR": "Τρέιλερ (TLR)",
               "ADV": "Διαφήμιση (ADV)", "PSA": "Ανακοίνωση (PSA)", "TSR": "Teaser (TSR)"}
RATIO_LABELS = {"185": "Flat 1.85", "239": "Scope 2.39"}
FPS_LABELS = {"": "αυτόματα", "24": "24", "25": "25", "30": "30", "48": "48"}
COLUMNS = (("n", "#", 36), ("file", "Αρχείο", 330), ("dur", "Διάρκεια", 80), ("fps", "fps", 50),
           ("size", "Εικόνα", 90), ("ch", "Ήχος", 60), ("srt", "Υπότιτλοι", 90))
CONVERT_EXT = tuple(e for e in VIDEO_EXT if e != ".mxf")


class ConvertWindow:
    """on_start(infos, opts, out_dir) ξεκινά τη μετατροπή· on_install() εγκαθιστά το DCP-o-matic."""

    def __init__(self, parent, theme, tools, on_start, on_install):
        self.parent, self.theme, self.tools = parent, theme, tools
        self.on_start, self.on_install = on_start, on_install
        self.infos = []
        self.win = w = tk.Toplevel(parent)
        w.title("Μετατροπή βίντεο σε DCP")
        w.geometry("960x640")
        w.minsize(820, 560)
        theme.toplevel(w, parent)
        self._build_files()
        self._build_options()
        self._build_footer()
        self.set_tools(tools)

    # ------------------------------------------------------------------ κατασκευή
    def _build_files(self):
        f = ttk.Frame(self.win, padding=(10, 10, 10, 0))
        f.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("Προσθήκη αρχείων…", self.add_files), ("Προσθήκη φακέλου…", self.add_folder),
                          ("Αφαίρεση", self.remove), ("▲ Πάνω", lambda: self.move(-1)), ("▼ Κάτω", lambda: self.move(1))):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.total_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.total_var).pack(side="right")
        self.tree = ttk.Treeview(f, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended")
        for key, text, width in COLUMNS:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w", stretch=key == "file")
        self.tree.pack(fill="both", expand=True)
        self.warn_var = tk.StringVar()
        self.warn_label = ttk.Label(f, textvariable=self.warn_var, wraplength=900, justify="left")
        self.warn_label.pack(fill="x", pady=(6, 0))

    def _combo(self, parent, row, col, label, labels, value):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6))
        var = tk.StringVar(value=labels.get(value, list(labels.values())[0]))
        cb = ttk.Combobox(parent, textvariable=var, values=list(labels.values()), state="readonly", width=22)
        cb.grid(row=row, column=col + 1, sticky="w", padx=(0, 18))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_warnings())
        return var, labels

    def _build_options(self):
        saved = dict(DEFAULT_OPTS, **(get_setting("convert_opts", {}) or {}))
        o = ttk.LabelFrame(self.win, text=" Ρυθμίσεις ", padding=10)
        o.pack(fill="x", padx=10, pady=8)
        self.mode = tk.StringVar(value=saved["mode"])
        ttk.Radiobutton(o, text="Ένα DCP με όλα στη σειρά (σαν μία ταινία)", value="single", variable=self.mode,
                        command=self.refresh_warnings).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(o, text="Ξεχωριστό DCP για κάθε αρχείο", value="separate", variable=self.mode,
                        command=self.refresh_warnings).grid(row=0, column=3, columnspan=3, sticky="w")
        ttk.Label(o, text="Τίτλος (για ένα DCP):").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.name = tk.StringVar(value=saved["name"])
        ttk.Entry(o, textvariable=self.name, width=25).grid(row=1, column=1, sticky="w", padx=(0, 18))
        kinds = {k: KIND_LABELS.get(k, k) for k in CONTENT_TYPES if k in KIND_LABELS}
        self.kind = self._combo(o, 1, 2, "Είδος:", kinds, saved["kind"])
        self.ratio = self._combo(o, 2, 0, "Κάδρο:", RATIO_LABELS, saved["ratio"])
        self.fps = self._combo(o, 2, 2, "fps:", FPS_LABELS, str(saved["fps"] or ""))
        ttk.Label(o, text="Κενό ανάμεσα (δευτ.):").grid(row=3, column=0, sticky="w", padx=(0, 6))
        self.gap = tk.StringVar(value=f"{float(saved['gap']):g}")
        ttk.Entry(o, textvariable=self.gap, width=6).grid(row=3, column=1, sticky="w")
        ttk.Label(o, text="Γλώσσα υποτίτλων (π.χ. EL):").grid(row=3, column=2, sticky="w", padx=(0, 6))
        self.sub_lang = tk.StringVar(value=saved["sub_lang"])
        ttk.Entry(o, textvariable=self.sub_lang, width=6).grid(row=3, column=3, sticky="w")
        self.normalize = tk.IntVar(value=1 if saved["normalize"] else 0)
        ttk.Checkbutton(o, text="Ίδια ένταση για όλα", variable=self.normalize).grid(row=4, column=0, columnspan=2,
                                                                                    sticky="w", pady=(6, 0))
        self.interop = tk.IntVar(value=1 if saved["standard"] == "interop" else 0)
        ttk.Checkbutton(o, text="Interop (αντί SMPTE, μόνο για παλιούς servers)", variable=self.interop).grid(
            row=4, column=2, columnspan=3, sticky="w", pady=(6, 0))
        for r in range(5):
            o.grid_rowconfigure(r, pad=4)

    def _build_footer(self):
        f = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        f.pack(fill="x")
        ttk.Label(f, text="Φάκελος εξόδου:").pack(side="left")
        self.out_dir = tk.StringVar(value=get_setting("convert_out", "") or "")
        ttk.Entry(f, textvariable=self.out_dir, width=40).pack(side="left", padx=6)
        ttk.Button(f, text="…", width=3, command=self.choose_out).pack(side="left")
        self.start_btn = ttk.Button(f, text="▶  Μετατροπή σε DCP", command=self.start)
        self.start_btn.pack(side="right")
        g = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        g.pack(fill="x")
        self.tools_var = tk.StringVar()
        ttk.Label(g, textvariable=self.tools_var).pack(side="left")
        self.install_btn = ttk.Button(g, text="Κατέβασμα και εγκατάσταση DCP-o-matic", command=self.on_install)

    # ------------------------------------------------------------------ DCP-o-matic
    def set_tools(self, tools):
        self.tools = tools
        if tools:
            self.tools_var.set(f"DCP-o-matic {tools['version']}: έτοιμο ({tools['dir']})")
            self.install_btn.pack_forget()
            self.start_btn.state(["!disabled"])
        else:
            self.tools_var.set("Δεν βρέθηκε το DCP-o-matic (δωρεάν, dcpomatic.com). Χρειάζεται για τη μετατροπή.")
            self.install_btn.pack(side="right")
            self.start_btn.state(["disabled"])

    # ------------------------------------------------------------------ αρχεία
    def add_paths(self, paths):
        known = {os.path.normcase(i["path"]) for i in self.infos}
        for p in paths:
            if os.path.normcase(p) not in known and p.lower().endswith(CONVERT_EXT):
                self.infos.append(probe_media(p))
                known.add(os.path.normcase(p))
        self.refresh()

    def add_files(self):
        paths = filedialog.askopenfilenames(parent=self.win, title="Βίντεο για DCP", filetypes=[
            ("Βίντεο", " ".join("*" + e for e in CONVERT_EXT)), ("Όλα", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(parent=self.win, title="Φάκελος με βίντεο")
        if d:
            self.add_paths([os.path.join(d, f) for f in sorted(os.listdir(d))])

    def selected(self):
        return [int(i) for i in self.tree.selection()]

    def remove(self):
        for i in sorted(self.selected(), reverse=True):
            del self.infos[i]
        self.refresh()

    def move(self, step):
        sel = self.selected()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + step
        if 0 <= j < len(self.infos):
            self.infos[i], self.infos[j] = self.infos[j], self.infos[i]
            self.refresh()
            self.tree.selection_set(str(j))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for n, i in enumerate(self.infos):
            vals = (n + 1, os.path.basename(i["path"]), fmt_time(i["duration"]) if not i["error"] else "σφάλμα",
                    f"{i['fps']:g}" if i["fps"] else "?", f"{i['width']}x{i['height']}" if i["width"] else "?",
                    f"{i['channels']}ch" if i["channels"] else "όχι",
                    os.path.basename(i["srt"]) if i["srt"] else "—")
            self.tree.insert("", "end", iid=str(n), values=vals)
        total = sum(i["duration"] for i in self.infos if not i["error"])
        self.total_var.set(f"{len(self.infos)} αρχεία · συνολικά {fmt_time(total)}")
        self.refresh_warnings()

    def refresh_warnings(self):
        w = warnings_for(self.infos, self.options()) if self.infos else []
        self.warn_var.set("⚠ " + "\n⚠ ".join(w) if w else "")

    # ------------------------------------------------------------------ έναρξη
    def options(self):
        def key(var_labels):
            var, labels = var_labels
            return label_key(var, labels)
        try:
            gap = max(0.0, float(self.gap.get().replace(",", ".") or 0))
        except ValueError:
            gap = 0.0
        fps = key(self.fps)
        return {"mode": self.mode.get(), "name": self.name.get().strip() or "Program", "kind": key(self.kind),
                "ratio": key(self.ratio), "fps": int(fps) if fps else None, "gap": gap,
                "sub_lang": self.sub_lang.get().strip().lower(), "normalize": bool(self.normalize.get()),
                "standard": "interop" if self.interop.get() else "SMPTE", "reel_per_film": True}

    def choose_out(self):
        d = filedialog.askdirectory(parent=self.win, title="Πού να γραφτούν τα DCP;")
        if d:
            self.out_dir.set(os.path.normpath(d))

    def start(self):
        good = [i for i in self.infos if not i["error"]]
        if not good:
            messagebox.showinfo(APP_NAME, "Πρόσθεσε πρώτα αρχεία βίντεο.", parent=self.win)
            return
        out = self.out_dir.get().strip()
        if not out:
            self.choose_out()
            out = self.out_dir.get().strip()
            if not out:
                return
        out = os.path.normpath(out)
        opts = self.options()
        set_setting("convert_opts", opts)
        set_setting("convert_out", out)
        self.on_start(good, opts, out)
        self.win.destroy()
