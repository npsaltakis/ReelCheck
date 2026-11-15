"""Έλεγχος αρχείων με το PKL (μέγεθος + SHA-1), με cache."""
import os
import threading

from app.core.config import HASH_CACHE_FILE, log
from app.core.dcp import cpl_pkl
from app.core.util import fmt_size, load_json, save_json, sha1_file


# ----------------------------------------------------------------- HASH (PKL)
_hash_lock = threading.Lock()


def _hash_cache():
    return load_json(HASH_CACHE_FILE, {})


def cache_key(path):
    """Κλειδί cache: διαδρομή, μέγεθος, ώρα τροποποίησης (αν αλλάξει το αρχείο, ξαναϋπολογίζεται)."""
    return f"{os.path.abspath(path)}|{os.path.getsize(path)}|{int(os.path.getmtime(path))}"


def remember_hash(path, sha1_b64):
    """Αποθήκευση hash που υπολογίστηκε αλλού (π.χ. στην αντιγραφή), ώστε ο έλεγχος να είναι άμεσος."""
    _hash_cache_put(cache_key(path), sha1_b64)


def _hash_cache_put(key, value):
    with _hash_lock:
        c = _hash_cache()
        c[key] = value
        try:
            save_json(HASH_CACHE_FILE, c)
        except OSError:
            log.exception("Αποθήκευση cache hash")


def verify_cpl(cpl, progress=None, cancel=None):
    """
    Έλεγχος αρχείων του CPL με το PKL (μέγεθος + SHA-1).
    progress(done_bytes, total_bytes, όνομα αρχείου). Επιστρέφει λίστα αποτελεσμάτων.
    """
    pkl = cpl_pkl(cpl)  # τα assets του OV ελέγχονται με το PKL του OV
    items = [(cpl.id, cpl.path)] + list(cpl.assets.items())
    results, todo = [], []
    for aid, f in items:
        p = pkl.get(aid)
        name = os.path.basename(f) if f else (p["name"] if p else aid)
        r = {"id": aid, "file": f, "name": name, "status": "", "detail": ""}
        results.append(r)
        if not f or not os.path.isfile(f):
            r["status"], r["detail"] = "ΛΕΙΠΕΙ", "το αρχείο δεν υπάρχει"
            continue
        if not p or not p["hash"]:
            r["status"], r["detail"] = "ΧΩΡΙΣ PKL", "δεν υπάρχει στο PKL"
            continue
        size = os.path.getsize(f)
        if p["size"] and size != p["size"]:
            r["status"], r["detail"] = "ΛΑΘΟΣ ΜΕΓΕΘΟΣ", f"{size} αντί {p['size']} bytes"
            continue
        todo.append((r, f, p["hash"], size))
    total = sum(t[3] for t in todo)
    done = 0
    cache = _hash_cache()
    for r, f, want, size in todo:
        key = cache_key(f)
        got = cache.get(key)
        if got is None:
            def step(b, name=r["name"]):
                nonlocal done
                done += len(b)
                if progress:
                    progress(done, total, name)
            got = sha1_file(f, on_chunk=step, cancel=cancel)
            _hash_cache_put(key, got)
        else:
            done += size
            if progress:
                progress(done, total, r["name"])
        if got == want.strip():
            r["status"], r["detail"] = "OK", fmt_size(size)
        else:
            r["status"], r["detail"] = "ΛΑΘΟΣ HASH", "το αρχείο είναι χαλασμένο ή διαφορετικό"
    bad = [r for r in results if r["status"] not in ("OK",)]
    log.info("Hash %s: %d αρχεία, %d προβλήματα %s", cpl.title, len(results), len(bad),
             [(r["name"], r["status"]) for r in bad])
    return results


def hash_summary(results):
    if results is None:
        return "—"
    bad = [r for r in results if r["status"] != "OK"]
    if not bad:
        return "OK"
    if all(r["status"] == "ΧΩΡΙΣ PKL" for r in bad):
        return f"OK ({len(bad)} χωρίς PKL)"
    return f"ΣΦΑΛΜΑ ({len(bad)})"
