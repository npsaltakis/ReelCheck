"""Σημειώσεις στον χρόνο ανά CPL."""
import hashlib
import os

from app.core.config import NOTES_DIR, log
from app.core.util import load_json, save_json


# ----------------------------------------------------------------- ΣΗΜΕΙΩΣΕΙΣ
def _notes_path(cpl):
    key = cpl.id or hashlib.sha1(os.path.abspath(cpl.path).lower().encode()).hexdigest()
    return os.path.join(NOTES_DIR, f"{key}.json")


def load_notes(cpl):
    data = load_json(_notes_path(cpl), {})
    return sorted(data.get("notes", []) if isinstance(data, dict) else [], key=lambda n: n["t"])


def save_notes(cpl, notes):
    try:
        save_json(_notes_path(cpl), {"title": cpl.title, "path": cpl.path,
                                     "notes": sorted(notes, key=lambda n: n["t"])}, indent=1)
    except OSError:
        log.exception("Δεν σώθηκαν οι σημειώσεις")
