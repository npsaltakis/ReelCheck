"""Φωτεινό / σκούρο θέμα για τα παράθυρα tkinter."""
from app.core.config import get_setting, set_setting
from app.ui.winutil import dark_title_bar, system_theme

PALETTES = {
    "light": dict(bg="#f3f3f5", fg="#1d1d1f", field="#ffffff", alt="#e7e7ea", border="#c9c9ce",
                  sel="#0a64d8", selfg="#ffffff", accent="#e6a21e", muted="#6b6b70",
                  ok="#137333", warn="#b06000", err="#c5221f"),
    "dark": dict(bg="#1e1f24", fg="#e8e8ec", field="#26272d", alt="#2e3038", border="#3a3c45",
                 sel="#2f6fd6", selfg="#ffffff", accent="#f0b030", muted="#9a9aa2",
                 ok="#5bd17a", warn="#f0a640", err="#ff6b6b"),
}
MODES = ["auto", "light", "dark"]
MODE_LABELS = {"auto": "Θέμα: αυτόματο", "light": "Θέμα: φωτεινό", "dark": "Θέμα: σκούρο"}
STATUS_TAGS = ("OK", "ΠΡΟΣΟΧΗ", "ΣΦΑΛΜΑ")


class Theme:
    """Κρατά την επιλογή (αυτόματο / φωτεινό / σκούρο) και βάφει τα widgets του ttk."""

    def __init__(self, root, style, mode=None):
        self.root, self.style = root, style
        self.mode = mode or get_setting("theme", "auto")
        self._last_system = None

    @property
    def effective(self):
        return system_theme() if self.mode == "auto" else self.mode

    @property
    def palette(self):
        return PALETTES[self.effective]

    def cycle(self):
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        set_setting("theme", self.mode)

    def system_changed(self):
        """True αν άλλαξε το θέμα των Windows (για το «αυτόματο»)."""
        cur = system_theme()
        changed = self.mode == "auto" and cur != self._last_system
        self._last_system = cur
        return changed

    def apply_ttk(self):
        p, st = self.palette, self.style
        self.root.configure(bg=p["bg"])
        st.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                     bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                     troughcolor=p["alt"], focuscolor=p["sel"], insertcolor=p["fg"],
                     selectbackground=p["sel"], selectforeground=p["selfg"])
        st.configure("TButton", background=p["alt"], foreground=p["fg"], padding=(10, 4))
        st.map("TButton", background=[("disabled", p["bg"]), ("pressed", p["border"]), ("active", p["border"])],
               foreground=[("disabled", p["muted"])])
        st.configure("TCheckbutton", background=p["bg"], foreground=p["fg"], indicatorbackground=p["field"],
                     indicatorforeground=p["fg"])
        st.map("TCheckbutton", background=[("active", p["bg"])],
               indicatorbackground=[("selected", p["sel"]), ("active", p["alt"])])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"])
        st.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], background=p["alt"],
                     arrowcolor=p["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])],
               selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])])
        self.root.option_add("*TCombobox*Listbox.background", p["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
        st.configure("TRadiobutton", background=p["bg"], foreground=p["fg"], indicatorbackground=p["field"])
        st.map("TRadiobutton", background=[("active", p["bg"])], indicatorbackground=[("selected", p["sel"])])
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        st.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"],
                     rowheight=24, bordercolor=p["border"])
        st.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["selfg"])])
        st.configure("Treeview.Heading", background=p["alt"], foreground=p["fg"], relief="flat")
        st.map("Treeview.Heading", background=[("active", p["border"])])
        st.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["alt"],
                     bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])
        st.configure("Vertical.TScrollbar", background=p["alt"], troughcolor=p["bg"], arrowcolor=p["fg"])
        st.configure("TPanedwindow", background=p["bg"])
        dark_title_bar(self.root, self.effective == "dark")

    def style_text(self, widget):
        p = self.palette
        widget.configure(bg=p["field"], fg=p["fg"], insertbackground=p["fg"], selectbackground=p["sel"],
                         highlightthickness=1, highlightbackground=p["border"], highlightcolor=p["border"])

    def tag_tree(self, tree, extra=()):
        """Χρώματα για τις καταστάσεις OK / ΠΡΟΣΟΧΗ / ΣΦΑΛΜΑ (+ extra: [(tag, κλειδί παλέτας)])."""
        p = self.palette
        for tag, key in list(zip(STATUS_TAGS, ("ok", "warn", "err"))) + list(extra):
            tree.tag_configure(tag, foreground=p[key])

    def toplevel(self, win, parent):
        """Ρυθμίσεις για νέο παράθυρο (διάλογο): θέμα και θέση πάνω από το κύριο παράθυρο."""
        win.transient(parent)
        win.configure(bg=self.palette["bg"])
        dark_title_bar(win, self.effective == "dark")
        win.after_idle(lambda: center_on(win, parent))


def window_center(win):
    """Κέντρο του παραθύρου σε συντεταγμένες οθόνης (για να ανοίγουν τα άλλα παράθυρα στην ίδια οθόνη)."""
    win.update_idletasks()
    return win.winfo_rootx() + win.winfo_width() // 2, win.winfo_rooty() + win.winfo_height() // 2


def center_on(win, parent):
    """Τοποθετεί το win στο κέντρο του parent, άρα στην ίδια οθόνη (τα Windows αλλιώς το βάζουν στην κύρια)."""
    try:
        win.update_idletasks()
        w = win.winfo_width() if win.winfo_width() > 1 else win.winfo_reqwidth()
        h = win.winfo_height() if win.winfo_height() > 1 else win.winfo_reqheight()
        cx, cy = window_center(parent)
        win.geometry(f"+{cx - w // 2}+{max(cy - h // 2, parent.winfo_rooty())}")
    except Exception:
        pass
