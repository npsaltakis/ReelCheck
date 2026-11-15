"""Παραλαβή DCP από δίσκο: εύρεση των DCP μιας πηγής με μέγεθος και τίτλους (για το παράθυρο παραλαβής)."""
import datetime
import os

from app.core.dcp import find_dcps, load_dcp
from app.core.i18n import tr


def folder_size(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def list_dcps(source):
    """[{'folder', 'name', 'size', 'titles', 'encrypted', 'missing'}] για κάθε DCP στην πηγή."""
    out = []
    for d in find_dcps(source):
        try:
            cpls = load_dcp(d)
        except Exception:
            cpls = []
        out.append({"folder": d, "name": os.path.basename(d), "size": folder_size(d),
                    "titles": [c.title for c in cpls], "encrypted": any(c.encrypted for c in cpls),
                    "missing": sum(len(c.missing) for c in cpls)})
    return out


def report_path(dest):
    return os.path.join(dest, f"{tr('Αναφορά_παραλαβής')}_{datetime.datetime.now():%Y%m%d_%H%M}.html")
