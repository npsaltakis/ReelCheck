"""Τεστ για τον αναγνώστη ext2/ext4 με μικρά συστήματα αρχείων (φτιαγμένα με mke2fs -d)."""
import gzip
import io
import os
import tempfile
import unittest

DATA = os.path.join(os.path.dirname(__file__), "data")
EXPECTED_PIC = ("\n".join(str(i) for i in range(1, 60001)) + "\n").encode()  # περιεχόμενο του pic.mxf (seq 1 60000)


def image(name):
    with gzip.open(os.path.join(DATA, name), "rb") as f:
        return io.BytesIO(f.read())


class TestExtFs(unittest.TestCase):
    def check(self, name, version, block_size):
        from app.tools.extfs import AlignedReader, ExtFS
        fs = ExtFS(AlignedReader(image(name)))
        self.assertEqual((fs.version, fs.block_size), (version, block_size))
        dcps = fs.find_dcps()
        self.assertEqual([d[0] for d in dcps], ["/FILM_FTR_OV"])
        pic = fs.lookup("/FILM_FTR_OV/pic.mxf")
        self.assertEqual(b"".join(fs.read_chunks(pic, chunk=7000)), EXPECTED_PIC)  # μικρά κομμάτια
        with tempfile.TemporaryDirectory() as d:
            copied = fs.copy_tree("/", d)
            names = sorted(os.path.relpath(p, d).replace("\\", "/") for p, _s in copied)
            self.assertIn("Ελληνικά/SUB/a.txt", names)
            with open(os.path.join(d, "Ελληνικά", "SUB", "a.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "γεια")
            with open(os.path.join(d, "FILM_FTR_OV", "pic.mxf"), "rb") as f:
                self.assertEqual(f.read(), EXPECTED_PIC)

    def test_ext2_1k_blocks_indirect(self):
        self.check("ext2_tiny.img.gz", "ext2", 1024)   # 340 KB με blocks 1 KB: χρειάζεται διπλά έμμεσα

    def test_ext4_extents(self):
        self.check("ext4_tiny.img.gz", "ext4", 4096)

    def test_not_ext(self):
        from app.tools.extfs import ExtError, ExtFS
        with self.assertRaises(ExtError):
            ExtFS(io.BytesIO(b"\0" * 8192))

    def test_aligned_reader(self):
        from app.tools.extfs import AlignedReader
        raw = bytes(range(256)) * 200
        r = AlignedReader(io.BytesIO(raw), align=512)
        self.assertEqual(r.read_at(1000, 3000), raw[1000:4000])
        self.assertEqual(r.read_at(7, 5), raw[7:12])


if __name__ == "__main__":
    unittest.main()
