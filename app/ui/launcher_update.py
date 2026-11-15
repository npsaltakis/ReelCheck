"""Έλεγχος για νέα έκδοση και αυτόματη αναβάθμιση. Mixin της LauncherWindow."""
import threading
import time
import webbrowser
from tkinter import messagebox

from app.core.config import APP_NAME, APP_VERSION, get_setting, log, set_setting
from app.tools.update import can_self_update, check_update, download_update, install_update


class UpdateMixin:
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
