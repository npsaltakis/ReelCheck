"""
Εργαλεία φεστιβάλ του κεντρικού παραθύρου: σύγκριση εκδόσεων DCP, παρακολούθηση φακέλου, αναφορά PDF
για την επιτροπή, στατιστικά, επιστροφές, συγχρονισμός ήχου-εικόνας, ident. Mixin της LauncherWindow.
"""
import os
from tkinter import filedialog

from app.checks.sync import check_sync
from app.core import prefs
from app.core.config import log
from app.core.hashing import verify_cpl
from app.core.i18n import tr
from app.core.media import probe, thumbnail
from app.core.report import item_row, write_committee_report
from app.core.subtitles import track_summary
from app.checks.scan import scan_cpl
from app.tools import compare, ident
from app.tools.convert import convert
from app.tools.dcpomatic import find_dcpomatic
from app.tools.mediainfo import probe_media
from app.tools.pdf import find_browser, html_to_pdf
from app.tools.stats import festival_stats
from app.tools.watch import FolderWatcher
from app.ui.dialogs import TableWindow
from app.ui.ident_window import IdentWindow
from app.ui.returns_window import ReturnsWindow

COMPARE_TAGS = [("diff", "err"), ("info", "muted")]  # οι ίδιες γραμμές μένουν στο κανονικό χρώμα
SYNC_TAGS = {"ok": "OK", "warn": "ΠΡΟΣΟΧΗ", "err": "ΣΦΑΛΜΑ", "info": ""}


class FestivalMixin:
    """Χρειάζεται από την LauncherWindow: root, theme, items, jobs, start_job, info_box, notifier κ.λπ."""

    watcher = None

    # ------------------------------------------------------------------ κοινό: έλεγχος DCP
    def check_dcps(self, idx, label, on_done=None):
        """Ανάλυση, hash και σάρωση (όχι στα κρυπτογραφημένα) για τις ταινίες idx, και μετά on_done(idx)."""
        def work(i, p, cancel):
            it = self.items[i]
            c = it["cpl"]
            if it.get("info") is None:
                it["info"] = probe(c)
            it["hash"] = verify_cpl(c, lambda d, t, n: p(0.3 * d / max(1, t), f"hash {n[:30]}"), cancel)
            if not c.encrypted:
                it["scan"] = scan_cpl(c, lambda f, m: p(0.3 + 0.7 * f, m), cancel)

        self.start_job(label, self._per_item_job(idx, label, work))
        if on_done:
            self.when_idle(lambda: on_done(idx))

    def add_dcp_folder(self, folder):
        """Προσθήκη των CPL ενός φακέλου DCP στο τέλος της λίστας. Επιστρέφει τους νέους δείκτες."""
        start = len(self.items)
        self._read_dcps([folder])
        return list(range(start, len(self.items)))

    # ------------------------------------------------------------------ σύγκριση δύο DCP
    def compare_selected(self):
        sel = self.selected()
        if len(sel) != 2:
            self.info_box("Διάλεξε ακριβώς δύο ταινίες από τη λίστα (Ctrl + κλικ), π.χ. την παλιά και τη νέα έκδοση.")
            return
        a, b = (self.items[i] for i in sel)
        self.start_job("Σύγκριση DCP", lambda progress, cancel: self._compare_job(a, b, progress))

    def _compare_job(self, a, b, progress):
        progress(0.2, "Ανάγνωση στοιχείων")
        for it in (a, b):
            if it.get("info") is None:
                it["info"] = probe(it["cpl"])
        subs = [track_summary(it["cpl"])[0] for it in (a, b)]
        rows = compare.compare_cpls(a["cpl"], b["cpl"], a["info"], b["info"], *subs)
        self.jobs.call_in_gui(lambda: self._show_compare(a, b, rows))

    def _show_compare(self, a, b, rows):
        cols = [("Στοιχείο", 190), (f"A: {a['cpl'].title[:40]}", 380), (f"B: {b['cpl'].title[:40]}", 380), ("", 60)]
        table = [(n, va, vb, "✔" if k == compare.SAME else ("≠" if k == compare.DIFF else "")) for n, va, vb, k in rows]
        TableWindow(self.root, self.theme, "Σύγκριση δύο DCP", compare.summary(rows), cols, table,
                    "Σύγκριση_DCP.csv", stretch=2, size="1080x600",
                    tag_of=lambda r: {"✔": "same", "≠": "diff"}.get(r[3], "info"), tags=COMPARE_TAGS)

    # ------------------------------------------------------------------ στατιστικά
    def open_stats(self):
        if not self.items:
            self.info_box("Άνοιξε πρώτα έναν φάκελο με DCP.")
            return
        rows = festival_stats(self.items, lambda it: item_row(it)["Κατάσταση"])
        dcps = rows[0][1]
        TableWindow(self.root, self.theme, "Στατιστικά φεστιβάλ", f"{dcps} DCP στη λίστα · {rows[2][1]} συνολικά",
                    [("Μέτρηση", 240), ("Τιμή", 700)], rows, "Στατιστικά_φεστιβάλ.csv", size="980x520")

    # ------------------------------------------------------------------ επιστροφές
    def _film_refs(self, items):
        return [(it["cpl"].title, it["cpl"].id, it["cpl"].folder) for it in items if not it["cpl"].is_file]

    def open_returns(self, add_selected=False):
        preset = self._film_refs([self.items[i] for i in self.selected()]) if add_selected else ()
        if add_selected and not preset:
            self.info_box("Διάλεξε πρώτα ταινίες (DCP) από τη λίστα.")
            return
        ReturnsWindow(self.root, self.theme, lambda: self._film_refs(self.items), preset)

    # ------------------------------------------------------------------ αναφορά PDF για την επιτροπή
    def committee_pdf(self):
        idx = self.selected() or self.all_indices()
        if not idx:
            self.info_box("Άνοιξε πρώτα έναν φάκελο με DCP.")
            return
        if not find_browser():
            self.info_box("Για PDF χρειάζεται ο Microsoft Edge ή το Chrome. Χρησιμοποίησε «Αναφορά HTML» και "
                          "Εκτύπωση → Αποθήκευση ως PDF.")
            return
        path = filedialog.asksaveasfilename(title="Αναφορά PDF για την επιτροπή", defaultextension=".pdf",
                                            initialfile="Επιτροπή_ταινίες.pdf", filetypes=[("PDF", "*.pdf")])
        if path:
            self.start_job("Αναφορά PDF", lambda progress, cancel: self._committee_job(idx, path, progress, cancel))

    def _committee_job(self, idx, path, progress, cancel):
        items, frames = [self.items[i] for i in idx], {}
        for n, it in enumerate(items):
            if cancel.is_set():
                return
            c = it["cpl"]
            progress(0.8 * n / len(items), f"Καρέ {n + 1}/{len(items)}: {c.title[:40]}")
            if it.get("info") is None:
                it["info"] = probe(c)
            if not c.encrypted:
                frames[n] = [thumbnail(c, c.seconds * f, 480) for f in (0.15, 0.5, 0.8)]
        html_path = os.path.splitext(path)[0] + ".html"
        write_committee_report(items, html_path, frames)
        progress(0.9, "PDF")
        html_to_pdf(html_path, path)
        os.remove(html_path)
        self.jobs.call_in_gui(lambda: os.startfile(path))

    # ------------------------------------------------------------------ συγχρονισμός ήχου-εικόνας
    def sync_selected(self):
        idx = [i for i in self.selected() if not self.items[i]["cpl"].encrypted]
        if not idx:
            self.info_box("Διάλεξε ταινίες (μη κρυπτογραφημένες). Ο έλεγχος χρειάζεται sync test, 2-pop ή κλακέτα "
                          "στα πρώτα 2 λεπτά (ή στο σημείο όπου βρίσκεται ο κέρσορας της αναπαραγωγής).")
            return
        results = {}

        def work(i, p, cancel):
            results[i] = check_sync(self.items[i]["cpl"], 0.0, prefs.get("sync_seconds"), p, cancel)

        self.start_job("Συγχρονισμός", self._per_item_job(idx, "Συγχρονισμός", work))
        self.when_idle(lambda: self._show_sync(results))

    def _show_sync(self, results):
        if not results:
            return
        rows = []
        for i, r in results.items():
            self.items[i]["sync"] = r
            off = "" if r["offset_ms"] is None else f"{r['offset_ms']:+d} ms"
            rows.append((SYNC_TAGS[r["level"]] or "—", self.items[i]["cpl"].title, off, str(len(r["pairs"])), r["text"]))
        TableWindow(self.root, self.theme, "Συγχρονισμός ήχου-εικόνας",
                    "Θετικό = ο ήχος έρχεται αργότερα. Έως ±22 ms (μισό καρέ) είναι ανεπαίσθητο.",
                    [("Κατάσταση", 80), ("Ταινία", 300), ("Διαφορά", 80), ("Μετρήσεις", 80), ("Αποτέλεσμα", 520)],
                    rows, "Συγχρονισμός.csv", stretch=4, tag_of=lambda r: r[0], size="1100x420")

    # ------------------------------------------------------------------ ident
    def open_ident(self):
        IdentWindow(self.root, self.theme, self.run_ident, bool(find_dcpomatic()))

    def run_ident(self, opts):
        self.start_job("Ident φεστιβάλ", lambda progress, cancel: self._ident_job(opts, progress, cancel))

    def _ident_job(self, opts, progress, cancel):
        os.makedirs(opts["out_dir"], exist_ok=True)
        video = os.path.join(opts["out_dir"], f"{opts['name']}_ident.mp4")
        ident.make_ident_video(video, opts["logo"], opts["seconds"], opts["music"] or None, opts["text"],
                               opts["ratio"], lambda f, m: progress(0.15 * f, m), cancel)
        conv = {"mode": "single", "name": opts["name"], "kind": opts["kind"], "ratio": opts["ratio"], "fps": 24,
                "gap": 0.0, "normalize": False}
        results = convert([probe_media(video)], conv, opts["out_dir"], lambda f, m: progress(0.15 + 0.85 * f, m),
                          cancel)
        self.jobs.call_in_gui(lambda: self.when_idle(lambda: self._after_convert(results, opts["out_dir"])))

    # ------------------------------------------------------------------ παρακολούθηση φακέλου
    def restart_watch(self):
        """Ξεκινά / σταματά την παρακολούθηση σύμφωνα με τις Ρυθμίσεις."""
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        folder = prefs.folder("watch_folder")
        if prefs.get("watch_enabled") and folder:
            self.watcher = FolderWatcher(folder, lambda d: self.jobs.call_in_gui(lambda: self._watch_ready(d)),
                                         interval=prefs.get("watch_interval"))
            self.watcher.start()
            self.status_var.set(tr("Παρακολούθηση φακέλου: ") + folder)

    def _indices_in(self, folder):
        key = os.path.normcase(os.path.abspath(folder))
        return [i for i, it in enumerate(self.items) if os.path.normcase(os.path.abspath(it["cpl"].folder)) == key]

    def _watch_ready(self, folder):
        """Νέο DCP έτοιμο: προσθήκη στη λίστα (όταν δεν τρέχει εργασία), ειδοποίηση και αυτόματος έλεγχος."""
        log.info("Νέο DCP στον φάκελο παρακολούθησης: %s", folder)
        self.when_idle(lambda: self._watch_add(folder))

    def _watch_add(self, folder):
        try:
            idx = self._indices_in(folder) or self.add_dcp_folder(folder)
        except Exception:
            log.exception("Ανάγνωση νέου DCP %s", folder)
            idx = []
        if not idx:
            self.notifier.notify(tr("Νέος φάκελος DCP"), os.path.basename(folder) + ": " + tr("δεν βρέθηκε CPL"), "warn")
            return
        names = ", ".join(self.items[i]["cpl"].title for i in idx)
        if not prefs.get("watch_check"):
            self.notifier.notify(tr("Νέο DCP"), names)
            return
        self.notifier.notify(tr("Νέο DCP: έλεγχος…"), names)
        self.check_dcps(idx, "Έλεγχος νέου DCP", self._watch_checked)

    def _watch_checked(self, idx):
        lines = [f"{tr(item_row(self.items[i])['Κατάσταση'])}: {self.items[i]['cpl'].title}" for i in idx]
        bad = any(item_row(self.items[i])["Κατάσταση"] == "ΣΦΑΛΜΑ" for i in idx)
        self.notifier.notify(tr("Νέο DCP ελέγχθηκε"), "\n".join(lines), "err" if bad else "info")
        self.refresh_all()
