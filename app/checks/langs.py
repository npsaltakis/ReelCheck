"""Κωδικοί γλωσσών και γλώσσες υποτίτλων ενός CPL."""
import re

from app.core.config import log
from app.core.subtitles import load_tracks


# ======================================================================== ΓΛΩΣΣΕΣ
_LANG_ALIASES = {
    "english": "en", "eng": "en", "greek": "el", "ell": "el", "gre": "el", "gr": "el", "french": "fr", "fra": "fr",
    "fre": "fr", "german": "de", "deu": "de", "ger": "de", "spanish": "es", "spa": "es", "italian": "it",
    "ita": "it", "polish": "pl", "pol": "pl", "portuguese": "pt", "por": "pt", "dutch": "nl", "nld": "nl",
    "dut": "nl", "russian": "ru", "rus": "ru", "turkish": "tr", "tur": "tr", "japanese": "ja", "jpn": "ja",
    "chinese": "zh", "chi": "zh", "zho": "zh", "korean": "ko", "kor": "ko", "swedish": "sv", "swe": "sv",
    "danish": "da", "dan": "da", "norwegian": "no", "nor": "no", "finnish": "fi", "fin": "fi",
    "czech": "cs", "ces": "cs", "cze": "cs", "hungarian": "hu", "hun": "hu", "romanian": "ro", "ron": "ro",
    "rum": "ro", "bulgarian": "bg", "bul": "bg", "serbian": "sr", "srp": "sr", "croatian": "hr", "hrv": "hr",
    "arabic": "ar", "ara": "ar", "hebrew": "he", "heb": "he", "persian": "fa", "fas": "fa", "per": "fa",
    "ukrainian": "uk", "ukr": "uk", "ua": "uk", "ge": "de", "sp": "es",
}


def norm_lang(s):
    """'en-US', 'English', 'ENG', 'EN' -> 'en'."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    base = re.split(r"[-_ ]", s)[0]
    return _LANG_ALIASES.get(base, base[:2])


def track_langs(cpl):
    cached = getattr(cpl, "_langs", None)
    if cached is not None:
        return cached
    out = set()
    try:
        for t in load_tracks(cpl):
            for lg in t.get("langs") or []:
                out.add(norm_lang(lg))
    except Exception:
        log.exception("Γλώσσες υποτίτλων %s", cpl.title)
    cpl._langs = out
    return out
