"""Εργαλεία του κεντρικού παραθύρου: βίντεο → DCP, μετατροπή βίντεο, ρυθμίσεις, δίσκος Linux, πρόγραμμα
προβολών, παραλαβή και αντιγραφή DCP, κατάλογος φεστιβάλ. Mixin της LauncherWindow."""
import os
import shutil
import threading
import time
import webbrowser
from tkinter import filedialog, messagebox

from app.checks.scan import scan_cpl
from app.checks.spec import get_spec
from app.core import prefs
from app.core.config import APP_NAME, get_setting, log, set_setting
from app.core.hashing import verify_cpl
from app.core.report import item_row, write_report
from app.core.subtitles import track_summary
from app.core.util import fmt_size
from app.tools.extfs import unique_targets
from app.tools.catalog import catalog_table, read_catalog
from app.tools.convert import convert
from app.tools.copier import copy_verify, dcp_folders_for, write_copy_report
from app.tools.ingest import report_path
from app.tools.dcpomatic import DOWNLOAD_PAGE, find_dcpomatic, find_downloaded_installer, install
from app.tools.transcode import gpu_name, output_path, transcode_dcp, transcode_file
from app.ui.convert_window import ConvertWindow
from app.ui.ingest_window import IngestWindow
from app.ui.settings_window import SettingsWindow
from app.ui.linux_disk_window import LinuxDiskWindow
from app.ui.schedule_window import ScheduleWindow
from app.ui.transcode_window import TranscodeWindow, dcp_item
from app.ui.dialogs import CatalogWindow
from app.ui.spawn import relaunch, relaunch_as_admin
from app.ui.widgets import ask_folder


class ToolsMixin:
    """Χρειάζεται από την LauncherWindow: root, theme, items, start_job, info_box, load, refresh_all κ.λπ."""

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

        def dcp_status(cpl_id):
            it = next((x for x in self.items if x["cpl"].id == cpl_id), None)
            return item_row(it)["Κατάσταση"] if it else ""

        ScheduleWindow(self.root, self.theme, films, dcp_status)

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
        dest = ask_folder(self.root, "Πού να αντιγραφούν τα DCP;")
        if not dest:
            return
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
