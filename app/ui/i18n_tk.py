"""
Σύνδεση της μετάφρασης (app/core/i18n.py) με το tkinter: ό,τι εμφανίζεται (κουμπιά, ετικέτες, μενού, στήλες,
τίτλοι παραθύρων, μηνύματα, διάλογοι αρχείων, κείμενο λεπτομερειών) περνά από την tr() τη στιγμή που
εμφανίζεται. Ο κώδικας και τα δεδομένα (ονόματα ταινιών, διαδρομές) δεν αλλάζουν.
"""
import tkinter as tk
from tkinter import commondialog, messagebox, simpledialog, ttk

from app.core.i18n import language, tr

_TEXT_KEYS = ("text", "label", "title")
_installed = {"done": False}


def _tr_options(options, widget_name=""):
    if not options:
        return options
    out = dict(options)
    for k in _TEXT_KEYS:
        if isinstance(out.get(k), str):
            out[k] = tr(out[k])
    if widget_name == "ttk::combobox" and isinstance(out.get("values"), (list, tuple)):
        out["values"] = [tr(v) for v in out["values"]]
    if isinstance(out.get("values"), (list, tuple)) and widget_name == "treeview":
        out["values"] = [tr(v) for v in out["values"]]
    return out


def install():
    """Μία φορά, πριν φτιαχτεί το πρώτο παράθυρο. Στα ελληνικά δεν κάνει τίποτα."""
    if _installed["done"] or language() == "el":
        return
    _installed["done"] = True

    base_init = tk.BaseWidget.__init__

    def init(self, master, widgetName, cnf={}, kw={}, extra=()):
        base_init(self, master, widgetName, _tr_options(cnf, widgetName), _tr_options(kw, widgetName), extra)
    tk.BaseWidget.__init__ = init

    configure = tk.Misc._configure

    def _configure(self, cmd, cnf, kw):
        name = getattr(self, "widgetName", "")
        return configure(self, cmd, _tr_options(cnf, name) if isinstance(cnf, dict) else cnf, _tr_options(kw, name))
    tk.Misc._configure = _configure

    heading = ttk.Treeview.heading
    ttk.Treeview.heading = lambda self, column, option=None, **kw: heading(self, column, option, **_tr_options(kw))
    insert = ttk.Treeview.insert
    ttk.Treeview.insert = lambda self, parent, index, iid=None, **kw: insert(
        self, parent, index, iid, **_tr_options(kw, "treeview"))
    item = ttk.Treeview.item
    ttk.Treeview.item = lambda self, it, option=None, **kw: item(self, it, option, **_tr_options(kw, "treeview"))

    nb_add = ttk.Notebook.add
    ttk.Notebook.add = lambda self, child, **kw: nb_add(self, child, **_tr_options(kw))
    nb_tab = ttk.Notebook.tab
    ttk.Notebook.tab = lambda self, tab_id, option=None, **kw: nb_tab(self, tab_id, option, **_tr_options(kw))

    menu_add = tk.Menu.add
    tk.Menu.add = lambda self, itemType, cnf={}, **kw: menu_add(self, itemType, _tr_options(cnf), **_tr_options(kw))

    wm_title = tk.Wm.wm_title

    def title(self, string=None):
        return wm_title(self, tr(string) if isinstance(string, str) else string)
    tk.Wm.wm_title = tk.Wm.title = title

    show = messagebox._show

    def _show(title=None, message=None, _icon=None, _type=None, **options):
        return show(tr(title), tr(message), _icon, _type, **_tr_options(options))
    messagebox._show = _show

    dialog_init = commondialog.Dialog.__init__

    def dlg_init(self, master=None, **options):
        o = _tr_options(options)
        if isinstance(o.get("initialfile"), str):
            o["initialfile"] = tr(o["initialfile"])
        if o.get("filetypes"):
            o["filetypes"] = [(tr(a), b) for a, b in o["filetypes"]]
        dialog_init(self, master, **o)
    commondialog.Dialog.__init__ = dlg_init

    askstring = simpledialog.askstring
    simpledialog.askstring = lambda title, prompt, **kw: askstring(tr(title), tr(prompt), **kw)

    var_init = tk.StringVar.__init__

    def init_var(self, master=None, value=None, name=None):
        var_init(self, master, tr(value) if isinstance(value, str) else value, name)
    tk.StringVar.__init__ = init_var

    var_set = tk.StringVar.set
    tk.StringVar.set = lambda self, value: var_set(self, tr(value) if isinstance(value, str) else value)

    text_insert = tk.Text.insert
    tk.Text.insert = lambda self, index, chars, *args: text_insert(self, index, tr(chars), *args)


def label_key(var, labels):
    """Κλειδί επιλογής από Combobox, είτε το κείμενο είναι ελληνικό είτε μεταφρασμένο."""
    shown = var.get()
    return next((k for k, v in labels.items() if v == shown or tr(v) == shown), list(labels)[0])
