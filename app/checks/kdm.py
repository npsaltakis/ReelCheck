"""KDM: για ποιον server και μέχρι πότε ισχύει (δεν αποκρυπτογραφεί)."""
import datetime
import os
import re

from app.core.util import find_all, local, norm_id, xml_root


# ======================================================================== KDM
KDMS = {}  # cpl id -> [kdm]


def parse_kdm(path):
    root = xml_root(path)
    if root is None or local(root.tag) != "DCinemaSecurityMessage":
        return None

    def first(name):
        e = find_all(root, name)
        return e[0].text.strip() if e and e[0].text else ""

    def date(s):
        try:
            return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    subj = ""
    rec = find_all(root, "Recipient")
    if rec:
        x = find_all(rec[0], "X509SubjectName")
        subj = x[0].text.strip() if x and x[0].text else ""
    cn = re.search(r"CN=([^,]+)", subj)
    return {"path": path, "cpl_id": norm_id(first("CompositionPlaylistId")),
            "title": first("ContentTitleText"), "server": (cn.group(1) if cn else subj)[:80],
            "device": first("DeviceListDescription"),
            "from": date(first("ContentKeysNotValidBefore")), "to": date(first("ContentKeysNotValidAfter")),
            "keys": len(find_all(root, "TypedKeyId"))}


def scan_kdms(folder, recursive=True):
    """Βρίσκει KDM (.xml) σε φάκελο. Επιστρέφει πόσα βρέθηκαν."""
    n = 0
    for root, dirs, files in os.walk(folder):
        for f in files:
            if not f.lower().endswith(".xml"):
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                with open(p, "rb") as fh:
                    if b"DCinemaSecurityMessage" not in fh.read(4096):
                        continue
            except OSError:
                continue
            k = parse_kdm(p)
            if k and k["cpl_id"] and add_kdm(k):
                n += 1
        if not recursive:
            break
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    return n


def kdm_state(k, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    a, b = k.get("from"), k.get("to")
    if a and a.tzinfo is None:
        a = a.replace(tzinfo=datetime.timezone.utc)
    if b and b.tzinfo is None:
        b = b.replace(tzinfo=datetime.timezone.utc)
    if a and now < a:
        return "δεν ισχύει ακόμα"
    if b and now > b:
        return "έληξε"
    return "ισχύει"


def kdm_lines(cpl):
    out = []
    for k in KDMS.get(cpl.id, []):
        f = k["from"].astimezone().strftime("%d/%m/%Y %H:%M") if k["from"] else "?"
        t = k["to"].astimezone().strftime("%d/%m/%Y %H:%M") if k["to"] else "?"
        out.append(f"{kdm_state(k).upper()}: {f} έως {t}, server {k['server'] or '?'}"
                   + (f" ({k['device']})" if k["device"] else "") + f", {k['keys']} κλειδιά")
    return out


def kdm_issues(cpl):
    if not cpl.encrypted:
        return []
    ks = KDMS.get(cpl.id, [])
    if not ks:
        return [("info", "κρυπτογραφημένο: δεν βρέθηκε KDM (Εργαλεία → Φόρτωση KDM)")]
    states = [kdm_state(k) for k in ks]
    if "ισχύει" in states:
        best = max((k for k in ks if kdm_state(k) == "ισχύει"),
                   key=lambda k: k["to"].timestamp() if k["to"] else float("inf"))
        to = best["to"].astimezone().strftime("%d/%m/%Y") if best["to"] else "?"
        return [("info", f"KDM ισχύει έως {to} (server {best['server']})")]
    if "δεν ισχύει ακόμα" in states:
        return [("warn", "KDM δεν ισχύει ακόμα")]
    return [("warn", "το KDM έληξε")]


def add_kdm(k):
    """Προσθήκη KDM στη λίστα (χωρίς διπλά). True αν ήταν καινούριο."""
    lst = KDMS.setdefault(k["cpl_id"], [])
    if any(x["path"] == k["path"] for x in lst):
        return False
    lst.append(k)
    return True


def add_kdm_files(paths):
    """Φόρτωση KDM από αρχεία. Επιστρέφει (νέα, όχι KDM)."""
    n = bad = 0
    for p in paths:
        k = parse_kdm(p)
        if not k:
            bad += 1
        elif add_kdm(k):
            n += 1
    return n, bad
