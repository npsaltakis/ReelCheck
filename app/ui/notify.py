"""
Ειδοποιήσεις των Windows (κάτω δεξιά / κέντρο ειδοποιήσεων) όταν τελειώνει μια μεγάλη εργασία.
Με Shell_NotifyIcon από το ίδιο το πρόγραμμα: χωρίς επιπλέον βιβλιοθήκες, με το εικονίδιο του ReelCheck.
"""
import ctypes
import sys
from ctypes import wintypes

from app.core.config import APP_NAME, log, resource

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR, NIIF_USER, NIIF_LARGE_ICON = 0x1, 0x2, 0x3, 0x4, 0x20
LR_LOADFROMFILE, IMAGE_ICON = 0x10, 1
HIDE_AFTER_MS = 20000  # το εικονίδιο της γραμμής εργασιών φεύγει μετά (η ειδοποίηση μένει στο κέντρο ειδοποιήσεων)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class Notifier:
    """notify(τίτλος, κείμενο, level) από το main thread του tkinter."""

    def __init__(self, root):
        self.root = root
        self.shown = False
        self.hide_job = None
        self.icons = {}

    def _hwnd(self):
        return int(self.root.wm_frame(), 16)

    def _load_icon(self, size):
        """Εικονίδιο του ReelCheck: 16 px για τη γραμμή εργασιών, 64 px για την ειδοποίηση (NIIF_LARGE_ICON)."""
        if size not in self.icons:
            path = resource("icon.ico")
            self.icons[size] = ctypes.windll.user32.LoadImageW(None, path, IMAGE_ICON, size, size,
                                                               LR_LOADFROMFILE) if path else 0
        return self.icons[size]

    def _data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd()
        nid.uID = 1
        return nid

    def notify(self, title, message, level="info"):
        """True αν εμφανίστηκε. Δεν πετά ποτέ σφάλμα (η ειδοποίηση είναι προαιρετική)."""
        if sys.platform != "win32":
            return False
        try:
            nid = self._data()
            nid.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
            nid.hIcon, nid.hBalloonIcon = self._load_icon(16), self._load_icon(64)
            nid.szTip = APP_NAME
            nid.szInfoTitle = title[:63]
            nid.szInfo = message[:255]
            flags = {"warn": NIIF_WARNING, "err": NIIF_ERROR}.get(level)
            nid.dwInfoFlags = flags if flags else (NIIF_USER | NIIF_LARGE_ICON if nid.hBalloonIcon else NIIF_INFO)
            shell = ctypes.windll.shell32
            ok = shell.Shell_NotifyIconW(NIM_MODIFY if self.shown else NIM_ADD, ctypes.byref(nid))
            if not ok and self.shown:  # το εικονίδιο χάθηκε (π.χ. επανεκκίνηση του Explorer)
                ok = shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self.shown = bool(ok)
            if self.hide_job:
                self.root.after_cancel(self.hide_job)
            self.hide_job = self.root.after(HIDE_AFTER_MS, self.hide)
            return bool(ok)
        except Exception:  # ειδοποίηση: ποτέ αιτία να σταματήσει το πρόγραμμα
            log.exception("Ειδοποίηση Windows")
            return False

    def hide(self):
        self.hide_job = None
        if self.shown:
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            except Exception:
                log.exception("Απόκρυψη ειδοποίησης")
            self.shown = False
