"""
Πρόγραμμα προβολών: προβολές ανά μέρα / αίθουσα, λήξη με βάση τη διάρκεια της ταινίας, συγκρούσεις
στην ίδια αίθουσα, και εξαγωγή HTML (για εκτύπωση) και CSV.
"""
import csv
import datetime
import html
import json
import os
import uuid

from app.core.config import APP_DIR, APP_NAME, APP_VERSION
from app.core.i18n import tr, tr_html

SCHEDULE_FILE = os.path.join(APP_DIR, "schedule.json")
DATE_FMT, TIME_FMT = "%d/%m/%Y", "%H:%M"


def new_screening(date, time, room, title, minutes, extra=0, cpl_id=None, notes=""):
    return {"id": uuid.uuid4().hex[:12], "date": date, "time": time, "room": room.strip(), "title": title.strip(),
            "minutes": float(minutes or 0), "extra": float(extra or 0), "cpl_id": cpl_id, "notes": notes.strip()}


def load(path=SCHEDULE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(screenings, path=SCHEDULE_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(screenings, f, ensure_ascii=False, indent=1)


def parse_when(date, time):
    """'15/11/2026', '20:30' -> datetime (ή ValueError με κατανοητό μήνυμα)."""
    try:
        return datetime.datetime.strptime(f"{date.strip()} {time.strip()}", f"{DATE_FMT} {TIME_FMT}")
    except ValueError:
        raise ValueError("Ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ και ώρα ΩΩ:ΛΛ, π.χ. 15/11/2026 και 20:30")


def start_end(s):
    start = parse_when(s["date"], s["time"])
    return start, start + datetime.timedelta(minutes=s["minutes"] + s.get("extra", 0))


def sort_key(s):
    try:
        return (parse_when(s["date"], s["time"]), s["room"])
    except ValueError:
        return (datetime.datetime.max, s["room"])


def conflicts(screenings):
    """{id: [ids που επικαλύπτονται στην ίδια αίθουσα]}."""
    out = {}
    by_room = {}
    for s in screenings:
        try:
            by_room.setdefault(s["room"].lower(), []).append((*start_end(s), s["id"]))
        except ValueError:
            continue
    for items in by_room.values():
        items.sort()
        for i, (a1, b1, id1) in enumerate(items):
            for a2, b2, id2 in items[i + 1:]:
                if a2 >= b1:
                    break
                out.setdefault(id1, []).append(id2)
                out.setdefault(id2, []).append(id1)
    return out


def write_csv(screenings, path, status_of=lambda s: ""):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("sep=;\n")
        w = csv.writer(f, delimiter=";")
        w.writerow([tr(h) for h in ("Ημερομηνία", "Ώρα", "Λήξη", "Αίθουσα", "Ταινία", "Διάρκεια (λεπτά)",
                                    "Επιπλέον (λεπτά)", "DCP", "Σημειώσεις")])
        for s in sorted(screenings, key=sort_key):
            try:
                end = start_end(s)[1].strftime(TIME_FMT)
            except ValueError:
                end = "?"
            w.writerow([s["date"], s["time"], end, s["room"], s["title"], f"{s['minutes']:.0f}", f"{s['extra']:.0f}",
                        tr(status_of(s)), s["notes"]])


def write_html(screenings, path, status_of=lambda s: "", title="Πρόγραμμα προβολών"):
    """Πρόγραμμα ανά μέρα, για εκτύπωση."""
    esc = html.escape
    clash = conflicts(screenings)
    days = {}
    for s in sorted(screenings, key=sort_key):
        days.setdefault(s["date"], []).append(s)
    out = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>{esc(title)}</title><style>"
           "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d1d1f}h1{font-size:22px}"
           "h2{font-size:17px;margin:22px 0 6px;border-bottom:2px solid #333}table{border-collapse:collapse;width:100%}"
           "th,td{border:1px solid #ccc;padding:6px 8px;font-size:13px;text-align:left}th{background:#eee}"
           ".clash{color:#c5221f;font-weight:600}.ΣΦΑΛΜΑ{color:#c5221f}.ΠΡΟΣΟΧΗ{color:#b06000}.OK{color:#137333}"
           ".small{color:#666;font-size:12px}@media print{body{margin:0}}</style></head><body>",
           f"<h1>{esc(title)}</h1><div class='small'>{datetime.datetime.now():%d/%m/%Y %H:%M} · "
           f"{len(screenings)} προβολές</div>"]
    for day, items in days.items():
        out.append(f"<h2>{esc(day)}</h2><table><tr><th>Ώρα</th><th>Λήξη</th><th>Αίθουσα</th><th>Ταινία</th>"
                   "<th>DCP</th><th>Σημειώσεις</th></tr>")
        for s in items:
            try:
                end = start_end(s)[1].strftime(TIME_FMT)
            except ValueError:
                end = "?"
            st = status_of(s)
            note = esc(s["notes"]) + (" <span class='clash'>ΣΥΓΚΡΟΥΣΗ ΩΡΑΣ</span>" if s["id"] in clash else "")
            out.append(f"<tr><td>{esc(s['time'])}</td><td>{end}</td><td>{esc(s['room'])}</td><td>{esc(s['title'])}"
                       f"</td><td class='{esc(st.split()[0]) if st else ''}'>{esc(st)}</td><td>{note}</td></tr>")
        out.append("</table>")
    out.append(f"<p class='small'>{APP_NAME} v{APP_VERSION}</p></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(tr_html("\n".join(out)))
