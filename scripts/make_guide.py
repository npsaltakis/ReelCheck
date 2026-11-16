"""
Οδηγίες χρήσης σε PDF: python scripts/make_guide.py
docs/guide/guide.html → ReelCheck_Guide.pdf (ελληνικά), docs/guide/guide_en.html → ReelCheck_Guide_EN.pdf (αγγλικά),
με κοινό στυλ (guide.css) και τα screenshots του docs/screenshots. Ανοίγουν (στο GitHub) από το μενού Βοήθεια.
Νέα screenshots: python scripts/guide_shots.py el  και  python scripts/guide_shots.py en
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core.config import APP_VERSION  # noqa: E402
from app.tools.pdf import html_to_pdf  # noqa: E402

GUIDE_DIR = os.path.join(ROOT, "docs", "guide")
GUIDES = (("guide.html", "ReelCheck_Guide.pdf", "%d/%m/%Y"),
          ("guide_en.html", "ReelCheck_Guide_EN.pdf", "%d %B %Y"))


def build(source, output, date_fmt):
    with open(os.path.join(GUIDE_DIR, source), encoding="utf-8") as f:
        html = f.read().replace("{{VERSION}}", APP_VERSION).replace(
            "{{DATE}}", datetime.date.today().strftime(date_fmt))
    tmp = os.path.join(GUIDE_DIR, "_build.html")  # ίδιος φάκελος: εικόνες και guide.css βρίσκονται σχετικά
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        html_to_pdf(tmp, os.path.join(GUIDE_DIR, output))
    finally:
        os.remove(tmp)
    return os.path.join(GUIDE_DIR, output)


if __name__ == "__main__":
    for src, out, fmt in GUIDES:
        path = build(src, out, fmt)
        print(f"{path} ({os.path.getsize(path) // 1024} KB)")
