"""Ρυθμίσεις, διαδρομές αρχείων και logging."""
import logging
import logging.handlers
import os
import platform
import shutil
import sys
import threading
import traceback

from app.core.util import load_json, save_json

APP_NAME = "ReelCheck"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


APP_VERSION = "1.10"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: εξωτερικά προγράμματα χωρίς παράθυρο κονσόλας
PLAYER_ENDED = 10  # κωδικός εξόδου του player όταν η ταινία τελειώνει μόνη της (--autoexit)


APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ReelCheck")


LOG_FILE = os.path.join(APP_DIR, "reelcheck.log")


NOTES_DIR = os.path.join(APP_DIR, "notes")


HASH_CACHE_FILE = os.path.join(APP_DIR, "hash_cache.json")


SHOTS_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ReelCheck")


VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm", ".ts", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg")


# Σειρά καναλιών DCP (SMPTE 428-12 / ISDCF)
CH_NAMES = ["L", "R", "C", "LFE", "Ls", "Rs", "HI", "VI", "9", "10", "BsL", "BsR", "Motion", "Sync", "SL", "16"]


log = logging.getLogger("dcp")


# ----------------------------------------------------------------- γενικά
def resource(name):
    """Αρχείο του φακέλου assets/, δίπλα στον κώδικα ή μέσα στο .exe (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", PROJECT_DIR)
    p = os.path.join(base, "assets", name)
    return p if os.path.isfile(p) else None


SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")


def get_setting(key, default=None):
    s = load_json(SETTINGS_FILE, {})
    return s.get(key, default) if isinstance(s, dict) else default


def set_setting(key, value):
    s = load_json(SETTINGS_FILE, {})
    s = s if isinstance(s, dict) else {}
    s[key] = value
    try:
        save_json(SETTINGS_FILE, s, indent=1)
    except OSError:
        log.exception("Αποθήκευση ρυθμίσεων")


def migrate_old_data():
    """Μεταφορά δεδομένων από το παλιό όνομα (DCP Player): σημειώσεις, cache hash, τελευταίος φάκελος."""
    old = os.path.join(os.path.dirname(APP_DIR), "DCPPlayer")
    if not os.path.isdir(old) or os.path.isdir(NOTES_DIR):
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        for name in ("notes", "hash_cache.json", "last_folder.txt"):
            src, dst = os.path.join(old, name), os.path.join(APP_DIR, name)
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.copytree(src, dst)
            elif os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except OSError:
        pass


def setup_logging(role):
    """Log στο %APPDATA%\\ReelCheck\\reelcheck.log (μέχρι 3 αρχεία x 2MB)."""
    migrate_old_data()
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(f"%(asctime)s [{role}:%(process)d] %(levelname)s %(message)s"))
    log.addHandler(h)
    if sys.stderr:
        log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)

    def hook(t, v, tb):
        log.critical("Μη αναμενόμενο σφάλμα:\n%s", "".join(traceback.format_exception(t, v, tb)))
        sys.__excepthook__(t, v, tb)

    sys.excepthook = hook
    threading.excepthook = lambda a: log.critical(
        "Σφάλμα σε thread %s:\n%s", a.thread.name if a.thread else "?",
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    log.info("---- έναρξη %s v%s | Python %s | %s | frozen=%s | args=%s", role, APP_VERSION,
             platform.python_version(), platform.platform(), getattr(sys, "frozen", False), sys.argv[1:])
