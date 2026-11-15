"""Εκκίνηση του player σε ξεχωριστή διεργασία και παρακολούθηση του τέλους της."""
import os
import subprocess
import sys

from app.core.config import PROJECT_DIR, log


def player_command(target, half=False, fullscreen=False, extra_args=(), center=None):
    """
    Γραμμή εντολής για αναπαραγωγή: το ίδιο το .exe ή το reelcheck.py με --play.
    center: (x, y) κέντρο του κύριου παραθύρου, ώστε ο player να ανοίξει στην ίδια οθόνη.
    """
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.join(PROJECT_DIR, "reelcheck.py")]
    args += ["--play", target, "--lowres", "1" if half else "auto"]
    if fullscreen:
        args.append("--fullscreen")
    if center:
        args += ["--center", f"{int(center[0])},{int(center[1])}"]
    return args + list(extra_args)


def start_player(root, args, on_exit, interval=1000):
    """Ξεκινά τον player και καλεί on_exit(κωδικός) όταν κλείσει. Πετά OSError αν δεν ξεκινήσει."""
    log.info("Εκκίνηση αναπαραγωγής: %s", args[2:4] if len(args) > 3 else args)
    proc = subprocess.Popen(args)

    def watch():
        rc = proc.poll()
        if rc is None:
            root.after(interval, watch)
        else:
            on_exit(rc)

    root.after(interval, watch)
    return proc
