"""Σημειώσεις στον χρόνο ανά CPL."""
import hashlib
import json
import os

from app.core.config import NOTES_DIR, log


# ----------------------------------------------------------------- ΣΗΜΕΙΩΣΕΙΣ
def _notes_path(cpl):
    key = cpl.id or hashlib.sha1(os.path.abspath(cpl.path).lower().encode()).hexdigest()
    return os.path.join(NOTES_DIR, f"{key}.json")


def load_notes(cpl):
    try:
        with open(_notes_path(cpl), encoding="utf-8") as f:
            return sorted(json.load(f).get("notes", []), key=lambda n: n["t"])
    except (OSError, ValueError):
        return []


def save_notes(cpl, notes):
    try:
        os.makedirs(NOTES_DIR, exist_ok=True)
        with open(_notes_path(cpl), "w", encoding="utf-8") as f:
            json.dump({"title": cpl.title, "path": cpl.path,
                       "notes": sorted(notes, key=lambda n: n["t"])}, f, ensure_ascii=False, indent=1)
    except OSError:
        log.exception("Δεν σώθηκαν οι σημειώσεις")
