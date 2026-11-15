"""Παράθυρο επιλογής: λίστα ταινιών, λεπτομέρειες, έλεγχοι και εργαλεία."""
import os
import re
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
from app.core.config import APP_DIR, APP_NAME, APP_VERSION, LOG_FILE, PLAYER_ENDED, VIDEO_EXT, get_setting, \
    log, resource, set_setting
from app.core.dcp import find_dcps, load_dcp
from app.core.hashing import verify_cpl
from app.core.media import probe, thumbnail
from app.core.notes import load_notes
from app.core.report import item_row, status_of, write_csv, write_report
from app.core.subtitles import track_summary
from app.core.util import fmt_time
from app.tools.catalog import catalog_table, read_catalog
from app.tools.copier import copy_verify, dcp_folders_for, write_copy_report
from app.tools.playlist import playlist_order
from app.tools.update import can_self_update, check_update, download_update, install_update
from app.ui.dialogs import CatalogWindow, SpecDialog
from app.ui.jobs import JobRunner
from app.ui.spawn import player_command, start_player
from app.ui.theme import MODE_LABELS, Theme, window_center
from app.ui.winutil import point_on_screen

COLUMNS = ("Κατάσταση", "Τίτλος", "Διάρκεια", "Εικόνα", "Ήχος", "Υπότιτλοι", "Αρχεία", "Hash", "Σημειώσεις")
WIDTHS = (80, 300, 85, 85, 85, 160, 80, 100, 80)
LAST_FOLDER_FILE = os.path.join(APP_DIR, "last_folder.txt")
ISSUE_ICON = {"err": "✖", "warn": "⚠", "info": "ℹ"}
DEFAULT_GEOMETRY = "1150x680"
GEOMETRY_RE = re.compile(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)")


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
    def __init__(self, initial=None):
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
        self.half = tk.IntVar(value=0)
        self.fullscreen = tk.IntVar(value=0)
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
        start = initial or last_folder()
        if start and os.path.isdir(start):
            self.root.after(150, lambda: self.load(start))
        if not os.environ.get("DCP_LAUNCHER_SHOT"):
            self.root.after(3000, self.update_check)

    # ================================================================== κατασκευή
    def _build_top(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Άνοιγμα φακέλου DCP…", command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="Άνοιγμα βίντεο…", command=self.open_video).pack(side="left", padx=(6, 0))
        self.entry = ttk.Entry(top, textvariable=self.path_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Φόρτωση", command=self.load_typed).pack(side="left")
        self.theme_btn = ttk.Button(top, width=15, command=self.cycle_theme)
        self.theme_btn.pack(side="left", padx=(8, 0))

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
        tools.add_command(label="Προδιαγραφές φεστιβάλ…", command=self.edit_spec)
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
        self.root.config(menu=menubar)

    def _bind(self):
        self.entry.bind("<Return>", lambda e: self.load_typed())
        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.tree.bind("<Double-Button-1>", lambda e: self.play_selected())
        self.tree.bind("<Return>", lambda e: self.play_selected())

    # ================================================================== θέμα
    def apply_theme(self):
        self.theme.apply_ttk()
        self.theme.style_text(self.info)
        self.theme.tag_tree(self.tree)
        self.theme_btn.configure(text=MODE_LABELS[self.theme.mode])

    def cycle_theme(self):
        self.theme.cycle()
        self.apply_theme()

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
        self.play_btn.state(["disabled"] if it["cpl"].encrypted else ["!disabled"])

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
            lines.append("\nΕΛΕΓΧΟΙ:")
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
    def edit_spec(self):
        def saved(spec):
            self.spec = spec
            self.refresh_all()
        SpecDialog(self.root, self.theme, self.spec, saved)

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


def launcher(initial=None):
    LauncherWindow(initial).run()
