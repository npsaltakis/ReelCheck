"""
Πρόγραμμα προβολών: προβολές ανά μέρα / αίθουσα, λήξη με βάση τη διάρκεια της ταινίας, συγκρούσεις
στην ίδια αίθουσα, και εξαγωγή HTML (για εκτύπωση) και CSV.
"""
import datetime
import html
import os
import uuid

from app.core.config import APP_DIR, APP_NAME, APP_VERSION
from app.core.i18n import tr, tr_html
from app.core.util import load_json, save_json, write_excel_csv

SCHEDULE_FILE = os.path.join(APP_DIR, "schedule.json")
DATE_FMT, TIME_FMT = "%d/%m/%Y", "%H:%M"


def new_screening(date, time, room, title, minutes, extra=0, cpl_id=None, notes=""):
    return {"id": uuid.uuid4().hex[:12], "date": date, "time": time, "room": room.strip(), "title": title.strip(),
            "minutes": float(minutes or 0), "extra": float(extra or 0), "cpl_id": cpl_id, "notes": notes.strip()}


def load(path=None):
    return load_json(path or SCHEDULE_FILE, [])


def save(screenings, path=None):
    save_json(path or SCHEDULE_FILE, screenings, indent=1)


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
        for i, (_, b1, id1) in enumerate(items):
            for a2, _, id2 in items[i + 1:]:
                if a2 >= b1:
                    break
                out.setdefault(id1, []).append(id2)
                out.setdefault(id2, []).append(id1)
    return out


def write_csv(screenings, path, status_of=lambda s: ""):
    rows = []
    for s in sorted(screenings, key=sort_key):
        try:
            end = start_end(s)[1].strftime(TIME_FMT)
        except ValueError:
            end = "?"
        rows.append([s["date"], s["time"], end, s["room"], s["title"], f"{s['minutes']:.0f}", f"{s['extra']:.0f}",
                     tr(status_of(s)), s["notes"]])
    header = ("Ημερομηνία", "Ώρα", "Λήξη", "Αίθουσα", "Ταινία", "Διάρκεια (λεπτά)", "Επιπλέον (λεπτά)", "DCP",
              "Σημειώσεις")
    write_excel_csv(path, [tr(h) for h in header], rows)


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


# ======================================================================== ΗΜΕΡΟΛΟΓΙΟ (.ics)
def _ics_text(s):
    """Κείμενο iCalendar: διαφυγή των \\ ; , και αλλαγών γραμμής."""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line):
    """Γραμμές έως 75 bytes (RFC 5545), οι συνέχειες αρχίζουν με κενό."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 74:
            out.append(cur)
            cur = " "
        cur += ch
    out.append(cur)
    return "\r\n".join(out)


def write_ics(screenings, path, status_of=lambda s: "", calendar="Πρόγραμμα προβολών"):
    """Ημερολόγιο για Google / Outlook / κινητό: μία εκδήλωση ανά προβολή (τοπική ώρα)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{APP_NAME}//{APP_VERSION}//EL", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_text(tr(calendar))}"]
    for s in sorted(screenings, key=sort_key):
        try:
            start, end = start_end(s)
        except ValueError:
            continue
        desc = "\n".join(x for x in (s.get("notes", ""), (tr("DCP") + ": " + tr(status_of(s))) if status_of(s) else "")
                         if x)
        lines += ["BEGIN:VEVENT", f"UID:{s['id']}@reelcheck", f"DTSTAMP:{stamp}",
                  f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}",
                  f"SUMMARY:{_ics_text(s['title'])} ({_ics_text(s['room'])})", f"LOCATION:{_ics_text(s['room'])}"]
        if desc:
            lines.append(f"DESCRIPTION:{_ics_text(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(_ics_fold(l) for l in lines) + "\r\n")


# ======================================================================== ΦΥΛΛΑ ΜΗΧΑΝΙΚΟΥ ΠΡΟΒΟΛΗΣ
_SHEET_CSS = (
    "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#000}"
    ".page{page-break-after:always;margin-bottom:40px}.page:last-child{page-break-after:auto}"
    "h1{font-size:22px;margin:0}.sub{color:#444;margin:2px 0 14px}"
    "table{border-collapse:collapse;width:100%}th,td{border:1px solid #888;padding:7px 8px;font-size:13px;"
    "text-align:left;vertical-align:top}th{background:#eee}.box{width:22px;text-align:center;font-size:16px}"
    ".big{font-size:16px;font-weight:600}.warn{color:#b06000;font-weight:600}.err{color:#c5221f;font-weight:600}"
    ".small{color:#555;font-size:12px}.sign{margin-top:28px;font-size:13px}"
    "@media print{body{margin:10mm}}")


def write_day_sheets(screenings, path, details_of=lambda s: {}, title="Φύλλο προβολών"):
    """
    Μία σελίδα ανά μέρα και αίθουσα για τον μηχανικό προβολής, με κουτάκια για έλεγχο.
    details_of(s) -> {"status", "format", "audio", "subs", "kdm", "duration"} για το DCP της προβολής.
    """
    esc = html.escape
    clash = conflicts(screenings)
    pages = {}
    for s in sorted(screenings, key=sort_key):
        pages.setdefault((s["date"], s["room"]), []).append(s)
    out = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>{esc(title)}</title>"
           f"<style>{_SHEET_CSS}</style></head><body>"]
    for (day, room), items in pages.items():
        out.append(f"<div class='page'><h1>{esc(room)} · {esc(day)}</h1>"
                   f"<div class='sub'>{esc(title)} · {len(items)} προβολές</div><table><tr><th>Ώρα</th><th>Λήξη</th>"
                   "<th>Ταινία</th><th>Μορφή</th><th>Υπότιτλοι</th><th>DCP / KDM</th><th class='box'>Server</th>"
                   "<th class='box'>Test</th><th class='box'>Προβλ.</th><th>Παρατηρήσεις</th></tr>")
        for s in items:
            out.append(_sheet_row(s, details_of(s) or {}, s["id"] in clash))
        out.append("</table><div class='sign'>Μηχανικός προβολής: ______________________ "
                   "&nbsp; Υπογραφή: ______________</div></div>")
    out.append(f"<p class='small'>{APP_NAME} v{APP_VERSION} · {datetime.datetime.now():%d/%m/%Y %H:%M}</p>"
               "</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(tr_html("\n".join(out)))


def _sheet_row(s, d, clashes):
    esc = html.escape
    try:
        end = start_end(s)[1].strftime(TIME_FMT)
    except ValueError:
        end = "?"
    status = d.get("status", "")
    cls = {"ΣΦΑΛΜΑ": "err", "ΠΡΟΣΟΧΗ": "warn"}.get(status.split()[0] if status else "", "")
    dcp = esc(status or "χωρίς DCP") + (f"<br><span class='warn'>{esc(d['kdm'])}</span>" if d.get("kdm") else "")
    notes = esc(s.get("notes", "")) + (" <span class='err'>ΣΥΓΚΡΟΥΣΗ ΩΡΑΣ</span>" if clashes else "")
    fmt = " · ".join(x for x in (d.get("format"), d.get("audio"), d.get("duration")) if x)
    return (f"<tr><td class='big'>{esc(s['time'])}</td><td>{end}</td><td class='big'>{esc(s['title'])}</td>"
            f"<td>{esc(fmt)}</td><td>{esc(d.get('subs', ''))}</td><td class='{cls}'>{dcp}</td>"
            f"<td class='box'>☐</td><td class='box'>☐</td><td class='box'>☐</td><td>{notes}</td></tr>")
