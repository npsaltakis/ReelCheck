"""Εργασίες στο παρασκήνιο (thread) με πρόοδο και ακύρωση, για παράθυρα tkinter."""
import queue
import threading

from app.core.config import log
from app.core.hashing import Cancelled


class JobRunner:
    """
    Τρέχει μία εργασία τη φορά σε thread. Η εργασία fn(progress, cancel) καλεί progress(κλάσμα, κείμενο).
    Τα μηνύματα προς το GUI περνούν από ουρά και τα διαβάζει το poll() στο main thread.
    """

    def __init__(self, root, on_progress, on_done, on_busy_change=None, interval=100):
        self.root = root
        self.on_progress, self.on_done, self.on_busy_change = on_progress, on_done, on_busy_change
        self.events = queue.Queue()
        self.name = None
        self.cancel_event = None
        self.interval = interval
        self.handlers = {}

    @property
    def busy(self):
        return self.name is not None

    def start(self, name, fn):
        """False αν τρέχει ήδη άλλη εργασία."""
        if self.busy:
            return False
        self.name, self.cancel_event = name, threading.Event()
        self._busy_changed()
        threading.Thread(target=self._run, args=(name, fn, self.cancel_event), daemon=True, name=name).start()
        return True

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()

    def post(self, kind, *args):
        """Μήνυμα από οποιοδήποτε thread προς το GUI (kind: 'call' ή δικό σου με on(kind, handler))."""
        self.events.put((kind, *args))

    def call_in_gui(self, fn):
        self.post("call", fn)

    def on(self, kind, handler):
        self.handlers[kind] = handler

    def _run(self, name, fn, cancel):
        try:
            fn(lambda frac, msg: self.post("progress", frac, msg), cancel)
            self.post("done", name, None)
        except Cancelled:
            self.post("done", name, "ακυρώθηκε")
        except Exception as e:
            log.exception("Σφάλμα στην εργασία %s", name)
            self.post("done", name, f"σφάλμα: {e}")

    def _busy_changed(self):
        if self.on_busy_change:
            self.on_busy_change(self.busy)

    def poll(self):
        try:
            while True:
                kind, *args = self.events.get_nowait()
                if kind == "progress":
                    self.on_progress(*args)
                elif kind == "done":
                    self.name, self.cancel_event = None, None
                    self._busy_changed()
                    self.on_done(*args)
                elif kind == "call":
                    args[0]()
                elif kind in self.handlers:
                    self.handlers[kind](*args)
        except queue.Empty:
            pass
        self.root.after(self.interval, self.poll)
