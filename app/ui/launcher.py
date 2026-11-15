"""Παράθυρο επιλογής: λίστα ταινιών, λεπτομέρειες, έλεγχοι και εργαλεία."""
import os
import re
import shutil
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from app.checks import kdm as kdm_mod
from app.checks.dcnc import cpl_standard, dcnc_summary
from app.checks.issues import item_issues
from app.checks.scan import load_scan, scan_cpl
from app.checks.spec import get_spec
from app.core import prefs
from app.core.config import APP_DIR, APP_NAME, APP_VERSION, LOG_FILE, PLAYER_ENDED, VIDEO_EXT, get_setting, \
    log, resource, set_setting
from app.core.dcp import find_dcps, load_dcp
from app.core.hashing import verify_cpl
from app.core.media import probe, thumbnail
from app.core.notes import load_notes
from app.core.report import item_row, status_of, write_csv, write_report
from app.core.subtitles import track_summary
from app.core.util import fmt_size, fmt_time
from app.tools.catalog import catalog_table, read_catalog
from app.tools.convert import convert
from app.tools.copier import copy_verify, dcp_folders_for, write_copy_report
from app.tools.ingest import report_path
from app.tools.dcpomatic import DOWNLOAD_PAGE, find_dcpomatic, find_downloaded_installer, install
from app.tools.playlist import playlist_order
from app.tools.transcode import gpu_name, output_path, transcode_dcp, transcode_file
from app.tools.update import can_self_update, check_update, download_update, install_update
from app.ui.convert_window import ConvertWindow
from app.ui.ingest_window import IngestWindow
from app.ui.settings_window import SettingsWindow
from app.ui.linux_disk_window import LinuxDiskWindow
from app.ui.schedule_window import ScheduleWindow
from app.ui.subtitle_window import SubtitleWindow
from app.ui.transcode_window import TranscodeWindow, dcp_item
from app.ui.dialogs import CatalogWindow
from app.ui.i18n_tk import install as install_translation
from app.ui.jobs import JobRunner
from app.ui.spawn import player_command, relaunch, relaunch_as_admin, start_player
from app.ui.theme import Theme, window_center
from app.ui.winutil import point_on_screen

COLUMNS = ("Κατάσταση", "Τίτλος", "Διάρκεια", "Εικόνα", "Ήχος", "Υπότιτλοι", "Αρχεία", "Hash", "Σημειώσεις")
WIDTHS = (80, 300, 85, 85, 85, 160, 80, 100, 80)
LAST_FOLDER_FILE = os.path.join(APP_DIR, "last_folder.txt")
ISSUE_ICON = {"err": "✖", "warn": "⚠", "info": "ℹ"}
DEFAULT_GEOMETRY = "1150x680"
TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?::\d{2})?")
GEOMETRY_RE = re.compile(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)")


def unique_targets(dest, paths):
    """
    Φάκελοι προορισμού για DCP από δίσκο Linux. Αν δύο έχουν το ίδιο όνομα (σε άλλους φακέλους του δίσκου),
    στο δεύτερο μπαίνει και ο γονικός φάκελος, ώστε να μην ανακατευτούν.
    """
    out, used = [], set()
    for p in paths:
        parts = p.strip("/").split("/")
        name = parts[-1]
        if name.lower() in used or os.path.exists(os.path.join(dest, name)):
            name = f"{parts[-1]} ({parts[-2] if len(parts) > 1 else 'δίσκος'})"
        n, base = 2, name
        while name.lower() in used:
            name, n = f"{base} {n}", n + 1
        used.add(name.lower())
        out.append(os.path.join(dest, name))
    return out


def new_item(cpl):
    return {"cpl": cpl, "info": None, "hash": None, "notes": load_notes(cpl), "thumb": None,
            "scan": load_scan(cpl), "issues": []}


def remember_folder(folder):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LAST_FOLDER_FILE, "w", encoding="utf-8") as f:
            f.write(folder)
    except OSError:
        pass


def last_folder():
    try:
        with open(LAST_FOLDER_FILE, encoding="utf-8") as f:
            p = f.read().strip()
        return p if os.path.isdir(p) else None
    except OSError:
        return None


def saved_geometry():
    """Μέγεθος και θέση από την προηγούμενη φορά, αν η θέση είναι ακόμα σε συνδεδεμένη οθόνη."""
    g = get_setting("window_geometry")
    m = GEOMETRY_RE.fullmatch(g or "")
    if not m:
        return None
    def off(s):  # "+-1500" σημαίνει x = -1500 (οθόνη αριστερά της κύριας)
        return int(s[1:]) if s[0] == "+" else int(s)

    w, h, x, y = int(m.group(1)), int(m.group(2)), off(m.group(3)), off(m.group(4))
    return g if point_on_screen(x + w // 2, y + 20) else f"{w}x{h}"


class LauncherWindow:
    def __init__(self, initial=None, open_linux=False):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self._requested_geometry = saved_geometry() or DEFAULT_GEOMETRY
        self.root.geometry(self._requested_geometry)
        self.root.minsize(900, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._height_fix = 0
        self.root.after(800, self._restore_window_state)
        if resource("icon.ico"):
            try:
                self.root.iconbitmap(resource("icon.ico"))
            except Exception:
                pass
        self.items = []
        self.players = []  # διεργασίες αναπαραγωγής που τρέχουν
        self.spec = get_spec()
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.theme = Theme(self.root, self.style)
        if os.environ.get("DCP_THEME") in ("light", "dark"):  # για δοκιμές / screenshots
            self.theme.mode = os.environ["DCP_THEME"]
        self.half = tk.IntVar(value=1 if prefs.get("play_half") else 0)
        self.fullscreen = tk.IntVar(value=1 if prefs.get("play_fullscreen") else 0)
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Έτοιμο")
        self._build_top()
        self._build_table()
        self._build_progress()
        self._build_bottom()
        self._build_menu()
        self.jobs = JobRunner(self.root, self._on_progress, self._on_job_done, self._on_busy)
        self.jobs.on("row", self._on_row_event)
        self._bind()
        self.apply_theme()
        self._follow_system()
        self.root.after(100, self.jobs.poll)
        start = initial or (last_folder() if prefs.get("open_last_folder") else None)
        if start and os.path.isdir(start):
            self.root.after(150, lambda: self.load(start))
        if not os.environ.get("DCP_LAUNCHER_SHOT") and prefs.get("update_auto"):
            self.root.after(3000, self.update_check)
        if open_linux:  # ξεκίνησε ως διαχειριστής για δίσκο Linux
            self.root.after(800, self.open_linux_disk)

    # ================================================================== κατασκευή
    def _build_top(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Άνοιγμα φακέλου DCP…", command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="Παραλαβή DCP…", command=self.open_ingest).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Άνοιγμα βίντεο…", command=self.open_video).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Βίντεο → DCP…", command=self.open_convert).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Μετατροπή βίντεο…", command=self.open_transcode).pack(side="left", padx=(6, 0))
        self.entry = ttk.Entry(top, textvariable=self.path_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Φόρτωση", command=self.load_typed).pack(side="left")

    def _build_table(self):
        mid = ttk.PanedWindow(self.root, orient="vertical")
        mid.pack(fill="both", expand=True, padx=8)
        tf = ttk.Frame(mid)
        self.tree = ttk.Treeview(tf, columns=COLUMNS, show="headings", selectmode="extended", height=10)
        for c, w in zip(COLUMNS, WIDTHS):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w", stretch=c in ("Τίτλος", "Υπότιτλοι"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        mid.add(tf, weight=3)
        self.info = tk.Text(mid, height=12, font=("Consolas", 10), wrap="word", relief="flat", padx=8, pady=6)
        mid.add(self.info, weight=2)

    def _build_progress(self):
        f = ttk.Frame(self.root, padding=(8, 4))
        f.pack(fill="x")
        self.prog = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.prog.pack(side="left", fill="x", expand=True)
        ttk.Label(f, textvariable=self.status_var, width=60).pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(f, text="Ακύρωση", command=lambda: self.jobs.cancel())
        self.cancel_btn.pack(side="left")
        self.cancel_btn.state(["disabled"])

    def _build_bottom(self):
        bot = ttk.Frame(self.root, padding=8)
        bot.pack(fill="x")
        for text, cmd in (("Έλεγχος hash επιλεγμένων", lambda: self.run_hash(self.selected())),
                          ("Έλεγχος hash όλων", lambda: self.run_hash(self.all_indices())),
                          ("Σάρωση ταινίας", lambda: self.run_scan(self.selected())),
                          ("Αναφορά HTML", self.run_report),
                          ("Εξαγωγή CSV", self.export_csv),
                          ("Log", self.open_log)):
            ttk.Button(bot, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.play_btn = ttk.Button(bot, text="▶  Αναπαραγωγή", command=self.play_selected)
        self.play_btn.pack(side="right")
        ttk.Checkbutton(bot, text="Πλήρης οθόνη", variable=self.fullscreen).pack(side="right", padx=10)
        ttk.Checkbutton(bot, text="Μισή ανάλυση", variable=self.half).pack(side="right")

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Παραλαβή DCP από δίσκο (αντιγραφή, έλεγχοι, αναφορά)…", command=self.open_ingest)
        tools.add_command(label="Δίσκος Linux (ext2/3/4) που δεν ανοίγει στα Windows…", command=self.open_linux_disk)
        tools.add_command(label="Πρόγραμμα προβολών…", command=self.open_schedule)
        tools.add_separator()
        tools.add_command(label="Μετατροπή βίντεο (MP4 κ.λπ.) σε DCP…", command=self.open_convert)
        tools.add_command(label="Μετατροπή βίντεο (π.χ. MOV → MP4)…", command=self.open_transcode)
        tools.add_command(label="Εξαγωγή επιλεγμένων DCP σε MP4 (screener)…", command=self.export_dcp_mp4)
        tools.add_command(label="Έλεγχος υποτίτλων .srt (και διόρθωση χρόνων)…",
                          command=lambda: SubtitleWindow(self.root, self.theme))
        tools.add_separator()
        tools.add_command(label="Σάρωση επιλεγμένων (μαύρο, σιωπή, ήχος, υπότιτλοι)",
                          command=lambda: self.run_scan(self.selected()))
        tools.add_command(label="Σάρωση όλων", command=lambda: self.run_scan(self.all_indices()))
        tools.add_separator()
        tools.add_command(label="Λίστα προβολής με τα επιλεγμένα", command=self.play_list)
        tools.add_command(label="Αντιγραφή επιλεγμένων σε δίσκο με επαλήθευση…", command=self.copy_selected)
        tools.add_command(label="Κατάλογος φεστιβάλ (CSV)…", command=self.open_catalog)
        tools.add_command(label="Φόρτωση KDM…", command=self.load_kdm)
        tools.add_separator()
        tools.add_command(label="Έλεγχος για νέα έκδοση / αναβάθμιση", command=lambda: self.update_check(manual=True))
        menubar.add_cascade(label="Εργαλεία", menu=tools)
        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Ρυθμίσεις…", command=self.open_settings)
        settings.add_command(label="Προδιαγραφές φεστιβάλ…", command=lambda: self.open_settings("festival"))
        settings.add_command(label="Φάκελοι…", command=lambda: self.open_settings("folders"))
        settings.add_command(label="Αναπαραγωγή (συσκευή ήχου κ.λπ.)…", command=lambda: self.open_settings("playback"))
        menubar.add_cascade(label="Ρυθμίσεις", menu=settings)
        self.root.config(menu=menubar)

    def _bind(self):
        self.entry.bind("<Return>", lambda e: self.load_typed())
        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.tree.bind("<Double-Button-1>", lambda e: self.play_selected())
        self.tree.bind("<Return>", lambda e: self.play_selected())
        self.info.tag_bind("jump", "<Button-1>", self._on_time_click)
        self.info.tag_bind("jump", "<Enter>", lambda e: self.info.configure(cursor="hand2"))
        self.info.tag_bind("jump", "<Leave>", lambda e: self.info.configure(cursor="xterm"))

    # ================================================================== θέμα
    def apply_theme(self):
        self.theme.apply_ttk()
        self.theme.style_text(self.info)
        self.info.tag_configure("jump", foreground=self.theme.palette["accent"], underline=True)
        self.theme.tag_tree(self.tree)

    def _follow_system(self):  # στο «αυτόματο» ακολουθεί αλλαγές των Windows
        if self.theme.system_changed():
            self.apply_theme()
        self.root.after(3000, self._follow_system)

    # ================================================================== βοηθητικά
    def selected(self):
        return [int(i) for i in self.tree.selection()]

    def all_indices(self):
        return list(range(len(self.items)))

    def selected_cpls(self, playable=False):
        return [self.items[i]["cpl"] for i in self.selected() if not (playable and self.items[i]["cpl"].encrypted)]

    def info_box(self, text):
        messagebox.showinfo(APP_NAME, text)

    def start_job(self, name, fn):
        if not self.jobs.start(name, fn):
            self.info_box("Τρέχει ήδη εργασία. Περίμενε ή πάτα Ακύρωση.")

    def _on_progress(self, frac, msg):
        self.prog["value"] = int(frac * 1000)
        self.status_var.set(msg)

    def _on_job_done(self, name, err):
        self.prog["value"] = 0 if err else 1000
        self.status_var.set(f"{name}: {err or 'ολοκληρώθηκε'}")

    def _on_busy(self, busy):
        self.cancel_btn.state(["!disabled"] if busy else ["disabled"])

    def _on_row_event(self, i):
        self.refresh_row(i)
        if i in self.selected():
            self.show_details()

    def refresh_row(self, i):
        it = self.items[i]
        it["issues"] = item_issues(it, self.spec)
        r = item_row(it)
        vals = [r["Κατάσταση"], ("🔒 " if it["cpl"].encrypted else "") + r["Τίτλος"], r["Διάρκεια"],
                r["Εικόνα"], r["Ήχος"], r["Υπότιτλοι"], r["Αρχεία"], r["Hash"], r["Σημειώσεις"] or ""]
        if self.tree.exists(str(i)):
            self.tree.item(str(i), values=vals, tags=(r["Κατάσταση"],))
        else:
            self.tree.insert("", "end", iid=str(i), values=vals, tags=(r["Κατάσταση"],))

    def refresh_all(self):
        for i in self.all_indices():
            self.refresh_row(i)
        self.show_details()

    # ================================================================== φόρτωση
    def load(self, folder):
        if self.jobs.busy:
            self.info_box("Τρέχει εργασία. Περίμενε ή πάτα Ακύρωση.")
            return
        folder = os.path.normpath(folder)
        log.info("Φόρτωση φακέλου: %s", folder)
        self.path_var.set(folder)
        remember_folder(folder)
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.info.delete("1.0", "end")
        self._find_kdms(folder)
        dcps = find_dcps(folder) or [folder]
        self._read_dcps(dcps)
        if not self.items:
            log.warning("  Δεν βρέθηκε CPL")
            self.info.insert("end", "Δεν βρέθηκε CPL σε αυτόν τον φάκελο.")
            self.status_var.set("Δεν βρέθηκε DCP")
            return
        self.tree.selection_set("0")
        self.status_var.set(f"{len(self.items)} CPL σε {len(dcps)} DCP")
        self.start_job("Ανάγνωση", self._probe_all)

    def _find_kdms(self, folder):
        try:
            n = kdm_mod.scan_kdms(folder)
            if n:
                log.info("  KDM στον φάκελο: %d", n)
        except Exception:
            log.exception("Αναζήτηση KDM")

    def _read_dcps(self, dcps):
        for d in dcps:
            try:
                self.items += [new_item(c) for c in load_dcp(d)]
            except Exception as e:
                log.exception("Σφάλμα ανάγνωσης DCP %s", d)
                messagebox.showerror("Σφάλμα", f"{d}\n{e}\n\nΛεπτομέρειες στο Log.")
        for i, it in enumerate(self.items):
            c = it["cpl"]
            log.info("  CPL: %s | %d reels | %s | κρυπτογρ.=%s | λείπουν=%s", c.title, len(c.reels),
                     fmt_time(c.seconds), c.encrypted, c.missing or "τίποτα")
            self.refresh_row(i)

    def _probe_all(self, progress, cancel):  # ανάλυση εικόνας/ήχου στο παρασκήνιο
        from app.core.hashing import Cancelled
        items = list(self.items)
        for i, it in enumerate(items):
            if cancel.is_set():
                raise Cancelled()
            progress(i / len(items), f"Ανάγνωση {i + 1}/{len(items)}: {it['cpl'].title[:40]}")
            try:
                it["info"] = probe(it["cpl"])
            except Exception as e:
                it["info"] = {"pic_err": str(e)}
            self.jobs.post("row", i)

    def load_typed(self):
        p = self.path_var.get().strip().strip('"')
        if os.path.isdir(p):
            self.load(p)
        elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXT):
            self.spawn_player(p)
        else:
            messagebox.showerror(APP_NAME, f"Δεν βρέθηκε ο φάκελος:\n{p}")

    def choose_folder(self):
        d = filedialog.askdirectory(title="Φάκελος DCP (ή φάκελος με πολλά DCP)", initialdir=last_folder() or "")
        if d:
            self.load(d)

    def open_video(self):
        f = filedialog.askopenfilename(title="Αρχείο βίντεο", filetypes=[
            ("Βίντεο", " ".join("*" + e for e in VIDEO_EXT)), ("Όλα", "*.*")])
        if f:
            self.spawn_player(f)

    # ================================================================== λεπτομέρειες
    def show_details(self, _e=None):
        sel = self.selected()
        if not sel:
            return
        it = self.items[sel[0]]
        lines = (self._detail_header(it) + self._detail_checks(it) + self._detail_files(it)
                 + self._detail_notes(it) + [f"\nCPL: {it['cpl'].path}"])
        self.info.delete("1.0", "end")
        self.info.insert("end", "\n".join(lines))
        if not it["cpl"].encrypted:
            self._link_times(lines)
        self.play_btn.state(["disabled"] if it["cpl"].encrypted else ["!disabled"])

    def _link_times(self, lines):
        """Οι χρόνοι στα ευρήματα και στις σημειώσεις γίνονται links: κλικ = αναπαραγωγή από εκεί."""
        for n, line in enumerate("\n".join(lines).split("\n"), 1):
            if not line.startswith("  "):  # μόνο γραμμές ευρημάτων / σημειώσεων, όχι η διάρκεια
                continue
            for m in TIME_RE.finditer(line):
                self.info.tag_add("jump", f"{n}.{m.start()}", f"{n}.{m.end()}")

    def _on_time_click(self, event):
        idx = self.info.index(f"@{event.x},{event.y}")
        rng = self.info.tag_prevrange("jump", f"{idx}+1c")
        if not rng:
            return
        m = TIME_RE.fullmatch(self.info.get(*rng))
        sel = self.selected()
        if not m or not sel:
            return
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start = max(0, h * 3600 + mi * 60 + s - prefs.get("jump_preroll"))
        log.info("Αναπαραγωγή από εύρημα: %s", self.info.get(*rng))
        self.spawn_player(self.items[sel[0]]["cpl"].path, sel[0], extra_args=["--start", str(start)])

    def _detail_header(self, it):
        c, inf = it["cpl"], it["info"] or {}
        r = item_row(it)
        st, why = status_of(c, inf, it["hash"], it["notes"], it.get("issues"))
        subs, sub_notes = track_summary(c)
        dn, std = dcnc_summary(c), cpl_standard(c)
        lines = [f"Κατάσταση:   {st}" + (f"  ({'; '.join(why)})" if why else ""), f"Τίτλος:      {c.title}"]
        if dn:
            lines.append(f"Όνομα DCNC:  {dn}")
        lines += [
            f"Είδος:       {c.kind}      Issuer: {c.issuer}" + (f"      Πρότυπο: {std}" if std else ""),
            f"Διάρκεια:    {r['Διάρκεια']}   ({len(c.reels)} reels, {r['fps']} fps)",
            f"Εικόνα:      {r['Εικόνα']} {inf.get('pix', '')}" + ("   3D (E = αλλαγή ματιού)" if r["3D"] == "ναι" else ""),
            f"Ήχος:        {r['Ήχος']}",
            f"Υπότιτλοι:   {subs}" + (f"  - {'; '.join(sub_notes)}" if sub_notes else ""),
            f"Κρυπτογρ.:   {'ΝΑΙ - χρειάζεται KDM, δεν παίζει' if c.encrypted else 'όχι'}",
            f"Hash:        {r['Hash']}",
        ]
        lines += [f"Πρόβλημα:    {inf[k]}" for k in ("pic_err", "snd_err") if inf.get(k)]
        return lines

    def _detail_checks(self, it):
        c, lines = it["cpl"], []
        iss = it.get("issues") or []
        if iss:
            lines.append("\nΕΛΕΓΧΟΙ:  (κλικ σε χρόνο = αναπαραγωγή από εκεί)")
            lines += [f"  {ISSUE_ICON.get(lv, '·')} {t}" for lv, t in iss]
        if it.get("scan"):
            lines.append(f"  (σάρωση: {it['scan'].get('date', '?')})")
        elif not c.is_file and not c.encrypted:
            lines.append("\nΣάρωση: δεν έχει γίνει (κουμπί «Σάρωση ταινίας»)")
        kl = kdm_mod.kdm_lines(c)
        if kl:
            lines.append("\nKDM:")
            lines += [f"  {x}" for x in kl]
        return lines

    def _detail_files(self, it):
        c, lines = it["cpl"], []
        if c.external:
            lines.append(f"\nVF: {len(c.external)} αρχεία από άλλο DCP (OV):")
            lines += [f"  - {os.path.basename(d)}" for d in sorted(set(c.external.values()))]
        if c.missing:
            lines.append("\nΛΕΙΠΟΥΝ ΑΡΧΕΙΑ:")
            lines += [f"  - {m}" for m in c.missing]
        bad = [h for h in it["hash"] or [] if h["status"] != "OK"]
        if bad:
            lines.append("\nΑΠΟΤΕΛΕΣΜΑ HASH:")
            lines += [f"  {h['status']:<14} {h['name']}  ({h['detail']})" for h in bad]
        return lines

    def _detail_notes(self, it):
        if not it["notes"]:
            return []
        c = it["cpl"]
        return ["\nΣΗΜΕΙΩΣΕΙΣ:"] + [f"  {fmt_time(n['t'], c.rate)}  {n['text']}" for n in it["notes"]]

    # ================================================================== hash / σάρωση / αναφορές
    def _per_item_job(self, idx, label, work):
        """Εργασία για κάθε επιλεγμένη ταινία: work(i, progress(frac, msg), cancel)."""
        def job(progress, cancel):
            for n, i in enumerate(idx):
                def p(frac, msg, n=n):
                    progress((n + frac) / len(idx), f"{label} {n + 1}/{len(idx)}: {msg}")
                work(i, p, cancel)
                self.jobs.post("row", i)
        return job

    def run_hash(self, idx):
        if not idx:
            self.info_box("Διάλεξε πρώτα ταινίες από τη λίστα.")
            return

        def work(i, p, cancel):
            def bytes_progress(done, total, name):
                p(done / total if total else 1, f"{name[:40]} {done * 100 // max(1, total)}%")
            self.items[i]["hash"] = verify_cpl(self.items[i]["cpl"], bytes_progress, cancel)

        self.start_job("Έλεγχος hash", self._per_item_job(idx, "Hash", work))

    def run_scan(self, idx):
        idx = [i for i in idx if not self.items[i]["cpl"].encrypted]
        if not idx:
            self.info_box("Διάλεξε πρώτα ταινίες (μη κρυπτογραφημένες) από τη λίστα.")
            return

        def work(i, p, cancel):
            self.items[i]["scan"] = scan_cpl(self.items[i]["cpl"], p, cancel)

        self.start_job("Σάρωση", self._per_item_job(idx, "Σάρωση", work))

    def run_report(self):
        if not self.items:
            return
        idx = self.selected() if len(self.selected()) > 1 else self.all_indices()
        path = filedialog.asksaveasfilename(title="Αποθήκευση αναφοράς", defaultextension=".html",
                                            initialfile="Αναφορά_DCP.html", filetypes=[("HTML", "*.html")])
        if not path:
            return

        def work(i, p, cancel):
            it = self.items[i]
            p(0, "καρέ")
            if it["info"] is None:
                it["info"] = probe(it["cpl"])
            if it["thumb"] is None and not it["cpl"].encrypted:
                try:
                    it["thumb"] = thumbnail(it["cpl"])
                except Exception:
                    log.exception("Δεν βγήκε καρέ για %s", it["cpl"].title)
            it["notes"] = load_notes(it["cpl"])

        per_item = self._per_item_job(idx, "Αναφορά", work)

        def job(progress, cancel):
            per_item(progress, cancel)
            write_report([self.items[i] for i in idx], path)
            self.jobs.call_in_gui(lambda: os.startfile(path))

        self.start_job("Αναφορά", job)

    def export_csv(self):
        if not self.items:
            return
        path = filedialog.asksaveasfilename(title="Εξαγωγή CSV", defaultextension=".csv",
                                            initialfile="Έλεγχος_DCP.csv", filetypes=[("CSV (Excel)", "*.csv")])
        if path:
            write_csv(self.items, path)
            self.status_var.set(f"CSV: {path}")
            os.startfile(path)

    def open_log(self):
        if os.path.isfile(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            self.info_box("Δεν υπάρχει ακόμα log.")

    # ================================================================== αναπαραγωγή
    def spawn_player(self, target, item_index=None, extra_args=(), on_exit=None):
        args = player_command(target, self.half.get(), self.fullscreen.get(), extra_args,
                              center=window_center(self.root))
        try:
            self.players.append(start_player(self.root, args, lambda rc: self._player_exited(rc, item_index, on_exit)))
        except Exception as e:
            log.exception("Δεν ξεκίνησε η αναπαραγωγή")
            messagebox.showerror(APP_NAME, f"Δεν ξεκίνησε η αναπαραγωγή:\n{e}")

    def _player_exited(self, rc, item_index, on_exit):
        if item_index is not None and item_index < len(self.items):  # ίσως προστέθηκαν σημειώσεις
            self.items[item_index]["notes"] = load_notes(self.items[item_index]["cpl"])
            self.refresh_row(item_index)
            self.show_details()
        if on_exit:
            on_exit(rc)
        if rc and rc != PLAYER_ENDED:
            log.error("Η αναπαραγωγή έκλεισε με κωδικό %s", rc)
            msg = {2: "Δεν βρέθηκε το CPL / αρχείο.", 3: "Κρυπτογραφημένο DCP: χρειάζεται KDM."}.get(
                rc, "Η αναπαραγωγή σταμάτησε με σφάλμα.")
            if messagebox.askyesno(APP_NAME, f"{msg}\n\nΆνοιγμα του log;"):
                self.open_log()

    def play_selected(self):
        sel = self.selected()
        if sel:
            self.spawn_player(self.items[sel[0]]["cpl"].path, sel[0])

    def play_list(self):
        sel = self.selected_cpls(playable=True)
        if len(sel) < 2:
            self.info_box("Διάλεξε 2 ή περισσότερα (Ctrl+κλικ): π.χ. τρέιλερ και ταινία.\n"
                          "Παίζουν πρώτα τα τρέιλερ/σποτ και τελευταία η ταινία.")
            return
        order = playlist_order(sel)
        log.info("Λίστα προβολής: %s", [c.title for c in order])
        self._play_list_step(order, 0)

    def _play_list_step(self, order, k):
        if k >= len(order):
            self.status_var.set("Λίστα προβολής: τέλος")
            return
        c = order[k]
        self.status_var.set(f"Λίστα προβολής {k + 1}/{len(order)}: {c.title[:50]}")

        def next_or_stop(rc):
            if rc == PLAYER_ENDED:
                self._play_list_step(order, k + 1)
            else:
                self.status_var.set("Λίστα προβολής: σταμάτησε")

        self.spawn_player(c.path, extra_args=["--autoexit"], on_exit=next_or_stop)

    # ================================================================== εργαλεία
    def when_idle(self, fn, interval=500):
        """Εκτελεί το fn μόλις δεν τρέχει άλλη εργασία."""
        if self.jobs.busy:
            self.root.after(interval, lambda: self.when_idle(fn, interval))
        else:
            fn()

    # ------------------------------------------------------------------ βίντεο → DCP
    def open_convert(self):
        self.convert_win = ConvertWindow(self.root, self.theme, find_dcpomatic(), self.run_convert,
                                         self.install_dcpomatic)

    def _convert_parent(self):
        win = getattr(self, "convert_win", None)
        return win.win if win and win.win.winfo_exists() else self.root

    def install_dcpomatic(self):
        """
        Ανοίγει τη σελίδα λήψης του DCP-o-matic και περιμένει στο παρασκήνιο: μόλις ο installer κατέβει στις
        Λήψεις, προτείνει να τον εγκαταστήσει μόνο του (ή εντοπίζει το πρόγραμμα αν το εγκαταστήσεις εσύ).
        """
        if getattr(self, "_waiting_install", False):
            webbrowser.open(DOWNLOAD_PAGE)
            return
        if not messagebox.askyesno(
                APP_NAME, "Θα ανοίξει η σελίδα του DCP-o-matic (δωρεάν πρόγραμμα, dcpomatic.com).\n\n"
                          "1. Πάτα «Windows 64-bit» (μπορείς να συνεχίσεις χωρίς δωρεά).\n"
                          "2. Άφησέ το να κατέβει στις Λήψεις.\n"
                          "3. Το ReelCheck θα το βρει και θα κάνει μόνο του την εγκατάσταση "
                          "(τα Windows θα ζητήσουν άδεια).\n\nΆνοιγμα της σελίδας;", parent=self._convert_parent()):
            return
        webbrowser.open(DOWNLOAD_PAGE)
        self._waiting_install = True
        self.status_var.set("Περιμένω το DCP-o-matic: κατέβασε «Windows 64-bit» από τη σελίδα που άνοιξε…")
        threading.Thread(target=self._wait_for_installer, args=(time.time(),), daemon=True,
                         name="dcpomatic-wait").start()

    def _wait_for_installer(self, since, timeout=3600):
        try:
            while time.time() - since < timeout:
                info = find_dcpomatic()
                if info:  # το εγκατέστησε ο χρήστης μόνος του
                    self.jobs.call_in_gui(lambda: self._dcpomatic_ready(info))
                    return
                inst = find_downloaded_installer(since)
                if inst:
                    self.jobs.call_in_gui(lambda: self._offer_install(inst))
                    return
                time.sleep(3)
            self.jobs.call_in_gui(lambda: self.status_var.set("Δεν βρέθηκε το DCP-o-matic (σταμάτησε η αναμονή)"))
        finally:
            self._waiting_install = False

    def _offer_install(self, installer):
        if not messagebox.askyesno(APP_NAME, f"Κατέβηκε ο installer:\n{os.path.basename(installer)}\n\n"
                                             "Να γίνει η εγκατάσταση τώρα;", parent=self._convert_parent()):
            return
        self.when_idle(lambda: self.start_job("Εγκατάσταση DCP-o-matic", lambda p, c: self._install_job(installer, p)))

    def _install_job(self, installer, progress):
        progress(0.5, "Εγκατάσταση DCP-o-matic: αποδέξου το μήνυμα των Windows…")
        info = install(installer)
        self.jobs.call_in_gui(lambda: self._dcpomatic_ready(info))

    def _dcpomatic_ready(self, info):
        win = getattr(self, "convert_win", None)
        if win and win.win.winfo_exists():
            win.set_tools(info)
        self.status_var.set(f"DCP-o-matic {info['version']}: έτοιμο")
        self.info_box(f"Το DCP-o-matic {info['version']} είναι έτοιμο. Μπορείς να ξεκινήσεις τη μετατροπή.")

    def run_convert(self, infos, opts, out_dir):
        n = 1 if opts["mode"] == "single" else len(infos)
        log.info("Μετατροπή σε DCP: %d αρχεία -> %d DCP στο %s (%s)", len(infos), n, out_dir, opts)
        self.start_job("Μετατροπή σε DCP", lambda progress, cancel: self._convert_job(infos, opts, out_dir,
                                                                                      progress, cancel))

    def _convert_job(self, infos, opts, out_dir, progress, cancel):
        results = convert(infos, opts, out_dir, progress, cancel)
        # μετά το κλείσιμο αυτής της εργασίας, ώστε να μπορεί να ξεκινήσει η φόρτωση
        self.jobs.call_in_gui(lambda: self.when_idle(lambda: self._after_convert(results, out_dir)))

    def _after_convert(self, results, out_dir):
        """Φόρτωση του φακέλου εξόδου και αυτόματος έλεγχος (hash + σάρωση) των νέων DCP."""
        self.load(out_dir)
        wanted = {os.path.normcase(os.path.abspath(r)) for r in results}
        self.when_idle(lambda: self._check_new_dcps(wanted))

    def _check_new_dcps(self, folders):
        idx = [i for i, it in enumerate(self.items) if os.path.normcase(os.path.abspath(it["cpl"].folder)) in folders]
        if not idx:
            return
        for i in idx:
            self.tree.selection_add(str(i))

        def work(i, p, cancel):
            c = self.items[i]["cpl"]
            self.items[i]["hash"] = verify_cpl(c, lambda d, t, n: p(0.3 * d / max(1, t), f"hash {n[:30]}"), cancel)
            self.items[i]["scan"] = scan_cpl(c, lambda f, m: p(0.3 + 0.7 * f, m), cancel)

        self.start_job("Έλεγχος νέων DCP", self._per_item_job(idx, "Έλεγχος", work))
        self.when_idle(lambda: self._report_new(idx))

    def _report_new(self, idx):
        lines = [f"{item_row(self.items[i])['Κατάσταση']:<8} {self.items[i]['cpl'].title}" for i in idx]
        self.info_box("Η μετατροπή τελείωσε και τα DCP ελέγχθηκαν:\n\n" + "\n".join(lines) +
                      "\n\nΛεπτομέρειες στη λίστα (επίλεξε το καθένα).")

    # ------------------------------------------------------------------ μετατροπή βίντεο
    def open_transcode(self, items=()):
        if not hasattr(self, "_gpu_label"):
            self._gpu_label = gpu_name()  # δοκιμή encoders μία φορά
        TranscodeWindow(self.root, self.theme, self.run_transcode, items, self._gpu_label)

    def export_dcp_mp4(self):
        idx = [i for i in self.selected() if not self.items[i]["cpl"].encrypted and not self.items[i]["cpl"].is_file]
        if not idx:
            self.info_box("Διάλεξε πρώτα DCP (μη κρυπτογραφημένα) από τη λίστα.")
            return
        self.open_transcode([dcp_item(self.items[i]["cpl"], self.items[i]["info"],
                                      track_summary(self.items[i]["cpl"])[0]) for i in idx])

    def run_transcode(self, items, preset, opts, out_dir):
        log.info("Μετατροπή βίντεο: %d αρχεία σε %s %s -> %s", len(items), preset, opts, out_dir or "δίπλα")
        self.start_job("Μετατροπή βίντεο", lambda progress, cancel: self._transcode_job(items, preset, opts, out_dir,
                                                                                        progress, cancel))

    def _transcode_job(self, items, preset, opts, out_dir, progress, cancel):
        total = sum(max(1.0, i["duration"]) for i in items)
        done, results = 0.0, []
        for n, it in enumerate(items, 1):
            span = max(1.0, it["duration"])
            label = f"{n}/{len(items)} {os.path.basename(it['path'])[:35]}: "

            def p(frac, msg, done=done, span=span, label=label):
                progress((done + span * frac) / total, label + msg)

            folder = out_dir or os.path.dirname(it["path"])
            os.makedirs(folder, exist_ok=True)
            dst = output_path(it["path"], folder, preset)
            try:
                if it["kind"] == "dcp":
                    r = transcode_dcp(it["cpl"], dst, preset, dict(opts, subs="burn"), p, cancel)
                else:
                    r = transcode_file(it["path"], dst, preset, dict(opts, srt=it.get("srt")), p, cancel)
                results.append((it, dst, r, None))
            except Exception as e:
                if "Cancelled" in type(e).__name__:
                    if os.path.exists(dst):
                        os.remove(dst)
                    raise
                log.exception("Μετατροπή %s", it["path"])
                if os.path.exists(dst):
                    os.remove(dst)
                results.append((it, dst, None, str(e)))
            done += span
        self.jobs.call_in_gui(lambda: self._transcode_report(results, out_dir))

    def _transcode_report(self, results, out_dir):
        ok = [(it, dst, r) for it, dst, r, err in results if not err]
        bad = [(it, err) for it, _d, _r, err in results if err]
        lines = []
        for it, dst, r in ok:
            how = "χωρίς επανακωδικοποίηση" if r["mode"] == "remux" else r["encoder"]
            lines.append(f"OK  {os.path.basename(dst)}  ({how})")
        lines += [f"ΣΦΑΛΜΑ  {os.path.basename(it['path'])}: {err[:120]}" for it, err in bad]
        folder = out_dir or (os.path.dirname(ok[0][1]) if ok else None)
        msg = "\n".join(lines) + ("\n\nΆνοιγμα του φακέλου;" if folder else "")
        if folder and messagebox.askyesno(APP_NAME, msg, parent=self.root):
            os.startfile(folder)
        elif not folder:
            self.info_box(msg)

    # ------------------------------------------------------------------ ρυθμίσεις
    def open_settings(self, tab="general"):
        SettingsWindow(self.root, self.theme, self._settings_saved, tab)

    def _settings_saved(self, changed):
        """Εφαρμογή των αλλαγών αμέσως, όπου γίνεται· η γλώσσα θέλει επανεκκίνηση."""
        log.info("Ρυθμίσεις: άλλαξαν %s", sorted(changed))
        everything = "imported" in changed
        if everything or "theme" in changed:
            self.theme.mode = prefs.get("theme")
            self.apply_theme()
        if everything or changed & {"play_half", "play_fullscreen"}:
            self.half.set(1 if prefs.get("play_half") else 0)
            self.fullscreen.set(1 if prefs.get("play_fullscreen") else 0)
        if "scans_cleared" in changed:
            for it in self.items:
                it["scan"] = None
        rescore = {"festival_spec", "scans_cleared", "lufs_check", "lufs_target", "lufs_tolerance"}
        if everything or changed & rescore or any(k.startswith(("scan_", "sub_")) for k in changed):
            self.spec = get_spec()
            self.refresh_all()
        if everything or "language" in changed:
            if messagebox.askyesno(APP_NAME, "Η γλώσσα αλλάζει στην επόμενη εκκίνηση. Να ξεκινήσει ξανά το ReelCheck "
                                             "τώρα;\n\nThe language changes on the next start. Restart ReelCheck now?",
                                   parent=self.root):
                if relaunch(self.path_var.get() or None):
                    self.close()

    # ------------------------------------------------------------------ δίσκος Linux
    def open_linux_disk(self):
        LinuxDiskWindow(self.root, self.theme, self.copy_from_linux, relaunch_as_admin)

    def copy_from_linux(self, fs, handle, paths, dest, scan):
        sizes = {p: s for p, s, _n in fs.find_dcps()}
        need = sum(sizes.get(p, 0) for p in paths)
        os.makedirs(dest, exist_ok=True)
        free = shutil.disk_usage(dest).free
        if need > free:
            handle.close()
            messagebox.showerror(APP_NAME, f"Δεν χωράει: χρειάζονται {fmt_size(need)}, ελεύθερα {fmt_size(free)}.")
            return
        targets = unique_targets(dest, paths)
        log.info("Αντιγραφή από δίσκο Linux (%s): %s -> %s", fs.version, paths, dest)

        def job(progress, cancel):
            try:
                done = 0
                for p, target in zip(paths, targets):
                    span = sizes.get(p, 1) or 1

                    def prog(frac, msg, done=done, span=span):
                        progress((done + span * frac) / max(1, need), msg)
                    fs.copy_tree(p, target, prog, cancel)
                    done += span
            finally:
                handle.close()
            self.jobs.call_in_gui(lambda: self.when_idle(
                lambda: self._ingest_check(targets, dest, {"scan": scan}, None)))

        self.start_job("Αντιγραφή από δίσκο Linux", job)

    # ------------------------------------------------------------------ πρόγραμμα προβολών
    def open_schedule(self):
        def films():
            return [(it["cpl"].title, it["cpl"].id, it["cpl"].seconds / 60) for it in self.items
                    if not it["cpl"].is_file]

        def status_of(cpl_id):
            it = next((x for x in self.items if x["cpl"].id == cpl_id), None)
            return item_row(it)["Κατάσταση"] if it else ""

        ScheduleWindow(self.root, self.theme, films, status_of)

    # ------------------------------------------------------------------ παραλαβή DCP
    def open_ingest(self):
        IngestWindow(self.root, self.theme, self.run_ingest)

    def run_ingest(self, folders, dest, opts):
        """Αντιγραφή με επαλήθευση (αν ζητήθηκε) → φόρτωση → hash + σάρωση → κατάλογος → αναφορά."""
        log.info("Παραλαβή: %d DCP -> %s %s", len(folders), dest or "(έλεγχος στη θέση τους)", opts)
        if dest:
            self.start_job("Παραλαβή: αντιγραφή", lambda p, c: self._ingest_copy(folders, dest, opts, p, c))
        else:
            root = os.path.commonpath(folders) if len(folders) > 1 else os.path.dirname(folders[0])
            self._ingest_check(folders, root, opts, None)

    def _ingest_copy(self, folders, dest, opts, progress, cancel):
        res = copy_verify(folders, dest, progress, cancel)
        write_copy_report(res, dest)
        targets = [os.path.join(dest, os.path.basename(os.path.normpath(f))) for f in folders]
        self.jobs.call_in_gui(lambda: self.when_idle(lambda: self._ingest_check(targets, dest, opts, res)))

    def _ingest_check(self, targets, root, opts, copy_res):
        self.load(root)
        wanted = {os.path.normcase(os.path.abspath(t)) for t in targets}

        def checks():
            idx = [i for i, it in enumerate(self.items)
                   if os.path.normcase(os.path.abspath(it["cpl"].folder)) in wanted]

            def work(i, p, cancel):
                it = self.items[i]
                if it["cpl"].encrypted:
                    return
                it["hash"] = verify_cpl(it["cpl"], lambda d, t, n: p(0.2 * d / max(1, t), f"hash {n[:30]}"), cancel)
                if opts.get("scan"):
                    it["scan"] = scan_cpl(it["cpl"], lambda f, m: p(0.2 + 0.8 * f, m), cancel)

            self.tree.selection_set([str(i) for i in idx])
            self.start_job("Παραλαβή: έλεγχοι", self._per_item_job(idx, "Έλεγχος", work))
            self.when_idle(lambda: self._ingest_finish(idx, root, opts, copy_res))

        self.when_idle(checks)

    def _ingest_finish(self, idx, root, opts, copy_res):
        items = [self.items[i] for i in idx]
        rows = [item_row(it) for it in items]
        counts = {k: sum(r["Κατάσταση"] == k for r in rows) for k in ("OK", "ΠΡΟΣΟΧΗ", "ΣΦΑΛΜΑ")}
        rep = report_path(root)
        try:
            write_report(items, rep)
        except OSError:  # π.χ. δίσκος μόνο για ανάγνωση: η αναφορά πάει στα Έγγραφα
            rep = os.path.join(os.path.expanduser("~"), "Documents", os.path.basename(rep))
            write_report(items, rep)
        bad_copy = [r for r in copy_res or [] if r["status"] != "OK"]
        lines = [f"Παραλαβή: {len(items)} CPL · {counts['OK']} OK · {counts['ΠΡΟΣΟΧΗ']} προσοχή · "
                 f"{counts['ΣΦΑΛΜΑ']} σφάλμα"]
        if copy_res is not None:
            lines.append(f"Αντιγραφή: {len(copy_res)} αρχεία, " +
                         ("όλα σωστά" if not bad_copy else f"{len(bad_copy)} ΜΕ ΛΑΘΟΣ"))
        lines += [f"  {r['Κατάσταση']:<8} {r['Τίτλος']}" for r in rows]
        if opts.get("catalog"):
            try:
                table, summary = catalog_table(read_catalog(opts["catalog"]), self.items,
                                               lambda i: item_row(self.items[i])["Κατάσταση"])
                lines.append(f"Κατάλογος: {summary}")
                CatalogWindow(self.root, self.theme, os.path.basename(opts["catalog"]), summary, table)
            except Exception as e:
                log.exception("Κατάλογος στην παραλαβή")
                lines.append(f"Κατάλογος: δεν διαβάστηκε ({e})")
        lines.append(f"\nΑναφορά: {rep}\n\nΆνοιγμα της αναφοράς;")
        if messagebox.askyesno(APP_NAME, "\n".join(lines), parent=self.root):
            os.startfile(rep)

    def copy_selected(self):
        sel = self.selected_cpls()
        if not sel:
            self.info_box("Διάλεξε πρώτα ταινίες από τη λίστα.")
            return
        folders = dcp_folders_for(sel)
        dest = filedialog.askdirectory(title="Πού να αντιγραφούν τα DCP;")
        if not dest:
            return
        dest = os.path.normpath(dest)
        if any(os.path.normcase(dest).startswith(os.path.normcase(f)) for f in folders):
            messagebox.showerror(APP_NAME, "Ο προορισμός είναι μέσα στο DCP που αντιγράφεται.")
            return
        names = "\n".join(os.path.basename(f) for f in folders)
        if messagebox.askyesno(APP_NAME, f"Αντιγραφή {len(folders)} φακέλων στο\n{dest}\n\n{names}\n\n"
                                         "Μετά την αντιγραφή ελέγχεται κάθε αρχείο (SHA-1)."):
            self.start_job("Αντιγραφή", lambda progress, cancel: self._copy_job(folders, dest, progress, cancel))

    def _copy_job(self, folders, dest, progress, cancel):
        res = copy_verify(folders, dest, progress, cancel)
        bad = [r for r in res if r["status"] != "OK"]
        rep = write_copy_report(res, dest)
        msg = (f"Αντιγράφηκαν και ελέγχθηκαν {len(res)} αρχεία.\n" +
               ("Όλα σωστά." if not bad else f"ΠΡΟΣΟΧΗ: {len(bad)} αρχεία με λάθος:\n" +
                "\n".join(os.path.basename(r["file"]) for r in bad[:10])) +
               (f"\n\nΑναφορά: {rep}" if rep else ""))
        show = messagebox.showerror if bad else messagebox.showinfo
        self.jobs.call_in_gui(lambda: show(APP_NAME, msg))

    def open_catalog(self):
        if not self.items:
            self.info_box("Φόρτωσε πρώτα τον φάκελο με τα DCP.")
            return
        path = filedialog.askopenfilename(title="Κατάλογος ταινιών φεστιβάλ (CSV ή TXT)",
                                          initialfile=os.path.basename(get_setting("catalog", "") or ""),
                                          filetypes=[("CSV / TXT", "*.csv *.txt"), ("Όλα", "*.*")])
        if not path:
            return
        try:
            cat = read_catalog(path)
        except Exception as e:
            log.exception("Κατάλογος")
            messagebox.showerror(APP_NAME, f"Δεν διαβάστηκε ο κατάλογος:\n{e}")
            return
        if not cat:
            self.info_box("Δεν βρέθηκαν τίτλοι στο αρχείο.")
            return
        set_setting("catalog", path)
        table, summary = catalog_table(cat, self.items, lambda i: item_row(self.items[i])["Κατάσταση"])
        CatalogWindow(self.root, self.theme, os.path.basename(path), summary, table)

    def load_kdm(self):
        paths = filedialog.askopenfilenames(title="Αρχεία KDM (.xml)", filetypes=[("KDM", "*.xml"), ("Όλα", "*.*")])
        if not paths:
            return
        n, bad = kdm_mod.add_kdm_files(paths)
        matched = sum(1 for it in self.items if it["cpl"].id in kdm_mod.KDMS)
        self.refresh_all()
        self.info_box(f"Φορτώθηκαν {n} KDM" + (f", {bad} αρχεία δεν ήταν KDM" if bad else "") +
                      f".\nΤαιριάζουν με {matched} CPL της λίστας.\n\n"
                      "Το ReelCheck δεν αποκρυπτογραφεί: δείχνει μόνο για ποιον server και μέχρι πότε ισχύει.")

    def update_check(self, manual=False):
        if not manual and time.time() - float(get_setting("update_last", 0) or 0) < 86400:
            return
        threading.Thread(target=self._update_worker, args=(manual,), daemon=True, name="update").start()

    def _update_worker(self, manual):
        try:
            rel = check_update()
            set_setting("update_last", time.time())
        except Exception as e:
            log.info("Έλεγχος νέας έκδοσης απέτυχε: %s", e)
            if manual:
                self.jobs.call_in_gui(lambda: self.info_box("Δεν έγινε έλεγχος (χωρίς internet;)."))
            return
        self.jobs.call_in_gui(lambda: self._update_result(rel, manual))

    def _update_result(self, rel, manual):
        if not rel:
            if manual:
                self.info_box(f"Έχεις την τελευταία έκδοση (v{APP_VERSION}).")
            return
        v = rel["version"]
        self.status_var.set(f"Υπάρχει νέα έκδοση: v{v}")
        if not (can_self_update() and rel.get("exe_url")):
            hint = "\n\n(Τρέχει από τον κώδικα: αναβάθμιση με git pull.)" if not can_self_update() else ""
            if messagebox.askyesno(APP_NAME, f"Υπάρχει νέα έκδοση v{v} (έχεις v{APP_VERSION}).{hint}\n\n"
                                             "Άνοιγμα της σελίδας του release;"):
                webbrowser.open(rel["page"])
            return
        size = f" ({rel['size'] // 1048576} MB)" if rel.get("size") else ""
        ans = messagebox.askyesnocancel(
            APP_NAME, f"Υπάρχει νέα έκδοση v{v} (έχεις v{APP_VERSION}).\n\n"
                      f"Ναι: κατέβασμα{size} και αναβάθμιση τώρα\n"
                      "Όχι: άνοιγμα της σελίδας με τις αλλαγές\nΆκυρο: αργότερα")
        if ans:
            self.start_job("Αναβάθμιση", lambda progress, cancel: self._download_job(rel, progress, cancel))
        elif ans is False:
            webbrowser.open(rel["page"])

    def _download_job(self, rel, progress, cancel):
        path = download_update(rel, progress, cancel)
        self.jobs.call_in_gui(lambda: self._install_downloaded(rel["version"], path))

    def _running_players(self):
        self.players = [p for p in self.players if p.poll() is None]
        return self.players

    def _close_players(self):
        """Κλείνει τα παράθυρα αναπαραγωγής (κρατούν κλειδωμένο το .exe)."""
        for p in self._running_players():
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                log.exception("Κλείσιμο αναπαραγωγής για αναβάθμιση")

    def _install_downloaded(self, version, path):
        """Χωρίς άλλες ερωτήσεις: κλείνει ό,τι τρέχει, βάζει τη νέα έκδοση και την ανοίγει."""
        self.status_var.set(f"Αναβάθμιση σε v{version}: το ReelCheck κλείνει και ανοίγει ξανά…")
        self._close_players()
        try:
            install_update(path)
        except Exception as e:
            log.exception("Αναβάθμιση")
            messagebox.showerror(APP_NAME, f"Δεν ξεκίνησε η αναβάθμιση:\n{e}", parent=self.root)
            return
        self.root.after(1200, self.root.destroy)  # να προλάβει να φανεί το μήνυμα

    # ================================================================== εκτέλεση
    def _restore_window_state(self):
        """
        Μετά το άνοιγμα: (1) μέτρηση πόσο ύψος «τρώει» η μπάρα μενού, ώστε να μη μικραίνει το παράθυρο
        σε κάθε άνοιγμα/κλείσιμο, (2) μεγιστοποίηση αν ήταν μεγιστοποιημένο (στην οθόνη όπου ήταν).
        """
        try:
            m = GEOMETRY_RE.fullmatch(self._requested_geometry)
            if m and self.root.state() == "normal":
                self._height_fix = max(0, int(m.group(2)) - self.root.winfo_height())
            if get_setting("window_zoomed"):
                self.root.state("zoomed")
        except Exception:
            log.exception("Επαναφορά θέσης παραθύρου")

    def window_geometry_to_save(self):
        """(γεωμετρία σε κανονική κατάσταση, ήταν μεγιστοποιημένο;)."""
        zoomed = self.root.state() == "zoomed"
        if zoomed:  # η θέση «κανονικής» κατάστασης είναι στην ίδια οθόνη με τη μεγιστοποίηση
            self.root.state("normal")
            self.root.update_idletasks()
        g = self.root.geometry()
        m = GEOMETRY_RE.fullmatch(g)
        if m and self._height_fix:
            g = f"{m.group(1)}x{int(m.group(2)) + self._height_fix}{m.group(3)}{m.group(4)}"
        return g, zoomed

    def close(self):
        """Κλείσιμο από τον χρήστη: θυμάται μέγεθος, θέση, οθόνη και μεγιστοποίηση για την επόμενη φορά."""
        try:
            if self.root.state() != "iconic":
                g, zoomed = self.window_geometry_to_save()
                set_setting("window_geometry", g)
                set_setting("window_zoomed", zoomed)
        except Exception:
            log.exception("Αποθήκευση θέσης παραθύρου")
        self.root.destroy()

    def run(self):
        shot = os.environ.get("DCP_LAUNCHER_SHOT")  # για δοκιμές: screenshot του παραθύρου και έξοδος
        if shot:
            self.root.after(4000, lambda: self._screenshot_and_quit(shot))
        self.root.mainloop()

    def _screenshot_and_quit(self, path):
        from PIL import ImageGrab
        if os.environ.get("DCP_SHOT_HASH"):  # συμπλήρωση στήλης hash πριν το screenshot
            for i, it in enumerate(self.items):
                it["hash"] = verify_cpl(it["cpl"])
                self.refresh_row(i)
            self.show_details()
        r = self.root
        r.lift()
        r.attributes("-topmost", True)
        r.update()
        x, y = r.winfo_rootx(), r.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + r.winfo_width(), y + r.winfo_height()), all_screens=True).save(path)
        r.destroy()


def launcher(initial=None, open_linux=False):
    install_translation()  # αγγλικά: μετάφραση ό,τι εμφανίζεται (στα ελληνικά δεν κάνει τίποτα)
    LauncherWindow(initial, open_linux).run()
