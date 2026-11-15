"""
Ρυθμίσεις του χρήστη με τις προεπιλογές τους, σε ένα σημείο. Αποθηκεύονται στο settings.json (app/core/config.py).
Χρήση: prefs.get("scan_silence_min"), prefs.set("theme", "dark").
"""
import os

from app.core.config import SHOTS_DIR, get_setting, set_setting

DEFAULTS = {
    # γενικά
    "language": "el",               # el | en
    "theme": "auto",                # auto | light | dark
    "update_auto": True,            # έλεγχος για νέα έκδοση μία φορά την ημέρα
    "open_last_folder": True,       # άνοιγμα στον τελευταίο φάκελο
    # φάκελοι
    "ingest_dest": "",              # φάκελος του φεστιβάλ (παραλαβές)
    "convert_out": "",              # έξοδος «Βίντεο → DCP»
    "transcode_out": "",            # έξοδος «Μετατροπή βίντεο» (κενό = δίπλα στο πρωτότυπο)
    "shots_dir": "",                # screenshots (κενό = Εικόνες\ReelCheck)
    # αναπαραγωγή
    "play_fullscreen": False,
    "play_half": False,
    "audio_device": "",             # όνομα συσκευής εξόδου (κενό = προεπιλογή των Windows)
    "start_volume": 100,            # %
    "subs_on": True,
    "meters": "auto",               # auto | on | off
    "jump_preroll": 3,              # δευτ. πριν από ένα εύρημα
    # έλεγχοι
    "scan_black_min": 5.0,          # δευτ. μαύρης εικόνας για εύρημα
    "scan_silence_min": 10.0,       # δευτ. σιωπής για εύρημα
    "scan_edge_start": 5.0,         # λεπτά στην αρχή που θεωρούνται τίτλοι
    "scan_edge_end": 10.0,          # λεπτά στο τέλος (τίτλοι τέλους)
    "sub_min_seconds": 0.7,
    "sub_max_seconds": 7.0,
    "sub_max_cps": 21,
    "sub_max_line": 42,
    "sub_max_lines": 2,
    "lufs_check": False,            # προειδοποίηση για ένταση εκτός στόχου
    "lufs_target": -24.0,
    "lufs_tolerance": 3.0,
}


def get(key):
    """Η τιμή της ρύθμισης, ή η προεπιλογή (με τον ίδιο τύπο: αριθμοί / λογικές τιμές)."""
    default = DEFAULTS.get(key)
    value = get_setting(key, default)
    if value is None:
        return default
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
    except (TypeError, ValueError):
        return default
    return value


def set(key, value):  # noqa: A001 (όνομα όπως το get)
    set_setting(key, value)


def shots_dir():
    return get("shots_dir") or SHOTS_DIR


def folder(key):
    """Φάκελος από τις ρυθμίσεις, αν υπάρχει ακόμα· αλλιώς κενό."""
    p = get(key)
    return p if p and os.path.isdir(p) else ""
