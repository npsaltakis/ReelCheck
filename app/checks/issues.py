"""Όλα τα ευρήματα μιας ταινίας μαζί."""
from app.checks.dcnc import name_issues
from app.checks.kdm import kdm_issues
from app.checks.scan import scan_findings
from app.checks.spec import get_spec, spec_issues
from app.core.config import log


# ======================================================================== ΟΛΑ ΜΑΖΙ
def item_issues(item, spec=None):
    """Όλα τα ευρήματα για μία ταινία: όνομα, προδιαγραφές, KDM, σάρωση."""
    c = item["cpl"]
    out = []
    try:
        out += name_issues(item)
        out += spec_issues(item, spec if spec is not None else get_spec())
        out += kdm_issues(c)
        sc = item.get("scan")
        if sc:
            out += scan_findings(sc)  # από τα δεδομένα, ώστε να ισχύουν τα τωρινά όρια
    except Exception:
        log.exception("Σφάλμα στους ελέγχους για %s", c.title)
    return out
