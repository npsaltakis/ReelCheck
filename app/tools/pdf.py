"""HTML → PDF με τον Edge (ή Chrome) σε headless λειτουργία: υπάρχει σε κάθε Windows 10/11, χωρίς βιβλιοθήκες."""
import os
import pathlib
import shutil
import subprocess
import tempfile

from app.core.config import NO_WINDOW, log

BROWSERS = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")


def find_browser():
    """Διαδρομή του msedge.exe / chrome.exe, ή None."""
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel in BROWSERS:
        for root in filter(None, roots):
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                return p
    return shutil.which("msedge") or shutil.which("chrome")


def html_to_pdf(html_path, pdf_path, timeout=180):
    """Τυπώνει το HTML σε PDF (A4, χωρίς κεφαλίδες του browser). Πετά RuntimeError αν δεν γίνεται."""
    exe = find_browser()
    if not exe:
        raise RuntimeError("Δεν βρέθηκε Microsoft Edge ή Chrome για τη δημιουργία PDF.")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    with tempfile.TemporaryDirectory(prefix="reelcheck_pdf_") as profile:  # ξεχωριστό προφίλ: δεν ενοχλεί ανοιχτό Edge
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
               f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", pathlib.Path(html_path).resolve().as_uri()]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
        log.error("PDF απέτυχε (%s): %s", proc.returncode, (proc.stderr or "")[-500:])
        raise RuntimeError("Η δημιουργία PDF απέτυχε.")
    return pdf_path
