"""
Παράθυρο «Δίσκος Linux (ext)»: δίσκοι DCP που τα Windows δεν διαβάζουν. Βρίσκει τα partitions Linux,
δείχνει τα DCP και τα αντιγράφει στον δίσκο του φεστιβάλ, ΜΟΝΟ ΜΕ ΑΝΑΓΝΩΣΗ από τον δίσκο Linux.
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core.config import APP_NAME, get_setting, log, set_setting
from app.core.util import fmt_size
from app.tools import extfs

WARNING = ("ΠΡΟΣΟΧΗ: όταν συνδέεις δίσκο Linux, τα Windows μπορεί να ρωτήσουν «Πρέπει να διαμορφώσετε τον δίσκο». "
           "Πάτα ΑΚΥΡΟ. Η διαμόρφωση σβήνει όλα τα DCP. Το ReelCheck μόνο διαβάζει, δεν γράφει τίποτα στον δίσκο.")


class LinuxDiskWindow:
    """on_copy(fs, handle, [διαδρομές DCP], φάκελος προορισμού, σάρωση) · relaunch_admin() ανοίγει ως διαχειριστής."""

    def __init__(self, parent, theme, on_copy, relaunch_admin):
        self.on_copy, self.relaunch_admin = on_copy, relaunch_admin
        self.parts, self.fs, self.handle, self.dcps = [], None, None, []
        self.win = w = tk.Toplevel(parent)
        w.title("Δίσκος Linux (ext2 / ext3 / ext4)")
        w.geometry("980x640")
        w.minsize(820, 520)
        theme.toplevel(w, parent)
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.theme = theme
        self._build()
        self.win.after(200, self.refresh_partitions)

    # ------------------------------------------------------------------ κατασκευή
    def _build(self):
        p = self.theme.palette
        tk.Label(self.win, text=WARNING, wraplength=940, justify="left", fg=p["err"], bg=p["bg"],
                 font=("Segoe UI", 10, "bold")).pack(fill="x", padx=10, pady=(10, 4))
        top = ttk.Frame(self.win, padding=(10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="1. Partitions Linux στους συνδεδεμένους δίσκους:").pack(side="left")
        ttk.Button(top, text="Ανανέωση", command=self.refresh_partitions).pack(side="right")
        ttk.Button(top, text="Αρχείο εικόνας δίσκου (.img)…", command=self.open_image).pack(side="right", padx=4)
        self.parts_tree = ttk.Treeview(self.win, columns=("disk", "part", "size", "model", "bus"), show="headings",
                                       height=4)
        for key, text, width in (("disk", "Δίσκος", 60), ("part", "Partition", 70), ("size", "Μέγεθος", 90),
                                 ("model", "Μοντέλο", 360), ("bus", "Σύνδεση", 80)):
            self.parts_tree.heading(key, text=text)
            self.parts_tree.column(key, width=width, anchor="w", stretch=key == "model")
        self.parts_tree.pack(fill="x", padx=10)
        row = ttk.Frame(self.win, padding=(10, 6))
        row.pack(fill="x")
        self.admin_var = tk.StringVar()
        ttk.Label(row, textvariable=self.admin_var).pack(side="left")
        self.admin_btn = ttk.Button(row, text="Άνοιγμα του ReelCheck ως διαχειριστής", command=self.relaunch_admin)
        ttk.Button(row, text="Ανάγνωση επιλεγμένου", command=self.open_selected).pack(side="right")

        ttk.Label(self.win, text="2. DCP στον δίσκο:", padding=(10, 6, 10, 2)).pack(anchor="w")
        f = ttk.Frame(self.win, padding=(10, 0))
        f.pack(fill="both", expand=True)
        self.dcp_tree = ttk.Treeview(f, columns=("path", "size", "files"), show="headings", selectmode="extended")
        for key, text, width in (("path", "Φάκελος", 640), ("size", "Μέγεθος", 100), ("files", "Αρχεία", 70)):
            self.dcp_tree.heading(key, text=text)
            self.dcp_tree.column(key, width=width, anchor="w", stretch=key == "path")
        self.dcp_tree.pack(fill="both", expand=True)
        self.fs_var = tk.StringVar(value="Διάλεξε partition και «Ανάγνωση επιλεγμένου».")
        ttk.Label(self.win, textvariable=self.fs_var, padding=(10, 4)).pack(anchor="w")

        bot = ttk.Frame(self.win, padding=(10, 0, 10, 10))
        bot.pack(fill="x")
        ttk.Label(bot, text="3. Αντιγραφή στον φάκελο:").pack(side="left")
        self.dest = tk.StringVar(value=get_setting("ingest_dest", "") or "")
        ttk.Entry(bot, textvariable=self.dest, width=45).pack(side="left", padx=6)
        ttk.Button(bot, text="…", width=3, command=self.choose_dest).pack(side="left")
        self.scan = tk.IntVar(value=1)
        ttk.Checkbutton(bot, text="και σάρωση", variable=self.scan).pack(side="left", padx=8)
        ttk.Button(bot, text="▶  Αντιγραφή και έλεγχος", command=self.start).pack(side="right")
        self._update_admin()

    def _update_admin(self):
        if extfs.is_admin():
            self.admin_var.set("Δικαιώματα διαχειριστή: ναι (ο δίσκος ανοίγει μόνο για ανάγνωση)")
            self.admin_btn.pack_forget()
        else:
            self.admin_var.set("Για ανάγνωση δίσκου τα Windows θέλουν δικαιώματα διαχειριστή.")
            self.admin_btn.pack(side="left", padx=8)

    # ------------------------------------------------------------------ partitions
    def refresh_partitions(self):
        self.win.configure(cursor="watch")
        self.win.update()
        try:
            self.parts = extfs.list_linux_partitions()
        except Exception:
            log.exception("Λίστα partitions")
            self.parts = []
        finally:
            self.win.configure(cursor="")
        self.parts_tree.delete(*self.parts_tree.get_children())
        for n, p in enumerate(self.parts):
            self.parts_tree.insert("", "end", iid=str(n), values=(p["disk"], p["partition"], fmt_size(p["size"] or 0),
                                                                  p.get("model") or "", p.get("bus") or ""))
        if self.parts:
            self.parts_tree.selection_set("0")
        else:
            self.fs_var.set("Δεν βρέθηκε partition Linux. Συνδέθηκε ο δίσκος; (Μπορείς και «Αρχείο εικόνας δίσκου».)")

    def _show(self, fs, handle, source):
        self.close_fs()
        self.fs, self.handle = fs, handle
        self.dcps = fs.find_dcps()
        self.dcp_tree.delete(*self.dcp_tree.get_children())
        for n, (path, size, files) in enumerate(self.dcps):
            self.dcp_tree.insert("", "end", iid=str(n), values=(path, fmt_size(size), files))
        self.dcp_tree.selection_set([str(n) for n in range(len(self.dcps))])
        total = sum(d[1] for d in self.dcps)
        self.fs_var.set(f"{source}: {fs.version}" + (f" «{fs.label}»" if fs.label else "") +
                        f" · {len(self.dcps)} DCP, {fmt_size(total)}")

    def open_selected(self):
        sel = self.parts_tree.selection()
        if not sel:
            return
        if not extfs.is_admin():
            if messagebox.askyesno(APP_NAME, "Για να διαβαστεί ο δίσκος χρειάζονται δικαιώματα διαχειριστή.\n\n"
                                             "Να ανοίξει το ReelCheck ως διαχειριστής;", parent=self.win):
                self.relaunch_admin()
            return
        p = self.parts[int(sel[0])]
        try:
            fs, handle = extfs.open_partition(p["disk"], p["offset"])
        except Exception as e:
            log.exception("Άνοιγμα δίσκου Linux")
            messagebox.showerror(APP_NAME, f"Δεν διαβάστηκε ο δίσκος:\n{e}", parent=self.win)
            return
        self._show(fs, handle, f"Δίσκος {p['disk']}, partition {p['partition']}")

    def open_image(self):
        path = filedialog.askopenfilename(parent=self.win, title="Εικόνα δίσκου", filetypes=[
            ("Εικόνες δίσκου", "*.img *.dd *.raw *.bin *.iso"), ("Όλα", "*.*")])
        if not path:
            return
        handle = open(path, "rb", buffering=0)
        try:
            reader = extfs.AlignedReader(handle)
            offsets = extfs.ext_offsets(reader)
            if not offsets:
                raise extfs.ExtError("Δεν βρέθηκε σύστημα αρχείων ext2/ext3/ext4 στο αρχείο")
            fs = extfs.ExtFS(reader, offsets[0])
        except Exception as e:
            handle.close()
            messagebox.showerror(APP_NAME, str(e), parent=self.win)
            return
        self._show(fs, handle, os.path.basename(path))

    # ------------------------------------------------------------------ αντιγραφή
    def choose_dest(self):
        d = filedialog.askdirectory(parent=self.win, title="Φάκελος του φεστιβάλ")
        if d:
            self.dest.set(os.path.normpath(d))

    def start(self):
        if not self.fs:
            messagebox.showinfo(APP_NAME, "Διάβασε πρώτα έναν δίσκο.", parent=self.win)
            return
        paths = [self.dcps[int(i)][0] for i in self.dcp_tree.selection()]
        if not paths:
            return
        dest = self.dest.get().strip()
        if not dest:
            self.choose_dest()
            dest = self.dest.get().strip()
            if not dest:
                return
        set_setting("ingest_dest", dest)
        fs, handle = self.fs, self.handle
        self.fs = self.handle = None  # το κλείνει η εργασία αντιγραφής όταν τελειώσει
        self.on_copy(fs, handle, paths, os.path.normpath(dest), bool(self.scan.get()))
        self.win.destroy()

    def close_fs(self):
        if self.handle:
            try:
                self.handle.close()
            except OSError:
                pass
        self.fs = self.handle = None

    def close(self):
        self.close_fs()
        self.win.destroy()
