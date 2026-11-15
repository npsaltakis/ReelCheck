"""Αντιγραφή DCP σε δίσκο με επαλήθευση SHA-1."""
import datetime
import os
import shutil

from app.core.config import APP_NAME, APP_VERSION, log
from app.core.i18n import tr
from app.core.dcp import read_assetmap, read_pkls
from app.core.hashing import remember_hash
from app.core.util import fmt_size, sha1_file


# ======================================================================== ΑΝΤΙΓΡΑΦΗ ΜΕ ΕΠΑΛΗΘΕΥΣΗ
def dcp_folders_for(cpls):
    """Φάκελοι που χρειάζονται για να παίξουν τα CPL (μαζί με τα OV των VF)."""
    out = []
    for c in cpls:
        for d in [c.folder] + sorted(set(c.external.values())):
            if d not in out:
                out.append(d)
    return out


def _copy_jobs(folders, dest):
    """[(πηγή, προορισμός, μέγεθος, SHA-1 από το PKL ή None)] για όλα τα αρχεία των φακέλων."""
    jobs = []
    for src in folders:
        dst_root = os.path.join(dest, os.path.basename(os.path.normpath(src)))
        pkl = read_pkls(src)
        by_path = {os.path.normcase(os.path.abspath(p)): pkl.get(aid) for aid, p in read_assetmap(src).items()}
        for root, _dirs, files in os.walk(src):
            for f in files:
                s = os.path.join(root, f)
                info = by_path.get(os.path.normcase(os.path.abspath(s))) or {}
                jobs.append((s, os.path.join(dst_root, os.path.relpath(s, src)), os.path.getsize(s), info.get("hash")))
    return jobs


def _already_there(dst, size):
    return os.path.isfile(dst) and os.path.getsize(dst) == size


def _check_space(jobs, dest):
    need = sum(size for _s, d, size, _h in jobs if not _already_there(d, size))
    os.makedirs(dest, exist_ok=True)
    free = shutil.disk_usage(dest).free
    if need > free:
        raise OSError(f"Δεν χωράει: χρειάζονται {fmt_size(need)}, ελεύθερα {fmt_size(free)}")


class _Copier:
    """Αντιγραφή και επαλήθευση αρχείων με κοινή πρόοδο (κάθε αρχείο διαβάζεται δύο φορές: αντιγραφή + έλεγχος)."""

    def __init__(self, total, progress, cancel):
        self.grand, self.done = max(1, total * 2), 0
        self.progress, self.cancel = progress, cancel

    def hash(self, path, label, sink=None):
        def step(b):
            self.done += len(b)
            if self.progress:
                self.progress(self.done / self.grand, f"{label} {os.path.basename(path)[:40]}")
        return sha1_file(path, on_chunk=step, cancel=self.cancel, sink=sink)

    def copy(self, src, dst):
        """Αντιγραφή μέσω .part (ώστε ένα μισό αρχείο να μη μοιάζει ποτέ ολόκληρο). Επιστρέφει το SHA-1 της πηγής."""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".part"
        with open(tmp, "wb") as out:
            src_hash = self.hash(src, "Αντιγραφή", out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dst)
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass  # οι ημερομηνίες δεν είναι απαραίτητες
        return src_hash

    def run(self, job):
        src, dst, size, want = job
        want = (want or "").strip()
        copied = not _already_there(dst, size)
        if copied:
            src_hash = self.copy(src, dst)
        else:
            self.done += size  # υπάρχει ήδη: μόνο επαλήθευση
            src_hash = None
        got = self.hash(dst, "Επαλήθευση")
        remember_hash(dst, got)  # ο έλεγχος hash του αντιγράφου μετά δεν ξαναδιαβάζει το αρχείο
        ref = want or src_hash
        if not ref:  # υπήρχε ήδη και δεν είναι στο PKL: σύγκριση με το πρωτότυπο
            self.done -= size
            ref = self.hash(src, "Σύγκριση")
        status = "OK" if got == ref else "ΛΑΘΟΣ"
        detail = ("αντιγράφηκε" if copied else "υπήρχε ήδη") + (", έλεγχος με PKL" if want else ", έλεγχος με πρωτότυπο")
        if want and src_hash and src_hash != want:
            status, detail = "ΛΑΘΟΣ", "το πρωτότυπο δεν ταιριάζει με το PKL (χαλασμένο από πριν)"
        return {"file": dst, "status": status, "detail": detail, "size": size}


def copy_verify(folders, dest, progress=None, cancel=None):
    """
    Αντιγράφει κάθε φάκελο DCP μέσα στο dest και μετά ξαναδιαβάζει τα αντίγραφα και συγκρίνει SHA-1
    (με το PKL όπου υπάρχει, αλλιώς με το πρωτότυπο). Επιστρέφει [{"file", "status", "detail", "size"}].
    """
    jobs = _copy_jobs(folders, dest)
    _check_space(jobs, dest)
    copier = _Copier(sum(j[2] for j in jobs), progress, cancel)
    results = [copier.run(j) for j in jobs]
    log.info("Αντιγραφή στο %s: %d αρχεία, %d λάθη", dest, len(results), sum(r["status"] != "OK" for r in results))
    return results


def write_copy_report(results, dest):
    """Αρχείο κειμένου με το αποτέλεσμα μέσα στον προορισμό. Επιστρέφει τη διαδρομή ή None."""
    rep = os.path.join(dest, f"ReelCheck_{tr('αντιγραφή')}_{datetime.datetime.now():%Y%m%d_%H%M}.txt")
    try:
        with open(rep, "w", encoding="utf-8") as fh:
            fh.write(tr(f"{APP_NAME} v{APP_VERSION}: αντιγραφή με επαλήθευση") + "\n\n")
            for r in results:
                fh.write(f"{tr(r['status']):<6} {r['file']}  ({fmt_size(r['size'])}, {tr(r['detail'])})\n")
        return rep
    except OSError:
        return None
