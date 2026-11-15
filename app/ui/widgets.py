"""Κοινά κομμάτια παραθύρων: πίνακας με στήλες, λίστα επιλογής (Combobox) με κλειδιά, επιλογή φακέλου."""
import os
import tkinter as tk
from tkinter import filedialog, ttk


def make_tree(parent, columns, stretch=(), scroll=False, **kw):
    """
    Πίνακας (Treeview) με στήλες [(κλειδί, τίτλος, πλάτος)]. stretch: κλειδιά στηλών που μεγαλώνουν.
    scroll=True: με κάθετη μπάρα (το Treeview και η μπάρα μπαίνουν με pack στον parent).
    Χωρίς scroll ο πίνακας δεν τοποθετείται: ο καλών κάνει pack/grid.
    """
    tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", **kw)
    for key, title, width in columns:
        tree.heading(key, text=title)
        tree.column(key, width=width, anchor="w", stretch=key in stretch)
    if scroll:
        sb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
    return tree


def labeled_combo(parent, row, col, label, labels, value, width=24, on_change=None, colspan=1, **grid):
    """
    Ετικέτα + Combobox (μόνο ανάγνωση) σε grid. labels: {κλειδί: κείμενο}, value: το τρέχον κλειδί.
    Επιστρέφει (StringVar, labels)· το κλειδί της επιλογής δίνει το i18n_tk.label_key(var, labels).
    """
    pad = grid.pop("pady", 0)
    ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=pad)
    var = tk.StringVar(value=labels.get(value, list(labels.values())[0]))
    cb = ttk.Combobox(parent, textvariable=var, values=list(labels.values()), state="readonly", width=width)
    cb.grid(row=row, column=col + 1, columnspan=colspan, sticky="w", padx=grid.pop("padx", (0, 18)), pady=pad)
    if on_change:
        cb.bind("<<ComboboxSelected>>", lambda e: on_change())
    return var, labels


def ask_folder(parent, title=None, var=None):
    """Διάλογος φακέλου· γράφει τη διαδρομή στο var (αν δοθεί) και την επιστρέφει, ή None αν ακυρώθηκε."""
    initial = (var.get() if var is not None else "") or None
    d = filedialog.askdirectory(parent=parent, title=title, initialdir=initial)
    if not d:
        return None
    d = os.path.normpath(d)
    if var is not None:
        var.set(d)
    return d
