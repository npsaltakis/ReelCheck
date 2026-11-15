"""
Λίστα επιστροφών: ποια DCP / δίσκοι πρέπει να επιστραφούν, σε ποιον, μέχρι πότε, και αν επιστράφηκαν.
Αποθηκεύεται στο %APPDATA%\\ReelCheck\\returns.json.
"""
import datetime
import os
import uuid

from app.core.config import APP_DIR
from app.core.i18n import tr
from app.core.util import load_json, save_json, write_excel_csv

RETURNS_FILE = os.path.join(APP_DIR, "returns.json")
DATE_FMT = "%d/%m/%Y"
FIELDS = ("title", "disk", "to", "contact", "due", "returned", "notes")


def new_return(title, disk="", to="", contact="", due="", notes="", cpl_id=None, folder=""):
    return {"id": uuid.uuid4().hex[:12], "title": title.strip(), "disk": disk.strip(), "to": to.strip(),
            "contact": contact.strip(), "due": due.strip(), "returned": "", "notes": notes.strip(),
            "cpl_id": cpl_id, "folder": folder}


def load(path=None):
    return load_json(path or RETURNS_FILE, [])


def save(items, path=None):
    save_json(path or RETURNS_FILE, items, indent=1)


def parse_date(text):
    """'15/11/2026' -> date, κενό -> None, αλλιώς ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, DATE_FMT).date()
    except ValueError:
        raise ValueError("Ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ, π.χ. 30/11/2026")


def state(item, today=None):
    """'returned' | 'late' | 'soon' (μέσα σε 3 μέρες) | 'open'."""
    if item.get("returned"):
        return "returned"
    try:
        due = parse_date(item.get("due"))
    except ValueError:
        return "open"
    today = today or datetime.date.today()
    if due and due < today:
        return "late"
    if due and (due - today).days <= 3:
        return "soon"
    return "open"


STATE_LABELS = {"returned": "επιστράφηκε", "late": "ΕΚΠΡΟΘΕΣΜΟ", "soon": "λήγει σύντομα", "open": "εκκρεμεί"}


def mark_returned(item, when=None):
    item["returned"] = (when or datetime.date.today()).strftime(DATE_FMT)


def sort_key(item):
    try:
        due = parse_date(item.get("due")) or datetime.date.max
    except ValueError:
        due = datetime.date.max
    return (bool(item.get("returned")), due, item["title"].lower())


def summary(items):
    counts = {k: 0 for k in STATE_LABELS}
    for it in items:
        counts[state(it)] += 1
    return (f"{len(items)} DCP · {counts['open'] + counts['soon'] + counts['late']} εκκρεμούν"
            + (f" · {counts['late']} εκπρόθεσμα" if counts["late"] else "")
            + (f" · {counts['soon']} λήγουν σύντομα" if counts["soon"] else ""))


def write_csv(items, path):
    header = ["Ταινία", "Δίσκος", "Επιστροφή σε", "Επικοινωνία", "Προθεσμία", "Επιστράφηκε", "Κατάσταση", "Σημειώσεις"]
    rows = [[it["title"], it["disk"], it["to"], it["contact"], it["due"], it["returned"], tr(STATE_LABELS[state(it)]),
             it["notes"]] for it in sorted(items, key=sort_key)]
    write_excel_csv(path, [tr(h) for h in header], rows)
