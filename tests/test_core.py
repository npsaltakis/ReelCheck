"""Τεστ για ανάγνωση DCP, ονόματα DCNC, κατάλογο, αναβάθμιση, σάρωση και βοηθητικά."""
import os
import tempfile
import unittest

from tests.helpers import make_ov_vf


class TestDcp(unittest.TestCase):
    def test_vf_takes_assets_from_ov_and_skips_cpl_only(self):
        from app.core.dcp import find_dcps, load_dcp
        with tempfile.TemporaryDirectory() as root:
            ov, vf, ids = make_ov_vf(root)
            self.assertEqual(sorted(find_dcps(root)), sorted([ov, vf]))
            cpl = load_dcp(vf)[0]
            self.assertEqual(cpl.missing, [])                       # τίποτα δεν λείπει
            self.assertNotIn(ids["markers"], cpl.assets)             # MainMarkers δεν είναι αρχείο
            self.assertEqual(set(cpl.external), {ids["pic"], ids["snd"]})
            self.assertTrue(cpl.reels[0].pic.endswith("pic.mxf"))
            self.assertAlmostEqual(cpl.seconds, 10.0)

    def test_missing_file_is_reported(self):
        from app.core.dcp import load_dcp
        with tempfile.TemporaryDirectory() as root:
            ov, _vf, _ids = make_ov_vf(root)
            os.remove(os.path.join(ov, "snd.mxf"))
            cpl = load_dcp(ov)[0]
            self.assertEqual(len(cpl.missing), 1)
            self.assertIn("MainSound", cpl.missing[0])


class TestDcnc(unittest.TestCase):
    NAME = "LACHTARA_FTR-25_S-239_PL-XX_INT-NR_51_2K_MWM_20260721_BOM_SMPTE_OV"

    def test_parse(self):
        from app.checks.dcnc import parse_dcnc
        d = parse_dcnc(self.NAME)
        self.assertEqual((d["title"], d["kind"], d["fps"], d["aspect"]), ("LACHTARA", "FTR", 25, "S"))
        self.assertEqual((d["audio_lang"], d["sub_lang"], d["audio"], d["res"]), ("PL", "XX", "51", "2K"))
        self.assertEqual((d["std"], d["pkg"], d["territory"]), ("SMPTE", "OV", "INT-NR"))

    def test_not_dcnc(self):
        from app.checks.dcnc import parse_dcnc
        self.assertEqual(parse_dcnc("My holiday video"), {})

    def test_langs(self):
        from app.checks.langs import norm_lang
        self.assertEqual([norm_lang(x) for x in ("en-US", "English", "ENG", "GR", "ell")], ["en", "en", "en", "el", "el"])


class TestNames(unittest.TestCase):
    def test_latin(self):
        from app.tools.convert import film_name, latin
        self.assertEqual(latin("Το Ταξίδι"), "To Taxidi")
        self.assertEqual(latin("Μικρού Μήκους"), "Mikrou Mikous")
        self.assertEqual(latin("ΟΥΡΑΝΟΣ"), "OURANOS")
        self.assertEqual(film_name(r"C:\x\Η_Άλλη-Πλευρά.mp4"), "I Alli Pleyra")

    def test_catalog_match(self):
        from app.tools.catalog import norm_title, _match

        class Cpl:
            title = "TOTAXIDI_FTR-1_F_EL-EN_51_2K_20261115_SMPTE_OV"
        self.assertEqual(norm_title("Το Ταξίδι"), "TOTAXIDI")
        self.assertGreaterEqual(_match("Το Ταξίδι", Cpl()), 0.8)
        self.assertLess(_match("Κάτι άλλο εντελώς", Cpl()), 0.8)


class TestUpdate(unittest.TestCase):
    def test_versions(self):
        from app.tools.update import is_newer
        self.assertTrue(is_newer("v1.3.1", "1.3"))
        self.assertTrue(is_newer("1.10", "1.9"))
        self.assertFalse(is_newer("1.3", "1.3"))

    def test_parse_release(self):
        from app.tools.update import parse_release
        r = parse_release({"tag_name": "v2.0", "html_url": "u", "assets": [
            {"name": "ReelCheck.exe", "browser_download_url": "d", "size": 5, "digest": "sha256:AB"}]})
        self.assertEqual((r["version"], r["exe_url"], r["size"], r["sha256"]), ("2.0", "d", 5, "ab"))
        self.assertIsNone(parse_release({"tag_name": "v2.0", "assets": []})["exe_url"])


class TestScanFindings(unittest.TestCase):
    def test_edges_and_short_silence_are_not_warnings(self):
        from app.checks.scan import scan_findings
        res = {"seconds": 3600, "black": [(0, 10), (1800, 1810), (3590, 3600)],
               "silence": [(1000, 1006), (2000, 2015)], "peak_db": [-1] * 6, "clip": [0] * 6, "dead": [],
               "level_db": -25, "audio_seconds": 3600}
        warns = [t for lv, t in scan_findings(res) if lv == "warn"]
        self.assertEqual(len([w for w in warns if "μαύρη" in w]), 1)   # μόνο το μεσαίο
        self.assertEqual(len([w for w in warns if "σιωπή" in w]), 1)   # μόνο αυτή των 15 δευτ.


class TestUtil(unittest.TestCase):
    def test_fmt_time(self):
        from app.core.util import fmt_time
        self.assertEqual(fmt_time(3723.5), "01:02:03")
        self.assertEqual(fmt_time(1.5, 24), "00:00:01:12")

    def test_target_size_and_rotation(self):
        from app.tools.transcode import rotation_filters, target_size
        self.assertEqual(target_size(3840, 2160, 1080), (1920, 1080))
        self.assertEqual(target_size(1280, 720, 1080), (1280, 720))    # ποτέ μεγέθυνση
        self.assertEqual(rotation_filters(-90), [("transpose", "clock")])
        self.assertEqual(rotation_filters(0), [])

    def test_equalize_gains(self):
        from app.tools.mediainfo import equalize_gains
        g = equalize_gains([(-20, -3), (-26, -10), (None, None), (-14, -0.5)])
        self.assertEqual(g[2], 0.0)
        self.assertGreater(g[1], 0)          # το σιγανό δυναμώνει
        self.assertLessEqual(-0.5 + g[3], -1.0 + 1e-9)  # χωρίς να περάσει το όριο κορυφής


class TestSchedule(unittest.TestCase):
    def test_conflicts_and_export(self):
        from app.tools import schedule as sch
        a = sch.new_screening("15/11/2026", "20:00", "Αίθουσα 1", "Α", 90, 10)
        b = sch.new_screening("15/11/2026", "21:30", "αίθουσα 1", "Β", 60)        # πέφτει πάνω στο Α (+10')
        c = sch.new_screening("15/11/2026", "21:30", "Αίθουσα 2", "Γ", 60)        # άλλη αίθουσα
        d = sch.new_screening("15/11/2026", "21:40", "Αίθουσα 1", "Δ", 30)        # πέφτει στο Β
        clash = sch.conflicts([a, b, c, d])
        self.assertEqual(set(clash), {a["id"], b["id"], d["id"]})
        self.assertNotIn(c["id"], clash)
        self.assertEqual(sch.start_end(a)[1].strftime("%H:%M"), "21:40")
        with self.assertRaises(ValueError):
            sch.parse_when("2026-11-15", "20:00")
        with tempfile.TemporaryDirectory() as d_:
            p = os.path.join(d_, "s.json")
            sch.save([a, b], p)
            self.assertEqual(len(sch.load(p)), 2)
            sch.write_html([a, b, c, d], os.path.join(d_, "p.html"))
            with open(os.path.join(d_, "p.html"), encoding="utf-8") as f:
                self.assertIn("ΣΥΓΚΡΟΥΣΗ ΩΡΑΣ", f.read())


class TestSrt(unittest.TestCase):
    def test_parse_srt(self):
        from app.core.subtitles import parse_srt
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.srt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("1\n00:00:01,000 --> 00:00:02,500\nΓεια <i>σου</i>\n\n2\n00:00:03,000 --> 00:00:04,000\nΤέλος\n")
            e = parse_srt(p)
            self.assertEqual(len(e), 2)
            self.assertEqual((e[0][0], e[0][1]), (1.0, 2.5))
            self.assertEqual(e[0][2][0]["text"], "Γεια σου")
            self.assertTrue(e[0][2][0]["italic"])

    def test_subtitle_checks_and_fixes(self):
        from app.tools import subcheck
        t = lambda s: [{"type": "text", "text": s, "italic": False, "y": 90}]
        entries = [(1.0, 3.0, t("Καλά")), (2.5, 4.0, t("Επικάλυψη")), (5.0, 5.3, t("Σύντομος")),
                   (6.0, 7.0, t("Πάρα πολύ μεγάλο κείμενο για ένα δευτερόλεπτο")), (99.0, 100.0, t("Μετά"))]
        probs = subcheck.check_entries(entries, duration=60)
        kinds = {n: [p[3] for p in probs if p[0] == n] for n in range(1, 6)}
        self.assertTrue(any("επικαλύπτεται" in x for x in kinds[1]))
        self.assertTrue(any("σύντομος" in x for x in kinds[3]))
        self.assertTrue(any("γρήγορος" in x for x in kinds[4]))
        self.assertTrue(any("μετά το τέλος" in x for x in kinds[5]))
        self.assertEqual(kinds[2], [])
        shifted = subcheck.shift(entries, -1.5)
        self.assertEqual(shifted[0][0], 0.0)                 # δεν πάει κάτω από το 0
        slow = subcheck.change_fps(entries, 25, 24)
        self.assertAlmostEqual(slow[0][1], 3.0 * 25 / 24)
        self.assertEqual(subcheck.srt_time(3723.456), "01:02:03,456")
        self.assertIn("00:00:01,000 --> 00:00:03,000\nΚαλά", subcheck.to_srt(entries))

    def test_find_srt(self):
        from app.tools.mediainfo import find_srt
        with tempfile.TemporaryDirectory() as d:
            for n in ("film.mp4", "film.el.srt", "other.srt"):
                open(os.path.join(d, n), "w").close()
            self.assertTrue(find_srt(os.path.join(d, "film.mp4")).endswith("film.el.srt"))


if __name__ == "__main__":
    unittest.main()
