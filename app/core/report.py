"""Κατάσταση ταινίας, γραμμές πίνακα, CSV και αναφορά HTML."""
import base64
import datetime
import html

from app.core.config import APP_NAME, APP_VERSION, log
from app.core.i18n import tr, tr_html
from app.core.hashing import hash_summary
from app.core.subtitles import track_summary
from app.core.util import fmt_time, write_excel_csv


# ----------------------------------------------------------------- ΚΑΤΑΣΤΑΣΗ / ΑΝΑΦΟΡΕΣ
def status_of(cpl, info, hash_results, notes, issues=None):
    """('OK' | 'ΠΡΟΣΟΧΗ' | 'ΣΦΑΛΜΑ', [λόγοι]). issues: [(err|warn|info, κείμενο)] από app.checks."""
    errors = [t for lv, t in issues or [] if lv == "err"]
    warns = [t for lv, t in issues or [] if lv == "warn"]
    if cpl.missing:
        errors.append(f"λείπουν {len(cpl.missing)} αρχεία")
    for k in ("pic_err", "snd_err"):
        if info and info.get(k):
            errors.append(("εικόνα: " if k == "pic_err" else "ήχος: ") + info[k])
    if hash_results:
        bad = [r for r in hash_results if r["status"] in ("ΛΑΘΟΣ HASH", "ΛΑΘΟΣ ΜΕΓΕΘΟΣ", "ΛΕΙΠΕΙ")]
        if bad:
            errors.append(f"hash: {len(bad)} αρχεία με πρόβλημα")
        if any(r["status"] == "ΧΩΡΙΣ PKL" for r in hash_results):
            warns.append("αρχεία χωρίς PKL")
    if cpl.encrypted:
        warns.append("κρυπτογραφημένο (θέλει KDM)")
    if not any(r.snd for r in cpl.reels):
        warns.append("χωρίς ήχο")
    if notes:
        warns.append(f"{len(notes)} σημειώσεις")
    if errors:
        return "ΣΦΑΛΜΑ", errors + warns
    if warns:
        return "ΠΡΟΣΟΧΗ", warns
    return "OK", []


def item_row(item):
    """Στοιχεία για πίνακα / CSV / αναφορά."""
    c, info = item["cpl"], item.get("info") or {}
    size = info.get("size")
    subs, _ = track_summary(c)
    st, why = status_of(c, info, item.get("hash"), item.get("notes"), item.get("issues"))
    return {
        "Κατάσταση": st,
        "Τίτλος": c.title,
        "Είδος": c.kind,
        "Διάρκεια": fmt_time(c.seconds, c.rate),
        "Reels": len(c.reels),
        "fps": f"{float(c.rate):g}",
        "Εικόνα": f"{size[0]}x{size[1]}" if size else "?",
        "3D": "ναι" if any(r.stereo for r in c.reels) else "όχι",
        "Ήχος": f"{info['ch']}ch {info['sr'] // 1000}kHz" if info.get("ch") else "όχι",
        "Υπότιτλοι": subs,
        "Κρυπτογρ.": "ναι" if c.encrypted else "όχι",
        "Αρχεία": f"λείπουν {len(c.missing)}" if c.missing else ("OK (+OV)" if c.external else "OK"),
        "Hash": hash_summary(item.get("hash")),
        "Σημειώσεις": len(item.get("notes") or []),
        "Παρατηρήσεις": "; ".join(why),
        "Φάκελος": c.folder,
    }


def write_csv(items, path):
    rows = [item_row(i) for i in items]
    header = list(rows[0].keys()) if rows else ["Τίτλος"]
    write_excel_csv(path, [tr(h) for h in header], [[tr(str(r[h])) for h in header] for r in rows])
    log.info("CSV: %s (%d ταινίες)", path, len(rows))


_REPORT_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d1d1f;background:#f5f5f7}
h1{font-size:22px;margin:0 0 4px} .sub{color:#666;margin-bottom:20px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #ddd;padding:6px 8px;font-size:13px;text-align:left;vertical-align:top}
th{background:#eee} .OK{color:#137333;font-weight:600} .ΠΡΟΣΟΧΗ{color:#b06000;font-weight:600}
.ΣΦΑΛΜΑ{color:#c5221f;font-weight:600}
.film{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin:20px 0;page-break-inside:avoid}
.film h2{font-size:17px;margin:0 0 10px} .grid{display:flex;gap:16px;flex-wrap:wrap}
.grid img{width:360px;max-width:100%;border:1px solid #ccc} .kv td:first-child{color:#666;width:130px}
.small{font-size:12px;color:#666}
@media (prefers-color-scheme:dark){
 body{background:#17181c;color:#e8e8ec} .sub,.small,.kv td:first-child{color:#9a9aa2}
 table,.film{background:#1f2026} th{background:#2a2b32} th,td,.film{border-color:#3a3c45}
 .grid img{border-color:#3a3c45} .OK{color:#5bd17a} .ΠΡΟΣΟΧΗ{color:#f0a640} .ΣΦΑΛΜΑ{color:#ff6b6b}
}
@media print{body{background:#fff;color:#000;margin:0} table,.film{background:#fff}}
"""


def write_report(items, path):
    """Αναφορά HTML (ανοίγει σε browser, εκτυπώνεται και σε PDF)."""
    esc = html.escape
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    rows = [item_row(i) for i in items]
    counts = {k: sum(1 for r in rows if r["Κατάσταση"] == k) for k in ("OK", "ΠΡΟΣΟΧΗ", "ΣΦΑΛΜΑ")}
    out = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>Αναφορά ελέγχου DCP</title>"
           f"<style>{_REPORT_CSS}</style></head><body>",
           f"<h1>Αναφορά ελέγχου DCP</h1><div class='sub'>{now} · {len(rows)} ταινίες · "
           f"<span class='OK'>{counts['OK']} OK</span> · <span class='ΠΡΟΣΟΧΗ'>{counts['ΠΡΟΣΟΧΗ']} προσοχή</span> · "
           f"<span class='ΣΦΑΛΜΑ'>{counts['ΣΦΑΛΜΑ']} σφάλμα</span></div>"]
    cols = ["Κατάσταση", "Τίτλος", "Διάρκεια", "Εικόνα", "Ήχος", "Υπότιτλοι", "Αρχεία", "Hash", "Σημειώσεις"]
    out.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
    for r in rows:
        out.append("<tr>" + "".join(
            f"<td class='{esc(str(r[c]))}'>{esc(str(r[c]))}</td>" if c == "Κατάσταση" else f"<td>{esc(str(r[c]))}</td>"
            for c in cols) + "</tr>")
    out.append("</table>")
    for item, r in zip(items, rows):
        c = item["cpl"]
        out.append(f"<div class='film'><h2><span class='{r['Κατάσταση']}'>[{r['Κατάσταση']}]</span> {esc(c.title)}</h2>"
                   "<div class='grid'>")
        png = item.get("thumb")
        if png:
            out.append(f"<img src='data:image/png;base64,{base64.b64encode(png).decode()}'>")
        out.append("<table class='kv'>")
        for k in ("Είδος", "Διάρκεια", "Reels", "fps", "Εικόνα", "3D", "Ήχος", "Υπότιτλοι", "Κρυπτογρ.",
                  "Αρχεία", "Hash", "Παρατηρήσεις", "Φάκελος"):
            out.append(f"<tr><td>{k}</td><td>{esc(str(r[k]))}</td></tr>")
        out.append("</table></div>")
        iss = item.get("issues") or []
        if iss:
            out.append("<p><b>Έλεγχοι</b></p><ul>" + "".join(
                f"<li class='{'ΣΦΑΛΜΑ' if lv == 'err' else ('ΠΡΟΣΟΧΗ' if lv == 'warn' else 'small')}'>{esc(t)}</li>"
                for lv, t in iss) + "</ul>")
        if c.missing:
            out.append("<p><b>Λείπουν:</b><br>" + "<br>".join(esc(m) for m in c.missing) + "</p>")
        hr = item.get("hash")
        if hr:
            out.append("<p><b>Έλεγχος hash</b></p><table><tr><th>Αρχείο</th><th>Αποτέλεσμα</th><th>Λεπτομέρειες</th></tr>")
            for h in hr:
                cls = "OK" if h["status"] == "OK" else ("ΠΡΟΣΟΧΗ" if h["status"] == "ΧΩΡΙΣ PKL" else "ΣΦΑΛΜΑ")
                out.append(f"<tr><td>{esc(h['name'])}</td><td class='{cls}'>{esc(h['status'])}</td>"
                           f"<td>{esc(h['detail'])}</td></tr>")
            out.append("</table>")
        notes = item.get("notes")
        if notes:
            out.append("<p><b>Σημειώσεις</b></p><table><tr><th>Χρόνος</th><th>Σημείωση</th></tr>")
            for n in notes:
                out.append(f"<tr><td>{fmt_time(n['t'], c.rate)}</td><td>{esc(n['text'])}</td></tr>")
            out.append("</table>")
        out.append("</div>")
    out.append(f"<p class='small'>{APP_NAME} v{APP_VERSION}</p></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(tr_html("\n".join(out)))  # στα αγγλικά μεταφράζεται το κείμενο (όχι οι τίτλοι ταινιών)
    log.info("Αναφορά: %s (%d ταινίες)", path, len(rows))


# ======================================================================== ΑΝΑΦΟΡΑ ΓΙΑ ΤΗΝ ΕΠΙΤΡΟΠΗ (PDF)
_COMMITTEE_CSS = """
@page{size:A4;margin:14mm}
body{font-family:Segoe UI,Arial,sans-serif;color:#111;margin:0;font-size:12.5px}
.page{page-break-after:always}.page:last-child{page-break-after:auto}
.cover h1{font-size:28px;margin:40mm 0 6px}.cover .sub{font-size:15px;color:#444}
.cover table{margin-top:14mm;width:100%;border-collapse:collapse}
.cover td,.cover th{border:1px solid #bbb;padding:5px 7px;text-align:left}.cover th{background:#eee}
h2{font-size:20px;margin:0 0 2px}.meta{color:#555;margin-bottom:8px}
.frames{display:flex;gap:6px;margin:8px 0}.frames img{width:32.6%;border:1px solid #999}
.kv{border-collapse:collapse;width:100%}.kv td{border-bottom:1px solid #ddd;padding:4px 6px;vertical-align:top}
.kv td:first-child{color:#555;width:32mm}
.st{display:inline-block;padding:2px 8px;border-radius:4px;color:#fff;font-weight:600;font-size:12px}
.OK{background:#137333}.ΠΡΟΣΟΧΗ{background:#b06000}.ΣΦΑΛΜΑ{background:#c5221f}
ul{margin:4px 0 0 18px;padding:0}li.err{color:#c5221f}li.warn{color:#b06000}li.info{color:#555}
.comments{border:1px solid #999;height:45mm;margin-top:10px;padding:6px;color:#777}
.small{color:#666;font-size:11px}
"""


def write_committee_report(items, path, frames=None, title="Ταινίες για την επιτροπή"):
    """
    HTML για εκτύπωση σε PDF: εξώφυλλο με πίνακα και μία σελίδα ανά ταινία με τρία καρέ, στοιχεία,
    ευρήματα, σημειώσεις και χώρο για τα σχόλια της επιτροπής. frames: {index: [png, ...]}.
    """
    esc = html.escape
    rows = [item_row(i) for i in items]
    out = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>{esc(title)}</title>"
           f"<style>{_COMMITTEE_CSS}</style></head><body>",
           f"<div class='page cover'><h1>{esc(title)}</h1><div class='sub'>{len(items)} ταινίες · "
           f"{datetime.datetime.now():%d/%m/%Y}</div><table><tr><th>#</th><th>Τίτλος</th><th>Είδος</th>"
           "<th>Διάρκεια</th><th>Κατάσταση</th></tr>"]  # (το εξώφυλλο κλείνει μετά τον πίνακα)
    for n, r in enumerate(rows, 1):
        out.append(f"<tr><td>{n}</td><td>{esc(r['Τίτλος'])}</td><td>{esc(r['Είδος'])}</td>"
                   f"<td>{esc(r['Διάρκεια'])}</td><td>{esc(r['Κατάσταση'])}</td></tr>")
    out.append("</table>")
    out.append(f"<p class='small'>{APP_NAME} v{APP_VERSION}</p></div>")
    for n, (item, r) in enumerate(zip(items, rows), 1):
        out.append(_committee_page(n, item, r, (frames or {}).get(n - 1) or [item.get("thumb")]))
    out.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(tr_html("\n".join(out)))


def _committee_page(n, item, r, pngs):
    esc = html.escape
    c = item["cpl"]
    out = [f"<div class='page'><h2>{n}. {esc(c.title)}</h2>"
           f"<div class='meta'><span class='st {r['Κατάσταση']}'>{esc(r['Κατάσταση'])}</span> &nbsp; "
           f"{esc(r['Είδος'])} · {esc(r['Διάρκεια'])}</div><div class='frames'>"]
    out += [f"<img src='data:image/png;base64,{base64.b64encode(p).decode()}'>" for p in pngs if p]
    out.append("</div><table class='kv'>")
    for k in ("Εικόνα", "3D", "fps", "Ήχος", "Υπότιτλοι", "Κρυπτογρ.", "Αρχεία", "Hash", "Παρατηρήσεις"):
        out.append(f"<tr><td>{k}</td><td>{esc(str(r[k]))}</td></tr>")
    out.append("</table>")
    issues = [(lv, t) for lv, t in (item.get("issues") or []) if lv in ("err", "warn")]
    if issues:
        out.append("<p><b>Ευρήματα ελέγχου</b></p><ul>" +
                   "".join(f"<li class='{lv}'>{esc(t)}</li>" for lv, t in issues[:12]) + "</ul>")
    notes = item.get("notes")
    if notes:
        out.append("<p><b>Σημειώσεις</b></p><ul>" +
                   "".join(f"<li>{fmt_time(x['t'])} — {esc(x['text'])}</li>" for x in notes[:10]) + "</ul>")
    out.append("<div class='comments'>Σχόλια επιτροπής:</div></div>")
    return "".join(out)
