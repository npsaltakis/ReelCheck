"""Παράθυρο «Μετατροπή βίντεο»: MOV → MP4 κ.λπ., και DCP → MP4 (screener)."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core import prefs
from app.ui.i18n_tk import label_key
from app.ui.widgets import ask_folder, labeled_combo, make_tree
from app.core.config import APP_NAME, VIDEO_EXT, get_setting, set_setting
from app.core.util import fmt_time
from app.tools.mediainfo import find_srt
from app.tools.transcode import AUDIO_MODES, PRESETS, QUALITY_LABELS, SIZE_LABELS, SUB_MODES, probe

COLUMNS = (("file", "Αρχείο / DCP", 360), ("dur", "Διάρκεια", 80), ("codec", "Κωδικοποίηση", 110),
           ("size", "Εικόνα", 90), ("ch", "Ήχος", 60), ("srt", "Υπότιτλοι", 110))
INPUT_EXT = tuple(e for e in VIDEO_EXT if e != ".mxf")
DEFAULTS = {"preset": "h264", "quality": "normal", "max_h": 0, "audio": "stereo", "subs": "copy", "gpu": True,
            "remux": True}


def file_item(path):
    """Στοιχείο λίστας για αρχείο βίντεο."""
    item = {"kind": "file", "path": path, "srt": find_srt(path), "error": None}
    try:
        item.update(probe(path))
    except Exception as e:
        item.update(error=str(e), duration=0.0, vcodec=None, width=0, height=0, channels=0)
    return item


def dcp_item(cpl, info=None, subs_label=""):
    """Στοιχείο λίστας για DCP (CPL) που είναι ήδη φορτωμένο στο κύριο παράθυρο."""
    size = (info or {}).get("size") or (0, 0)
    return {"kind": "dcp", "path": cpl.title, "cpl": cpl, "srt": None, "error": None, "duration": cpl.seconds,
            "vcodec": "DCP JPEG2000", "width": size[0], "height": size[1], "channels": (info or {}).get("ch", 0),
            "subs_label": subs_label}


class TranscodeWindow:
    """on_start(items, preset, opts, out_dir ή None για «δίπλα στο πρωτότυπο»)."""

    def __init__(self, parent, theme, on_start, items=(), gpu_label=None):
        self.on_start = on_start
        self.items = list(items)
        self.win = w = tk.Toplevel(parent)
        w.title("Μετατροπή βίντεο")
        w.geometry("940x600")
        w.minsize(800, 520)
        theme.toplevel(w, parent)
        self._build_files()
        self._build_options(gpu_label)
        self._build_footer()
        self.refresh()

    # ------------------------------------------------------------------ κατασκευή
    def _build_files(self):
        f = ttk.Frame(self.win, padding=(10, 10, 10, 0))
        f.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("Προσθήκη αρχείων…", self.add_files), ("Προσθήκη φακέλου…", self.add_folder),
                          ("Αφαίρεση", self.remove)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.total_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.total_var).pack(side="right")
        self.tree = make_tree(f, COLUMNS, stretch=("file",), selectmode="extended")
        self.tree.pack(fill="both", expand=True)

    def _combo(self, parent, row, col, label, labels, value):
        return labeled_combo(parent, row, col, label, labels, value, width=26)

    def _build_options(self, gpu_label):
        s = dict(DEFAULTS, **(get_setting("transcode_opts", {}) or {}))
        o = ttk.LabelFrame(self.win, text=" Ρυθμίσεις ", padding=10)
        o.pack(fill="x", padx=10, pady=8)
        self.preset = self._combo(o, 0, 0, "Σε:", {k: v["label"] for k, v in PRESETS.items()}, s["preset"])
        self.quality = self._combo(o, 0, 2, "Ποιότητα:", QUALITY_LABELS, s["quality"])
        self.size = self._combo(o, 1, 0, "Ανάλυση:", SIZE_LABELS, s["max_h"])
        self.audio = self._combo(o, 1, 2, "Ήχος:", AUDIO_MODES, s["audio"])
        self.subs = self._combo(o, 2, 0, "Υπότιτλοι .srt:", SUB_MODES, s["subs"])
        self.gpu = tk.IntVar(value=1 if s["gpu"] and gpu_label else 0)
        gpu_text = f"Κάρτα γραφικών ({gpu_label}): πιο γρήγορα" if gpu_label else "Κάρτα γραφικών: δεν βρέθηκε συμβατή"
        cb = ttk.Checkbutton(o, text=gpu_text, variable=self.gpu)
        cb.grid(row=2, column=2, columnspan=2, sticky="w")
        if not gpu_label:
            cb.state(["disabled"])
        self.remux = tk.IntVar(value=1 if s["remux"] else 0)
        ttk.Checkbutton(o, text="Χωρίς επανακωδικοποίηση όταν γίνεται (π.χ. MOV H.264 → MP4: γρήγορο, χωρίς απώλεια)",
                        variable=self.remux).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(o, text="Τα DCP βγαίνουν πάντα με τους υπότιτλους τους «καμένους» στην εικόνα (για screener)."
                  ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for r in range(5):
            o.grid_rowconfigure(r, pad=4)

    def _build_footer(self):
        f = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        f.pack(fill="x")
        ttk.Label(f, text="Φάκελος εξόδου:").pack(side="left")
        self.out_dir = tk.StringVar(value=prefs.folder("transcode_out"))
        ttk.Entry(f, textvariable=self.out_dir, width=42).pack(side="left", padx=6)
        ttk.Button(f, text="…", width=3, command=self.choose_out).pack(side="left")
        ttk.Label(f, text="(κενό = δίπλα στο πρωτότυπο)").pack(side="left", padx=6)
        ttk.Button(f, text="▶  Έναρξη", command=self.start).pack(side="right")

    # ------------------------------------------------------------------ αρχεία
    def add_paths(self, paths):
        known = {os.path.normcase(i["path"]) for i in self.items}
        for p in paths:
            if p.lower().endswith(INPUT_EXT) and os.path.normcase(p) not in known:
                self.items.append(file_item(p))
                known.add(os.path.normcase(p))
        self.refresh()

    def add_files(self):
        paths = filedialog.askopenfilenames(parent=self.win, title="Βίντεο για μετατροπή", filetypes=[
            ("Βίντεο", " ".join("*" + e for e in INPUT_EXT)), ("Όλα", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def add_folder(self):
        d = ask_folder(self.win, "Φάκελος με βίντεο")
        if d:
            self.add_paths([os.path.join(d, f) for f in sorted(os.listdir(d))])

    def remove(self):
        for i in sorted((int(x) for x in self.tree.selection()), reverse=True):
            del self.items[i]
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for n, it in enumerate(self.items):
            name = os.path.basename(it["path"]) if it["kind"] == "file" else f"DCP: {it['path']}"
            subs = os.path.basename(it["srt"]) if it.get("srt") else (it.get("subs_label") or "—")
            vals = (name, fmt_time(it["duration"]) if not it["error"] else "σφάλμα", it.get("vcodec") or "?",
                    f"{it['width']}x{it['height']}" if it.get("width") else "?",
                    f"{it['channels']}ch" if it.get("channels") else "όχι", subs)
            self.tree.insert("", "end", iid=str(n), values=vals)
        total = sum(i["duration"] for i in self.items if not i["error"])
        self.total_var.set(f"{len(self.items)} αρχεία · συνολικά {fmt_time(total)}")

    # ------------------------------------------------------------------ έναρξη
    @staticmethod
    def _key(var_labels):
        var, labels = var_labels
        return label_key(var, labels)

    def options(self):
        return {"preset": self._key(self.preset), "quality": self._key(self.quality), "max_h": self._key(self.size),
                "audio": self._key(self.audio), "subs": self._key(self.subs), "gpu": bool(self.gpu.get()),
                "remux": bool(self.remux.get())}

    def choose_out(self):
        ask_folder(self.win, "Πού να γραφτούν τα νέα αρχεία;", self.out_dir)

    def start(self):
        good = [i for i in self.items if not i["error"]]
        if not good:
            messagebox.showinfo(APP_NAME, "Πρόσθεσε πρώτα αρχεία βίντεο.", parent=self.win)
            return
        opts = self.options()
        out = self.out_dir.get().strip()
        if not out and any(i["kind"] == "dcp" for i in good):
            messagebox.showinfo(APP_NAME, "Για DCP διάλεξε φάκελο εξόδου (στον φάκελο του DCP δεν γράφεται τίποτα).",
                                parent=self.win)
            self.choose_out()
            out = self.out_dir.get().strip()
            if not out:
                return
        set_setting("transcode_opts", opts)
        set_setting("transcode_out", out)
        preset = opts.pop("preset")
        self.on_start(good, preset, opts, os.path.normpath(out) if out else None)
        self.win.destroy()
