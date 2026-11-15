"""Τεστ με πραγματικά (μικρά) βίντεο: μετατροπή, χωρίς επανακωδικοποίηση, υπότιτλοι στην εικόνα."""
import os
import tempfile
import unittest

from tests.helpers import make_video


class TestTranscode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = cls.tmp.name
        cls.h264_mov = make_video(os.path.join(cls.dir, "clip.mov"), container="mov")
        cls.prores = make_video(os.path.join(cls.dir, "pro.mov"), codec="prores_ks", container="mov")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_encoder_available(self):
        from app.tools.transcode import encoder_works
        self.assertTrue(encoder_works("libx264"))

    def test_remux_mov_to_mp4(self):
        from app.tools.transcode import output_path, probe, transcode_file
        dst = output_path(self.h264_mov, self.dir, "h264")
        r = transcode_file(self.h264_mov, dst, "h264", {"gpu": False})
        self.assertEqual(r["mode"], "remux")
        info = probe(dst)
        self.assertEqual((info["vcodec"], info["acodec"]), ("h264", "aac"))
        self.assertAlmostEqual(info["duration"], 1.0, delta=0.15)

    def test_prores_to_h264_with_burned_subs(self):
        from app.tools.transcode import output_path, probe, transcode_file
        srt = os.path.join(self.dir, "pro.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:01,000\nΔοκιμή\n")
        dst = output_path(self.prores, self.dir, "h264", " burned")
        r = transcode_file(self.prores, dst, "h264", {"gpu": False, "subs": "burn", "srt": srt})
        self.assertEqual((r["mode"], r["encoder"]), ("encode", "libx264"))
        info = probe(dst)
        self.assertEqual((info["vcodec"], info["width"], info["height"]), ("h264", 320, 180))

    def test_lufs_reference_tone(self):
        """EBU R128: ημίτονο 1 kHz στα -20 dBFS σε L και R = -20 LUFS."""
        import numpy as np
        from app.checks.loudness import LoudnessMeter
        m = LoudnessMeter(6)
        t = np.arange(48000 * 5) / 48000
        tone = (0.1 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        a = np.zeros((len(tone), 6), np.float32)
        a[:, 0] = a[:, 1] = tone
        for k in range(0, len(a), 4800):
            m.add(a[k:k + 4800])
        r = m.result()
        self.assertAlmostEqual(r["integrated"], -20.0, delta=0.2)
        self.assertAlmostEqual(r["true_peak"], -20.0, delta=0.3)

    def test_lufs_file(self):
        from app.checks.loudness import measure_file
        r = measure_file(self.h264_mov)
        self.assertIsNotNone(r["integrated"])
        self.assertLess(r["integrated"], 0)

    def test_audio_level(self):
        from app.tools.mediainfo import audio_level
        rms, peak = audio_level(self.h264_mov)
        self.assertLess(rms, peak)
        self.assertLess(peak, 0)


if __name__ == "__main__":
    unittest.main()
