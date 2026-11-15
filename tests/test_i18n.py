"""Τεστ για τη μετάφραση στα αγγλικά: φράσεις, σύνθετα μηνύματα, και ότι τα δεδομένα μένουν ανέγγιχτα."""
import unittest

from app.core import i18n


class TestTranslation(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("el")

    def test_phrases_and_templates(self):
        tr = i18n.tr
        self.assertEqual(tr("ΣΦΑΛΜΑ"), "ERROR")
        self.assertEqual(tr("λείπουν 3 αρχεία"), "3 files missing")
        self.assertEqual(tr("μαύρη εικόνα 00:10:04–00:10:09 (5 δευτ.)"), "black picture 00:10:04–00:10:09 (5 s)")
        self.assertEqual(tr("φεστιβάλ: δεν δέχεται 25 fps"), "festival: 25 fps not accepted")

    def test_composite_messages(self):
        tr = i18n.tr
        self.assertEqual(
            tr("Κατάσταση:   ΣΦΑΛΜΑ  (λείπουν 2 αρχεία; hash: 1 αρχεία με πρόβλημα; κρυπτογραφημένο (θέλει KDM))"),
            "Status:      ERROR  (2 files missing; hash: 1 files with problems; encrypted (needs a KDM))")
        self.assertEqual(tr("3 ταινίες στον κατάλογο · 0 OK · 1 λείπουν"), "3 films in the catalogue · 0 OK · 1 missing")
        self.assertEqual(tr("  ⚠ σιωπή 00:11:08–00:11:19 (11 δευτ.)"), "  ⚠ silence 00:11:08–00:11:19 (11 s)")
        self.assertEqual(tr("\nΕΛΕΓΧΟΙ:"), "\nCHECKS:")

    def test_data_untouched(self):
        tr = i18n.tr
        for data in ("Το Ταξίδι", r"D:\Φεστιβάλ\Ταινίες", "LACHTARA_FTR (Φάκελος με ελληνικά)", "Θόρυβος στον ήχο"):
            self.assertEqual(tr(data), data)
        self.assertEqual(tr("ΣΦΑΛΜΑ  βίντεο.mp4: Το αρχείο δεν έχει εικόνα"), "ERROR  βίντεο.mp4: The file has no picture")

    def test_html(self):
        html = "<html lang='el'><td class='ΣΦΑΛΜΑ'>ΣΦΑΛΜΑ</td><td>Το Ταξίδι</td><th>Κατάσταση</th>"
        out = i18n.tr_html(html)
        self.assertIn("<html lang='en'>", out)
        self.assertIn("class='ΣΦΑΛΜΑ'>ERROR<", out)          # η κλάση CSS μένει
        self.assertIn(">Το Ταξίδι<", out)
        self.assertIn(">Status<", out)

    def test_greek_unchanged(self):
        i18n.set_language("el")
        self.assertEqual(i18n.tr("λείπουν 3 αρχεία"), "λείπουν 3 αρχεία")

    def test_tables_are_valid(self):
        from app.core import lang_en
        for greek, english in lang_en.TEMPLATES:
            self.assertEqual(greek.count("{}"), english.count("{") - english.count("{{"), greek)
        self.assertTrue(all(isinstance(v, str) and v for v in lang_en.EXACT.values()))


if __name__ == "__main__":
    unittest.main()
