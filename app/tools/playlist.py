"""Σειρά λίστας προβολής."""
from app.checks.dcnc import parse_dcnc


def playlist_order(cpls):
    """Πρώτα τρέιλερ/σποτ/διαφημίσεις, τελευταία η ταινία, κρατώντας τη σειρά της λίστας."""
    def rank(c):
        k = parse_dcnc(c.title).get("kind", "")
        return 1 if k in ("FTR", "SHR") or (not k and c.seconds > 20 * 60) else 0
    return sorted(cpls, key=rank)
