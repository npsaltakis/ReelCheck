"""Κατάσταση ταινίας, γραμμές πίνακα, CSV και αναφορά HTML."""
import base64
import csv
import datetime
import html

from app.core.config import APP_NAME, APP_VERSION, log
from app.core.i18n import tr, tr_html
from app.core.hashing import hash_summary
from app.core.subtitles import track_summary
from app.core.util import fmt_time


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
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("sep=;\n")  # για να το ανοίγει σωστά το ελληνικό Excel
        header = list(rows[0].keys()) if rows else ["Τίτλος"]
        w = csv.writer(f, delimiter=";")
        w.writerow([tr(h) for h in header])
        w.writerows([[tr(str(r[h])) for h in header] for r in rows])
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
