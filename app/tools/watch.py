"""
Παρακολούθηση φακέλου: όταν εμφανιστεί νέο DCP (π.χ. τελείωσε ένα κατέβασμα ή μια αντιγραφή), ειδοποιεί.
Ένα DCP θεωρείται «έτοιμο» όταν έχει ASSETMAP και PKL και το μέγεθός του δεν αλλάζει για δύο ελέγχους.
"""
import os
import threading

from app.core.config import log
from app.core.dcp import find_dcps, read_pkls

PARTIAL_EXT = (".part", ".tmp", ".crdownload", ".partial", ".!qb", ".download")


def folder_state(folder):
    """(πλήθος αρχείων, συνολικό μέγεθος, έχει μισά αρχεία) ενός φακέλου DCP."""
    n = size = 0
    partial = False
    for root, _dirs, files in os.walk(folder):
        for f in files:
            low = f.lower()
            if low.endswith(PARTIAL_EXT):
                partial = True
            try:
                size += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                partial = True  # σβήστηκε / γράφεται εκείνη τη στιγμή
    return n, size, partial


class FolderWatcher:
    """
    Thread που ελέγχει τον φάκελο κάθε `interval` δευτ. on_ready(φάκελος DCP) καλείται από το thread
    (ο καλών το περνά στο GUI). Τα DCP που υπάρχουν ήδη στην εκκίνηση δεν αναφέρονται.
    """

    def __init__(self, folder, on_ready, interval=20):
        self.folder, self.on_ready, self.interval = folder, on_ready, interval
        self.stop_event = threading.Event()
        self.known = set()
        self.pending = {}  # φάκελος -> τελευταία κατάσταση
        self.thread = None

    def start(self):
        self.known = {os.path.normcase(d) for d in self._dcps()}
        self.thread = threading.Thread(target=self._run, daemon=True, name="watch")
        self.thread.start()
        log.info("Παρακολούθηση φακέλου %s (%d DCP υπάρχουν ήδη)", self.folder, len(self.known))

    def stop(self):
        self.stop_event.set()

    def _dcps(self):
        try:
            return find_dcps(self.folder) if os.path.isdir(self.folder) else []
        except OSError:
            return []

    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.poll()
            except Exception:  # ένα σφάλμα ανάγνωσης δεν σταματά την παρακολούθηση
                log.exception("Παρακολούθηση φακέλου")

    def poll(self):
        """Ένας έλεγχος· επιστρέφει τους φακέλους που μόλις έγιναν έτοιμοι."""
        ready = []
        for d in self._dcps():
            key = os.path.normcase(d)
            if key in self.known:
                continue
            state = folder_state(d)
            n, _size, partial = state
            if n and not partial and self.pending.get(key) == state and read_pkls(d):
                self.known.add(key)
                self.pending.pop(key, None)
                ready.append(d)
                self.on_ready(d)
            else:
                self.pending[key] = state
        return ready
