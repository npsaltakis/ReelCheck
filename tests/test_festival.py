"""Τεστ για τα εργαλεία φεστιβάλ: σύγκριση DCP, επιστροφές, ημερολόγιο, συγχρονισμός, παρακολούθηση φακέλου."""
import datetime
import os
import shutil
import tempfile
import unittest

from tests.helpers import make_ov_vf


class TestCompare(unittest.TestCase):
    def test_same_and_changed_files(self):
        from app.core.dcp import load_dcp
        from app.tools.compare import DIFF, compare_cpls, summary
        with tempfile.TemporaryDirectory() as root:
            ov, _vf, _ids = make_ov_vf(root)
            copy = os.path.join(root, "copy")
            shutil.copytree(ov, copy)
            a, b = load_dcp(ov)[0], load_dcp(copy)[0]
            rows = compare_cpls(a, b)
            self.assertEqual([r[0] for r in rows if r[3] == DIFF], [])
            self.assertTrue(summary(rows).startswith("Ίδιο περιεχόμενο"))
            with open(os.path.join(copy, "snd.mxf"), "ab") as f:  # άλλο αρχείο ήχου (το PKL δεν έχει hash εδώ)
                f.write(b"x")
            diffs = [r[0] for r in compare_cpls(a, load_dcp(copy)[0]) if r[3] == DIFF]
            self.assertEqual(diffs, ["Reel 1: ήχος"])


class TestReturns(unittest.TestCase):
    def test_state_and_storage(self):
        from app.tools import returns as rt
        today = datetime.date(2026, 11, 20)
        late = rt.new_return("Α", due="19/11/2026")
        soon = rt.new_return("Β", due="22/11/2026")
        later = rt.new_return("Γ", due="30/12/2026")
        done = rt.new_return("Δ", due="01/11/2026")
        rt.mark_returned(done, today)
        self.assertEqual([rt.state(x, today) for x in (late, soon, later, done)], ["late", "soon", "open", "returned"])
        self.assertEqual(done["returned"], "20/11/2026")
        with self.assertRaises(ValueError):
            rt.parse_date("2026-11-20")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            rt.save([late, done], p)
            self.assertEqual([x["title"] for x in rt.load(p)], ["Α", "Δ"])
            self.assertEqual(rt.load(os.path.join(d, "δεν_υπάρχει.json")), [])


class TestSchedule(unittest.TestCase):
    def test_ics_escaping_and_folding(self):
        from app.tools import schedule as sch
        s = sch.new_screening("20/11/2026", "20:30", "Αίθουσα 1", "Ταινία, με κόμμα; και " + "πολύ " * 20, 95, 5,
                              notes="γραμμή 1\nγραμμή 2")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.ics")
            sch.write_ics([s], p)
            with open(p, "rb") as f:
                raw = f.read()
        self.assertTrue(all(len(line) <= 75 for line in raw.split(b"\r\n")))
        text = raw.decode("utf-8").replace("\r\n ", "")
        self.assertIn("DTSTART:20261120T203000", text)
        self.assertIn("DTEND:20261120T221000", text)   # 95 + 5 λεπτά
        self.assertIn("Ταινία\\, με κόμμα\\; και", text)
        self.assertIn("γραμμή 1\\nγραμμή 2", text)

    def test_day_sheets_one_page_per_day_and_room(self):
        from app.tools import schedule as sch
        items = [sch.new_screening("20/11/2026", "18:00", "Α", "x", 90), sch.new_screening("20/11/2026", "21:00", "Α", "y", 90),
                 sch.new_screening("20/11/2026", "20:00", "Β", "z", 90), sch.new_screening("21/11/2026", "20:00", "Α", "w", 90)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.html")
            sch.write_day_sheets(items, p, lambda s: {"format": "2K", "audio": "5.1"})
            with open(p, encoding="utf-8") as f:
                html = f.read()
        self.assertEqual(html.count("class='page'"), 3)
        self.assertIn("2K · 5.1", html)


class TestSync(unittest.TestCase):
    def test_flash_and_beep_detection(self):
        from app.checks.sync import find_beeps, find_flashes
        samples = [(i / 24, 20.0) for i in range(240)]
        for k in (24, 120):                    # δύο φλας του ενός καρέ
            samples[k] = (k / 24, 230.0)
        for k in range(150, 200):              # αλλαγή πλάνου σε φωτεινό: δεν είναι φλας
            samples[k] = (k / 24, 230.0)
        self.assertEqual(find_flashes(samples), [1.0, 5.0])
        levels = [-70.0] * 5000                # 10 δευτ. σε παράθυρα 2 ms
        for start in (520, 2520):              # μπιπ 80 ms στα 1.04 και 5.04 δευτ.
            levels[start:start + 40] = [-6.0] * 40
        self.assertEqual([round(b, 2) for b in find_beeps(levels)], [1.04, 5.04])


class TestWatch(unittest.TestCase):
    def test_new_dcp_reported_once_when_stable(self):
        from app.tools.watch import FolderWatcher
        with tempfile.TemporaryDirectory() as root:
            got = []
            w = FolderWatcher(root, got.append)
            w.start = lambda: None
            ov, _vf, _ids = make_ov_vf(os.path.join(root, "in"))
            with open(os.path.join(ov, "PKL.xml"), "w", encoding="utf-8") as f:  # τα πραγματικά DCP έχουν πάντα PKL
                f.write('<PackingList xmlns="http://www.smpte-ra.org/schemas/429-8/2007/PKL"><AssetList><Asset>'
                        '<Id>urn:uuid:11111111-1111-1111-1111-111111111111</Id><Size>0</Size></Asset></AssetList>'
                        '</PackingList>')
            part = os.path.join(ov, "pic.mxf.part")
            open(part, "w").close()
            self.assertEqual(w.poll(), [])     # μισό αρχείο
            os.remove(part)
            self.assertEqual(w.poll(), [])     # πρώτη φορά «σταθερό»
            ready = w.poll()
            self.assertIn(ov, ready)
            self.assertEqual(w.poll(), [])     # όχι ξανά
            self.assertIn(ov, got)


class TestJson(unittest.TestCase):
    def test_load_save_json(self):
        from app.core.util import load_json, save_json
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "a.json")
            self.assertEqual(load_json(p, {}), {})
            save_json(p, {"τίτλος": "Ταξίδι"})
            self.assertEqual(load_json(p), {"τίτλος": "Ταξίδι"})
            self.assertFalse(os.path.exists(p + ".tmp"))
            with open(p, "w") as f:
                f.write("{χαλασμένο")
            self.assertEqual(load_json(p, []), [])


if __name__ == "__main__":
    unittest.main()
