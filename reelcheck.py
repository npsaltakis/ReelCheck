#!/usr/bin/env python3
"""
ReelCheck: απλός έλεγχος DCP ταινιών (μη κρυπτογραφημένων).

  python reelcheck.py                    -> παράθυρο επιλογής
  python reelcheck.py "D:\\DCP\\Film"     -> ανοίγει κατευθείαν τον φάκελο
  python reelcheck.py --play CPL.xml     -> αναπαραγωγή CPL
  python reelcheck.py --play ταινία.mp4  -> αναπαραγωγή αρχείου βίντεο

Ο κώδικας είναι στο πακέτο app/ (core, checks, tools, player, ui).
"""
import argparse
import os
import sys

from app.core.config import APP_NAME, PLAYER_ENDED, VIDEO_EXT, setup_logging


def _require(ok):
    if not ok:
        raise RuntimeError("δεν είναι διαθέσιμο")


def selftest(out):
    """Για δοκιμές του .exe: ελέγχει ότι φορτώνουν όλα τα κομμάτια."""
    import xml.etree.ElementTree as ET
    res = []
    for name, fn in [
        ("xml", lambda: ET.fromstring("<a><b/></a>")),
        ("tkinter", lambda: __import__("tkinter").Tk().destroy()),
        ("av", lambda: __import__("av").codec.Codec("jpeg2000", "r")),
        ("pygame", lambda: __import__("pygame").font.init()),
        ("sounddevice", lambda: __import__("sounddevice").query_devices(kind="output")),
        ("numpy", lambda: __import__("numpy").zeros(3)),
        ("pillow", lambda: __import__("PIL.Image").Image.new("RGB", (2, 2))),
        ("hashlib", lambda: __import__("hashlib").sha1(b"x").digest()),
        ("app", lambda: __import__("app.ui.launcher") and __import__("app.player.window")),
        ("h264", lambda: _require(__import__("app.tools.transcode", fromlist=["x"]).encoder_works("libx264"))),
        ("aac", lambda: _require("aac" in __import__("av").codecs_available)),
    ]:
        try:
            fn()
            res.append(f"{name}: OK")
        except Exception as e:
            res.append(f"{name}: FAIL {type(e).__name__}: {e}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(res))


def parse_args():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--play", metavar="CPL_ή_ΒΙΝΤΕΟ")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--lowres", default="auto", help="auto | 0 | 1")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--autoexit", action="store_true", help="κλείσιμο στο τέλος (λίστα προβολής)")
    ap.add_argument("--center", help="x,y: κέντρο του παραθύρου (για να ανοίξει στην ίδια οθόνη)")
    ap.add_argument("--linux-disk", action="store_true", help="άνοιγμα με το παράθυρο «Δίσκος Linux»")
    return ap.parse_args()


def parse_center(text):
    try:
        x, y = (int(v) for v in (text or "").split(","))
        return x, y
    except ValueError:
        return None


def run_player(target, start=0.0, lowres="auto", fullscreen=False, autoexit=False, center=None):
    from app.player.window import play
    lr = None if lowres == "auto" else int(lowres)
    if play(target, start, lr, fullscreen, autoexit, parse_center(center)) == "ended":
        sys.exit(PLAYER_ENDED)


def main():
    if os.environ.get("DCP_SELFTEST"):
        selftest(os.environ["DCP_SELFTEST"])
        return
    a = parse_args()
    setup_logging("player" if a.play else "launcher")
    if a.play:
        run_player(a.play, a.start, a.lowres, a.fullscreen, a.autoexit, a.center)
        return
    f = a.folder
    if f and os.path.isfile(f):
        if f.lower().endswith(VIDEO_EXT) and not f.lower().endswith(".mxf"):
            run_player(f)  # σύρθηκε αρχείο βίντεο πάνω στο exe
            return
        f = os.path.dirname(f)  # σύρθηκε αρχείο του DCP (π.χ. CPL)
    from app.ui.launcher import launcher
    launcher(f, open_linux=a.linux_disk)


if __name__ == "__main__":
    main()
