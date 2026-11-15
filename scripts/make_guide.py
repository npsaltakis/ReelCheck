"""
Οδηγίες χρήσης σε PDF: python scripts/make_guide.py
Από το docs/guide/guide.html (με τα screenshots του docs/screenshots) → docs/guide/ReelCheck_Guide.pdf,
που ανοίγει (στο GitHub) από το μενού Βοήθεια. Νέα screenshots: python scripts/guide_shots.py
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core.config import APP_VERSION  # noqa: E402
from app.tools.pdf import html_to_pdf  # noqa: E402

SOURCE = os.path.join(ROOT, "docs", "guide", "guide.html")
OUTPUT = os.path.join(ROOT, "docs", "guide", "ReelCheck_Guide.pdf")


def build(output=OUTPUT):
    with open(SOURCE, encoding="utf-8") as f:
        html = f.read().replace("{{VERSION}}", APP_VERSION).replace(
            "{{DATE}}", datetime.date.today().strftime("%d/%m/%Y"))
    tmp = os.path.join(os.path.dirname(SOURCE), "_build.html")  # ίδιος φάκελος: οι εικόνες βρίσκονται σχετικά
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        html_to_pdf(tmp, output)
    finally:
        os.remove(tmp)
    return output


if __name__ == "__main__":
    out = build(sys.argv[1] if len(sys.argv) > 1 else OUTPUT)
    print(f"{out} ({os.path.getsize(out) // 1024} KB)")
