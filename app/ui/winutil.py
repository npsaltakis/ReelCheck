"""Βοηθητικά για τα Windows: θέμα συστήματος, σκούρα μπάρα τίτλου."""

def system_theme():
    """'light' ή 'dark' από τις ρυθμίσεις των Windows (Εφαρμογές: φωτεινό/σκούρο)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except (OSError, ImportError):
        return "light"


def dark_title_bar(tk_root, dark):
    """Σκούρα μπάρα τίτλου στα Windows 10/11 (αγνοείται αλλού)."""
    try:
        import ctypes
        tk_root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(tk_root.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (νέο / παλιό build)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        # ανανέωση πλαισίου ώστε να φανεί αμέσως
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    except Exception:
        pass


def point_on_screen(x, y):
    """True αν το σημείο είναι σε συνδεδεμένη οθόνη (π.χ. δεν αποσυνδέθηκε η 2η οθόνη)."""
    try:
        import ctypes
        from ctypes import wintypes
        return bool(ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 0))  # 0 = DEFAULTTONULL
    except Exception:
        return True
