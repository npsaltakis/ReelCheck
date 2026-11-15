"""
Έλεγχος και διόρθωση υποτίτλων (.srt και υπότιτλοι DCP):
επικαλύψεις, πολύ σύντομοι / πολύ μεγάλοι, ταχύτητα ανάγνωσης (χαρακτήρες/δευτ.), μακριές γραμμές,
πάνω από 2 γραμμές, κενοί, εκτός διάρκειας. Μετατόπιση χρόνου, αλλαγή fps, αποθήκευση .srt.
"""
from app.core.util import fmt_time

from app.core import prefs


def limits():
    """Όρια από τις Ρυθμίσεις (προεπιλογές: συνήθεις κανόνες για υπότιτλους κινηματογράφου / φεστιβάλ)."""
    return {"min": prefs.get("sub_min_seconds"), "max": prefs.get("sub_max_seconds"), "cps": prefs.get("sub_max_cps"),
            "line": prefs.get("sub_max_line"), "lines": prefs.get("sub_max_lines")}

FPS_CHOICES = (23.976, 24, 25, 29.97, 30)


def entry_text(entry):
    """Οι γραμμές κειμένου ενός υποτίτλου (χωρίς εικόνες)."""
    return [it["text"] for it in entry[2] if it.get("type") == "text"]


def check_entries(entries, duration=None):
    """[(αριθμός, επίπεδο 'warn'|'info', χρόνος, κείμενο προβλήματος)] για κάθε υπότιτλο με πρόβλημα."""
    out = []
    lim = limits()
    ordered = sorted(enumerate(entries, 1), key=lambda x: x[1][0])
    for k, (n, (a, b, items)) in enumerate(ordered):
        lines = entry_text((a, b, items))
        has_image = any(it.get("type") == "image" for it in items)
        dur = b - a
        if b <= a:
            out.append((n, "warn", a, "τελειώνει πριν αρχίσει"))
            continue
        if not lines and not has_image:
            out.append((n, "warn", a, "κενός υπότιτλος"))
        if k + 1 < len(ordered) and ordered[k + 1][1][0] < b - 0.01:
            out.append((n, "warn", a, f"επικαλύπτεται με τον #{ordered[k + 1][0]}"))
        if duration and b > duration + 0.5:
            out.append((n, "warn", a, "μετά το τέλος της ταινίας"))
        if dur < lim["min"]:
            out.append((n, "warn", a, f"πολύ σύντομος ({dur:.2f} δευτ.)"))
        elif dur > lim["max"]:
            out.append((n, "info", a, f"πολύ μεγάλος ({dur:.1f} δευτ.)"))
        chars = sum(len(t) for t in lines)
        if lines and chars / dur > lim["cps"]:
            out.append((n, "warn", a, f"γρήγορος: {chars / dur:.0f} χαρακτήρες/δευτ. (όριο {lim['cps']})"))
        if len(lines) > lim["lines"]:
            out.append((n, "info", a, f"{len(lines)} γραμμές (συνήθως έως {lim['lines']})"))
        long_lines = [t for t in lines if len(t) > lim["line"]]
        if long_lines:
            out.append((n, "info", a, f"γραμμή {max(len(t) for t in long_lines)} χαρακτήρων (όριο {lim['line']})"))
    return out


def summary(problems):
    """Σύντομο κείμενο, π.χ. «3 επικαλύψεις, 5 γρήγοροι»."""
    keys = (("επικαλύπτεται", "επικαλύψεις"), ("γρήγορος", "γρήγοροι"), ("σύντομος", "πολύ σύντομοι"),
            ("μετά το τέλος", "μετά το τέλος"), ("κενός", "κενοί"), ("τελειώνει πριν", "λάθος χρόνοι"))
    parts = []
    for key, label in keys:
        n = sum(1 for p in problems if key in p[3])
        if n:
            parts.append(f"{n} {label}")
    return ", ".join(parts)


def shift(entries, seconds):
    """Όλοι οι υπότιτλοι μετακινούνται κατά seconds (αρνητικό = νωρίτερα). Όσοι πάνε πριν το 0 κόβονται."""
    out = []
    for a, b, items in entries:
        a, b = a + seconds, b + seconds
        if b <= 0:
            continue
        out.append((max(0.0, a), b, items))
    return out


def change_fps(entries, from_fps, to_fps):
    """
    Για βίντεο που άλλαξε ταχύτητα (π.χ. 25 → 24 fps στο DCP γίνεται ~4% πιο αργό):
    οι χρόνοι πολλαπλασιάζονται με from/to.
    """
    k = float(from_fps) / float(to_fps)
    return [(a * k, b * k, items) for a, b, items in entries]


def srt_time(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(entries):
    """Κείμενο .srt (μόνο οι υπότιτλοι κειμένου· τα πλάγια γράφονται με <i>)."""
    blocks = []
    n = 0
    for a, b, items in sorted(entries, key=lambda e: e[0]):
        lines = [f"<i>{it['text']}</i>" if it.get("italic") else it["text"] for it in items if it.get("type") == "text"]
        if not lines:
            continue
        n += 1
        blocks.append(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def write_srt(entries, path):
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(to_srt(entries))


def describe(n, level, t, text):
    return f"#{n} {fmt_time(t)}  {text}"
