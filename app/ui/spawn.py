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


def relaunch(folder=None):
    """Νέο ReelCheck (π.χ. μετά από αλλαγή γλώσσας). True αν ξεκίνησε."""
    args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable,
                                                                    os.path.join(PROJECT_DIR, "reelcheck.py")]
    if folder and os.path.isdir(folder):
        args.append(folder)
    try:
        subprocess.Popen(args)
        return True
    except OSError:
        log.exception("Επανεκκίνηση")
        return False


def relaunch_as_admin(extra_args=("--linux-disk",)):
    """
    Ανοίγει νέο ReelCheck με δικαιώματα διαχειριστή (τα Windows ρωτούν με UAC), π.χ. για ανάγνωση δίσκου Linux.
    Επιστρέφει True αν ξεκίνησε.
    """
    import ctypes
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, list(extra_args)
    else:
        exe, params = sys.executable, [os.path.join(PROJECT_DIR, "reelcheck.py"), *extra_args]
    line = subprocess.list2cmdline(params)
    ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, line, PROJECT_DIR, 1) > 32
    log.info("Εκκίνηση ως διαχειριστής: %s", "ναι" if ok else "ακυρώθηκε")
    return ok


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
