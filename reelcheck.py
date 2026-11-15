#!/usr/bin/env python3
"""
ReelCheck: απλός έλεγχος DCP ταινιών (μη κρυπτογραφημένων).

  python reelcheck.py                    -> παράθυρο επιλογής
  python reelcheck.py "D:\\DCP\\Film"     -> ανοίγει κατευθείαν τον φάκελο
  python reelcheck.py --play CPL.xml     -> αναπαραγωγή CPL
  python reelcheck.py --play ταινία.mp4  -> αναπαραγωγή αρχείου βίντεο
"""
import argparse
import os
import queue
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

import dcp_core as core
from dcp_core import log


# ======================================================================== ΠΑΡΑΘΥΡΟ ΕΠΙΛΟΓΗΣ
def launcher(initial=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{core.APP_NAME} v{core.APP_VERSION}")
    root.geometry("1150x680")
    root.minsize(900, 520)
    if core.resource("icon.ico"):
        try:
            root.iconbitmap(core.resource("icon.ico"))
        except Exception:
            pass

    items = []                 # [{"cpl", "info", "hash", "notes", "thumb"}]
    events = queue.Queue()     # μηνύματα από threads προς το GUI
    busy = {"job": None, "cancel": None}
    last_file = os.path.join(core.APP_DIR, "last_folder.txt")

    def remember(folder):
        try:
            os.makedirs(core.APP_DIR, exist_ok=True)
            with open(last_file, "w", encoding="utf-8") as f:
                f.write(folder)
        except OSError:
            pass

    def last_folder():
        try:
            with open(last_file, encoding="utf-8") as f:
                p = f.read().strip()
            return p if os.path.isdir(p) else None
        except OSError:
            return None

    # --- πάνω μπάρα
    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    path_var = tk.StringVar()
    ttk.Button(top, text="Άνοιγμα φακέλου DCP…", command=lambda: choose()).pack(side="left")
    ttk.Button(top, text="Άνοιγμα βίντεο…", command=lambda: open_video()).pack(side="left", padx=(6, 0))
    entry = ttk.Entry(top, textvariable=path_var)
    entry.pack(side="left", fill="x", expand=True, padx=8)
    ttk.Button(top, text="Φόρτωση", command=lambda: load_typed()).pack(side="left")
    theme_btn = ttk.Button(top, width=12, command=lambda: cycle_theme())
    theme_btn.pack(side="left", padx=(8, 0))
    entry.bind("<Return>", lambda e: load_typed())

    # --- πίνακας ταινιών
    mid = ttk.PanedWindow(root, orient="vertical")
    mid.pack(fill="both", expand=True, padx=8)
    cols = ("Κατάσταση", "Τίτλος", "Διάρκεια", "Εικόνα", "Ήχος", "Υπότιτλοι", "Αρχεία", "Hash", "Σημειώσεις")
    widths = (80, 300, 85, 85, 85, 160, 80, 100, 80)
    tf = ttk.Frame(mid)
    tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended", height=10)
    for c, w in zip(cols, widths):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="w", stretch=c in ("Τίτλος", "Υπότιτλοι"))
    sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    mid.add(tf, weight=3)
    info = tk.Text(mid, height=12, font=("Consolas", 10), wrap="word", relief="flat", padx=8, pady=6)
    mid.add(info, weight=2)

    # --- θέμα light / dark
    style = ttk.Style(root)
    style.theme_use("clam")
    theme = {"mode": core.get_setting("theme", "auto")}
    PALETTES = {
        "light": dict(bg="#f3f3f5", fg="#1d1d1f", field="#ffffff", alt="#e7e7ea", border="#c9c9ce",
                      sel="#0a64d8", selfg="#ffffff", accent="#e6a21e", muted="#6b6b70",
                      ok="#137333", warn="#b06000", err="#c5221f"),
        "dark": dict(bg="#1e1f24", fg="#e8e8ec", field="#26272d", alt="#2e3038", border="#3a3c45",
                     sel="#2f6fd6", selfg="#ffffff", accent="#f0b030", muted="#9a9aa2",
                     ok="#5bd17a", warn="#f0a640", err="#ff6b6b"),
    }

    def effective_mode():
        return core.system_theme() if theme["mode"] == "auto" else theme["mode"]

    def apply_theme():
        m = effective_mode()
        p = PALETTES[m]
        root.configure(bg=p["bg"])
        style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                        bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                        troughcolor=p["alt"], focuscolor=p["sel"], insertcolor=p["fg"],
                        selectbackground=p["sel"], selectforeground=p["selfg"])
        style.configure("TButton", background=p["alt"], foreground=p["fg"], padding=(10, 4))
        style.map("TButton", background=[("disabled", p["bg"]), ("pressed", p["border"]), ("active", p["border"])],
                  foreground=[("disabled", p["muted"])])
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"], indicatorbackground=p["field"],
                        indicatorforeground=p["fg"])
        style.map("TCheckbutton", background=[("active", p["bg"])],
                  indicatorbackground=[("selected", p["sel"]), ("active", p["alt"])])
        style.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"])
        style.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"],
                        rowheight=24, bordercolor=p["border"])
        style.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["selfg"])])
        style.configure("Treeview.Heading", background=p["alt"], foreground=p["fg"], relief="flat")
        style.map("Treeview.Heading", background=[("active", p["border"])])
        style.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["alt"],
                        bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])
        style.configure("Vertical.TScrollbar", background=p["alt"], troughcolor=p["bg"], arrowcolor=p["fg"])
        style.configure("TPanedwindow", background=p["bg"])
        info.configure(bg=p["field"], fg=p["fg"], insertbackground=p["fg"], selectbackground=p["sel"],
                       highlightthickness=1, highlightbackground=p["border"], highlightcolor=p["border"])
        tree.tag_configure("OK", foreground=p["ok"])
        tree.tag_configure("ΠΡΟΣΟΧΗ", foreground=p["warn"])
        tree.tag_configure("ΣΦΑΛΜΑ", foreground=p["err"])
        theme_btn.configure(text={"auto": "Θέμα: αυτόματο", "light": "Θέμα: φωτεινό",
                                  "dark": "Θέμα: σκούρο"}[theme["mode"]])
        theme_btn.configure(width=15)
        core.dark_title_bar(root, m == "dark")

    def cycle_theme():
        order = ["auto", "light", "dark"]
        theme["mode"] = order[(order.index(theme["mode"]) + 1) % 3]
        core.set_setting("theme", theme["mode"])
        apply_theme()

    def follow_system():  # στο «αυτόματο» ακολουθεί αλλαγές των Windows
        if theme["mode"] == "auto" and core.system_theme() != follow_system.last:
            apply_theme()
        follow_system.last = core.system_theme()
        root.after(3000, follow_system)

    follow_system.last = None

    # --- κάτω μπάρα
    prog_frame = ttk.Frame(root, padding=(8, 4))
    prog_frame.pack(fill="x")
    prog = ttk.Progressbar(prog_frame, mode="determinate", maximum=1000)
    prog.pack(side="left", fill="x", expand=True)
    status_var = tk.StringVar(value="Έτοιμο")
    ttk.Label(prog_frame, textvariable=status_var, width=60).pack(side="left", padx=8)
    cancel_btn = ttk.Button(prog_frame, text="Ακύρωση", command=lambda: busy["cancel"] and busy["cancel"].set())
    cancel_btn.pack(side="left")
    cancel_btn.state(["disabled"])

    bot = ttk.Frame(root, padding=8)
    bot.pack(fill="x")
    half = tk.IntVar(value=0)
    fs = tk.IntVar(value=0)
    ttk.Button(bot, text="Έλεγχος hash επιλεγμένων", command=lambda: run_hash(selected())).pack(side="left")
    ttk.Button(bot, text="Έλεγχος hash όλων", command=lambda: run_hash(list(range(len(items))))).pack(
        side="left", padx=4)
    ttk.Button(bot, text="Αναφορά HTML", command=lambda: run_report()).pack(side="left", padx=4)
    ttk.Button(bot, text="Εξαγωγή CSV", command=lambda: export_csv()).pack(side="left", padx=4)
    ttk.Button(bot, text="Log", command=lambda: open_log()).pack(side="left", padx=4)
    play_btn = ttk.Button(bot, text="▶  Αναπαραγωγή", command=lambda: do_play())
    play_btn.pack(side="right")
    ttk.Checkbutton(bot, text="Πλήρης οθόνη", variable=fs).pack(side="right", padx=10)
    ttk.Checkbutton(bot, text="Μισή ανάλυση", variable=half).pack(side="right")

    # --- βοηθητικά
    def selected():
        return [int(i) for i in tree.selection()]

    def refresh_row(i):
        r = core.item_row(items[i])
        vals = [r["Κατάσταση"], ("🔒 " if items[i]["cpl"].encrypted else "") + r["Τίτλος"], r["Διάρκεια"],
                r["Εικόνα"], r["Ήχος"], r["Υπότιτλοι"], r["Αρχεία"], r["Hash"], r["Σημειώσεις"] or ""]
        if tree.exists(str(i)):
            tree.item(str(i), values=vals, tags=(r["Κατάσταση"],))
        else:
            tree.insert("", "end", iid=str(i), values=vals, tags=(r["Κατάσταση"],))

    def start_job(name, fn):
        """Εκτέλεση σε thread. fn(progress, cancel) -> καλεί progress(κλάσμα, κείμενο)."""
        if busy["job"]:
            messagebox.showinfo(core.APP_NAME, "Τρέχει ήδη εργασία. Περίμενε ή πάτα Ακύρωση.")
            return
        cancel = threading.Event()
        busy["job"], busy["cancel"] = name, cancel
        cancel_btn.state(["!disabled"])

        def progress(frac, msg):
            events.put(("progress", frac, msg))

        def run():
            try:
                fn(progress, cancel)
                events.put(("done", name, None))
            except core.Cancelled:
                events.put(("done", name, "ακυρώθηκε"))
            except Exception as e:
                log.exception("Σφάλμα στην εργασία %s", name)
                events.put(("done", name, f"σφάλμα: {e}"))

        threading.Thread(target=run, daemon=True, name=name).start()

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                if ev[0] == "progress":
                    prog["value"] = int(ev[1] * 1000)
                    status_var.set(ev[2])
                elif ev[0] == "row":
                    refresh_row(ev[1])
                    if ev[1] in selected():
                        show()
                elif ev[0] == "done":
                    busy["job"], busy["cancel"] = None, None
                    cancel_btn.state(["disabled"])
                    prog["value"] = 0 if ev[2] else 1000
                    status_var.set(f"{ev[1]}: {ev[2] or 'ολοκληρώθηκε'}")
                elif ev[0] == "call":
                    ev[1]()
        except queue.Empty:
            pass
        root.after(100, poll)

    # --- φόρτωση
    def load(folder):
        if busy["job"]:
            messagebox.showinfo(core.APP_NAME, "Τρέχει εργασία. Περίμενε ή πάτα Ακύρωση.")
            return
        folder = os.path.normpath(folder)
        log.info("Φόρτωση φακέλου: %s", folder)
        path_var.set(folder)
        remember(folder)
        items.clear()
        tree.delete(*tree.get_children())
        info.delete("1.0", "end")
        dcps = core.find_dcps(folder) or [folder]
        for d in dcps:
            try:
                for c in core.load_dcp(d):
                    items.append({"cpl": c, "info": None, "hash": None, "notes": core.load_notes(c), "thumb": None})
            except Exception as e:
                log.exception("Σφάλμα ανάγνωσης DCP %s", d)
                messagebox.showerror("Σφάλμα", f"{d}\n{e}\n\nΛεπτομέρειες στο Log.")
        for i, it in enumerate(items):
            c = it["cpl"]
            log.info("  CPL: %s | %d reels | %s | κρυπτογρ.=%s | λείπουν=%s", c.title, len(c.reels),
                     core.fmt_time(c.seconds), c.encrypted, c.missing or "τίποτα")
            refresh_row(i)
        if not items:
            log.warning("  Δεν βρέθηκε CPL")
            info.insert("end", "Δεν βρέθηκε CPL σε αυτόν τον φάκελο.")
            status_var.set("Δεν βρέθηκε DCP")
            return
        tree.selection_set("0")
        status_var.set(f"{len(items)} CPL σε {len(dcps)} DCP")

        def probe_all(progress, cancel):  # ανάλυση/ήχος στο παρασκήνιο
            for i, it in enumerate(list(items)):
                if cancel.is_set():
                    raise core.Cancelled()
                progress(i / len(items), f"Ανάγνωση {i + 1}/{len(items)}: {it['cpl'].title[:40]}")
                try:
                    it["info"] = core.probe(it["cpl"])
                except Exception as e:
                    it["info"] = {"pic_err": str(e)}
                events.put(("row", i))

        start_job("Ανάγνωση", probe_all)

    def load_typed():
        p = path_var.get().strip().strip('"')
        if os.path.isdir(p):
            load(p)
        elif os.path.isfile(p) and p.lower().endswith(core.VIDEO_EXT):
            spawn_player(p, os.path.basename(p))
        else:
            messagebox.showerror(core.APP_NAME, f"Δεν βρέθηκε ο φάκελος:\n{p}")

    def choose():
        d = filedialog.askdirectory(title="Φάκελος DCP (ή φάκελος με πολλά DCP)", initialdir=last_folder() or "")
        if d:
            load(d)

    def open_video():
        f = filedialog.askopenfilename(title="Αρχείο βίντεο", filetypes=[
            ("Βίντεο", " ".join("*" + e for e in core.VIDEO_EXT)), ("Όλα", "*.*")])
        if f:
            spawn_player(f, os.path.basename(f))

    # --- λεπτομέρειες
    def show(_e=None):
        sel = selected()
        if not sel:
            return
        it = items[sel[0]]
        c, inf = it["cpl"], it["info"] or {}
        r = core.item_row(it)
        st, why = core.status_of(c, inf, it["hash"], it["notes"])
        subs, sub_notes = core.track_summary(c)
        lines = [
            f"Κατάσταση:   {st}" + (f"  ({'; '.join(why)})" if why else ""),
            f"Τίτλος:      {c.title}",
            f"Είδος:       {c.kind}      Issuer: {c.issuer}",
            f"Διάρκεια:    {r['Διάρκεια']}   ({len(c.reels)} reels, {r['fps']} fps)",
            f"Εικόνα:      {r['Εικόνα']} {inf.get('pix', '')}" + ("   3D (E = αλλαγή ματιού)" if r["3D"] == "ναι" else ""),
            f"Ήχος:        {r['Ήχος']}",
            f"Υπότιτλοι:   {subs}" + (f"  - {'; '.join(sub_notes)}" if sub_notes else ""),
            f"Κρυπτογρ.:   {'ΝΑΙ - χρειάζεται KDM, δεν παίζει' if c.encrypted else 'όχι'}",
            f"Hash:        {r['Hash']}",
        ]
        for k in ("pic_err", "snd_err"):
            if inf.get(k):
                lines.append(f"Πρόβλημα:    {inf[k]}")
        if c.missing:
            lines.append("\nΛΕΙΠΟΥΝ ΑΡΧΕΙΑ:")
            lines += [f"  - {m}" for m in c.missing]
        if it["hash"]:
            bad = [h for h in it["hash"] if h["status"] != "OK"]
            if bad:
                lines.append("\nΑΠΟΤΕΛΕΣΜΑ HASH:")
                lines += [f"  {h['status']:<14} {h['name']}  ({h['detail']})" for h in bad]
        if it["notes"]:
            lines.append("\nΣΗΜΕΙΩΣΕΙΣ:")
            lines += [f"  {core.fmt_time(n['t'], c.rate)}  {n['text']}" for n in it["notes"]]
        lines.append(f"\nCPL: {c.path}")
        info.delete("1.0", "end")
        info.insert("end", "\n".join(lines))
        play_btn.state(["disabled"] if c.encrypted else ["!disabled"])

    # --- εργασίες
    def run_hash(idx):
        if not idx:
            messagebox.showinfo(core.APP_NAME, "Διάλεξε πρώτα ταινίες από τη λίστα.")
            return

        def job(progress, cancel):
            for n, i in enumerate(idx):
                c = items[i]["cpl"]

                def p(done, total, name, n=n):
                    frac = (n + (done / total if total else 1)) / len(idx)
                    progress(frac, f"Hash {n + 1}/{len(idx)}: {name[:40]} {done * 100 // max(1, total)}%")

                items[i]["hash"] = core.verify_cpl(c, p, cancel)
                events.put(("row", i))

        start_job("Έλεγχος hash", job)

    def run_report():
        if not items:
            return
        idx = selected() if len(selected()) > 1 else list(range(len(items)))
        path = filedialog.asksaveasfilename(title="Αποθήκευση αναφοράς", defaultextension=".html",
                                            initialfile="Αναφορά_DCP.html", filetypes=[("HTML", "*.html")])
        if not path:
            return

        def job(progress, cancel):
            for n, i in enumerate(idx):
                if cancel.is_set():
                    raise core.Cancelled()
                it = items[i]
                progress(n / len(idx), f"Αναφορά: καρέ {n + 1}/{len(idx)}")
                if it["info"] is None:
                    it["info"] = core.probe(it["cpl"])
                if it["thumb"] is None and not it["cpl"].encrypted:
                    try:
                        it["thumb"] = core.thumbnail(it["cpl"])
                    except Exception:
                        log.exception("Δεν βγήκε καρέ για %s", it["cpl"].title)
                it["notes"] = core.load_notes(it["cpl"])
            core.write_report([items[i] for i in idx], path)
            events.put(("call", lambda: os.startfile(path)))

        start_job("Αναφορά", job)

    def export_csv():
        if not items:
            return
        path = filedialog.asksaveasfilename(title="Εξαγωγή CSV", defaultextension=".csv",
                                            initialfile="Έλεγχος_DCP.csv", filetypes=[("CSV (Excel)", "*.csv")])
        if path:
            core.write_csv(items, path)
            status_var.set(f"CSV: {path}")
            os.startfile(path)

    def open_log():
        if os.path.isfile(core.LOG_FILE):
            os.startfile(core.LOG_FILE)
        else:
            messagebox.showinfo(core.APP_NAME, "Δεν υπάρχει ακόμα log.")

    def spawn_player(target, title, item_index=None):
        if getattr(sys, "frozen", False):  # τρέχει ως .exe
            args = [sys.executable]
        else:
            args = [sys.executable, os.path.abspath(__file__)]
        args += ["--play", target, "--lowres", "1" if half.get() else "auto"]
        if fs.get():
            args.append("--fullscreen")
        log.info("Εκκίνηση αναπαραγωγής: %s", title)
        try:
            proc = subprocess.Popen(args)
        except Exception as e:
            log.exception("Δεν ξεκίνησε η αναπαραγωγή")
            messagebox.showerror(core.APP_NAME, f"Δεν ξεκίνησε η αναπαραγωγή:\n{e}")
            return

        def watch():
            rc = proc.poll()
            if rc is None:
                root.after(1000, watch)
                return
            if item_index is not None and item_index < len(items):  # ίσως προστέθηκαν σημειώσεις
                items[item_index]["notes"] = core.load_notes(items[item_index]["cpl"])
                refresh_row(item_index)
                show()
            msgs = {2: "Δεν βρέθηκε το CPL / αρχείο.", 3: "Κρυπτογραφημένο DCP: χρειάζεται KDM."}
            if rc:
                log.error("Η αναπαραγωγή έκλεισε με κωδικό %s", rc)
                if messagebox.askyesno(core.APP_NAME, f"{msgs.get(rc, 'Η αναπαραγωγή σταμάτησε με σφάλμα.')}\n\n"
                                                      "Άνοιγμα του log;"):
                    open_log()

        root.after(1000, watch)

    def do_play():
        sel = selected()
        if not sel:
            return
        c = items[sel[0]]["cpl"]
        spawn_player(c.path, c.title, sel[0])

    tree.bind("<<TreeviewSelect>>", show)
    tree.bind("<Double-Button-1>", lambda e: do_play())
    tree.bind("<Return>", lambda e: do_play())
    if os.environ.get("DCP_THEME") in ("light", "dark"):  # για δοκιμές / screenshots
        theme["mode"] = os.environ["DCP_THEME"]
    apply_theme()
    follow_system()
    root.after(100, poll)
    start_folder = initial or last_folder()
    if start_folder and os.path.isdir(start_folder):
        root.after(150, lambda: load(start_folder))
    shot = os.environ.get("DCP_LAUNCHER_SHOT")  # για δοκιμές: screenshot του παραθύρου και έξοδος
    if shot:
        def grab():
            from PIL import ImageGrab
            if os.environ.get("DCP_SHOT_HASH"):  # συμπλήρωση στήλης hash πριν το screenshot
                for i, it in enumerate(items):
                    it["hash"] = core.verify_cpl(it["cpl"])
                    refresh_row(i)
                show()
            root.lift()
            root.attributes("-topmost", True)
            root.update()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height()), all_screens=True).save(shot)
            root.destroy()
        root.after(4000, grab)
    root.mainloop()


# ======================================================================== ΑΥΤΟΕΛΕΓΧΟΣ
def selftest(out):
    """Για δοκιμές του .exe: ελέγχει ότι φορτώνουν όλα τα κομμάτια."""
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
    ]:
        try:
            fn()
            res.append(f"{name}: OK")
        except Exception as e:
            res.append(f"{name}: FAIL {type(e).__name__}: {e}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(res))


def main():
    if os.environ.get("DCP_SELFTEST"):
        selftest(os.environ["DCP_SELFTEST"])
        return
    ap = argparse.ArgumentParser(description=core.APP_NAME)
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--play", metavar="CPL_ή_ΒΙΝΤΕΟ")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--lowres", default="auto", help="auto | 0 | 1")
    ap.add_argument("--fullscreen", action="store_true")
    a = ap.parse_args()
    core.setup_logging("player" if a.play else "launcher")
    if a.play:
        from player import play
        lr = None if a.lowres == "auto" else int(a.lowres)
        play(a.play, a.start, lr, a.fullscreen)
        return
    f = a.folder
    if f and os.path.isfile(f):
        if f.lower().endswith(core.VIDEO_EXT) and not f.lower().endswith(".mxf"):
            from player import play  # σύρθηκε αρχείο βίντεο πάνω στο exe
            play(f)
            return
        f = os.path.dirname(f)       # σύρθηκε αρχείο του DCP (π.χ. CPL)
    launcher(f)


if __name__ == "__main__":
    main()
