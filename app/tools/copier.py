"""Αντιγραφή DCP σε δίσκο με επαλήθευση SHA-1."""
import base64
import datetime
import hashlib
import os
import shutil

from app.core.config import APP_NAME, APP_VERSION, log
from app.core.dcp import read_assetmap, read_pkls
from app.core.hashing import Cancelled
from app.core.util import fmt_size


# ======================================================================== ΑΝΤΙΓΡΑΦΗ ΜΕ ΕΠΑΛΗΘΕΥΣΗ
def dcp_folders_for(cpls):
    """Φάκελοι που χρειάζονται για να παίξουν τα CPL (μαζί με τα OV των VF)."""
    out = []
    for c in cpls:
        for d in [c.folder] + sorted(set(c.external.values())):
            if d not in out:
                out.append(d)
    return out


def copy_verify(folders, dest, progress=None, cancel=None):
    """
    Αντιγράφει κάθε φάκελο DCP μέσα στο dest και μετά ξαναδιαβάζει τα αντίγραφα και συγκρίνει SHA-1
    (με το PKL όπου υπάρχει, αλλιώς με το πρωτότυπο). Επιστρέφει [{"file", "status", "detail"}].
    """
    jobs, total = [], 0
    for src in folders:
        name = os.path.basename(os.path.normpath(src))
        dst_root = os.path.join(dest, name)
        amap = read_assetmap(src)
        pkl = read_pkls(src)
        by_path = {os.path.normcase(os.path.abspath(p)): pkl.get(aid) for aid, p in amap.items()}
        for root, _dirs, files in os.walk(src):
            for f in files:
                s = os.path.join(root, f)
                rel = os.path.relpath(s, src)
                size = os.path.getsize(s)
                info = by_path.get(os.path.normcase(os.path.abspath(s)))
                jobs.append((s, os.path.join(dst_root, rel), size, (info or {}).get("hash")))
                total += size
    need = sum(sz for s, d, sz, h in jobs if not (os.path.isfile(d) and os.path.getsize(d) == sz))
    os.makedirs(dest, exist_ok=True)
    free = shutil.disk_usage(dest).free
    if need > free:
        raise OSError(f"Δεν χωράει: χρειάζονται {fmt_size(need)}, ελεύθερα {fmt_size(free)}")
    grand = total * 2
    done = [0]
    results = []

    def stream_hash(path, label, out_fh=None):
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                b = fh.read(8 * 1024 * 1024)
                if not b:
                    break
                h.update(b)
                if out_fh:
                    out_fh.write(b)
                done[0] += len(b)
                if progress:
                    progress(done[0] / max(1, grand), f"{label} {os.path.basename(path)[:40]}")
        return base64.b64encode(h.digest()).decode()

    for s, d, size, want in jobs:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        src_hash = None
        if os.path.isfile(d) and os.path.getsize(d) == size:
            done[0] += size  # υπάρχει ήδη: μόνο επαλήθευση
            copied = False
        else:
            tmp = d + ".part"
            with open(tmp, "wb") as out:
                src_hash = stream_hash(s, "Αντιγραφή", out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, d)
            try:
                shutil.copystat(s, d)
            except OSError:
                pass
            copied = True
        got = stream_hash(d, "Επαλήθευση")
        ref = (want or "").strip() or src_hash
        if not ref:  # υπήρχε ήδη και δεν είναι στο PKL: σύγκριση με το πρωτότυπο
            done[0] -= size
            ref = stream_hash(s, "Σύγκριση")
        st = "OK" if got == ref else "ΛΑΘΟΣ"
        detail = ("αντιγράφηκε" if copied else "υπήρχε ήδη") + (", έλεγχος με PKL" if want else ", έλεγχος με πρωτότυπο")
        if want and src_hash and src_hash != want.strip():
            st, detail = "ΛΑΘΟΣ", "το πρωτότυπο δεν ταιριάζει με το PKL (χαλασμένο από πριν)"
        results.append({"file": d, "status": st, "detail": detail, "size": size})
    log.info("Αντιγραφή στο %s: %d αρχεία, %d λάθη", dest, len(results), sum(r["status"] != "OK" for r in results))
    return results


def write_copy_report(results, dest):
    """Αρχείο κειμένου με το αποτέλεσμα μέσα στον προορισμό. Επιστρέφει τη διαδρομή ή None."""
    rep = os.path.join(dest, f"ReelCheck_αντιγραφή_{datetime.datetime.now():%Y%m%d_%H%M}.txt")
    try:
        with open(rep, "w", encoding="utf-8") as fh:
            fh.write(f"{APP_NAME} v{APP_VERSION}: αντιγραφή με επαλήθευση\n\n")
            for r in results:
                fh.write(f"{r['status']:<6} {r['file']}  ({fmt_size(r['size'])}, {r['detail']})\n")
        return rep
    except OSError:
        return None
