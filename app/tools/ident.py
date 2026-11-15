"""
Ident / intro φεστιβάλ: λογότυπο (PNG/JPG) σε μαύρο φόντο με fade in/out και προαιρετικά μουσική και κείμενο,
σε βίντεο MP4 (H.264 υψηλής ποιότητας). Μετά γίνεται DCP με το DCP-o-matic.
"""
import os

from app.core.util import check_cancel

FPS = 24
SIZES = {"185": (1998, 1080), "239": (2048, 858), "169": (1920, 1080)}
RATE = 48000
FADE = 1.0


def _logo_canvas(logo_path, size, text=""):
    """Εικόνα RGB του μεγέθους του κάδρου: λογότυπο στο κέντρο (έως 60% πλάτος / 55% ύψος) και κείμενο από κάτω."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = size
    canvas = Image.new("RGB", size, (0, 0, 0))
    logo = Image.open(logo_path).convert("RGBA")
    scale = min(w * 0.6 / logo.width, h * (0.45 if text else 0.55) / logo.height)
    logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.Resampling.LANCZOS)
    top = (h - logo.height) // 2 - (int(h * 0.06) if text else 0)
    canvas.paste(logo, ((w - logo.width) // 2, top), logo)
    if text:
        draw = ImageDraw.Draw(canvas)
        font = None
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, int(h * 0.045))
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        tw = draw.textlength(text, font=font)
        draw.text(((w - tw) / 2, top + logo.height + int(h * 0.05)), text, font=font, fill=(235, 235, 235))
    return canvas


def _music(path, seconds):
    """Μουσική ως numpy (n, 2) float32 στα 48 kHz, κομμένη στη διάρκεια με fade out. None αν δεν δόθηκε."""
    if not path:
        return None
    import av
    import numpy as np
    chunks, need = [], int(seconds * RATE)
    with av.open(path) as c:
        res = av.AudioResampler(format="flt", layout="stereo", rate=RATE)
        for fr in c.decode(audio=0):
            for rf in res.resample(fr):
                chunks.append(rf.to_ndarray().reshape(-1, 2))
            if sum(len(x) for x in chunks) >= need:
                break
    a = np.concatenate(chunks)[:need] if chunks else np.zeros((0, 2), np.float32)
    if len(a) < need:
        a = np.concatenate([a, np.zeros((need - len(a), 2), np.float32)])
    fade = int(min(FADE * 1.5, seconds / 3) * RATE)
    a[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    return a


def music_seconds(path):
    """Διάρκεια μουσικής σε δευτ. (για προεπιλογή διάρκειας του ident)."""
    import av
    with av.open(path) as c:
        return (c.duration or 0) / 1_000_000


def make_ident_video(out, logo, seconds=8.0, music=None, text="", ratio="185", progress=None, cancel=None):
    """Γράφει το MP4 του ident. Επιστρέφει τη διαδρομή."""
    import av
    import numpy as np
    from PIL import Image
    size = SIZES.get(ratio, SIZES["185"])
    canvas = _logo_canvas(logo, size, text)
    black = Image.new("RGB", size, (0, 0, 0))
    audio = _music(music, seconds)
    frames = int(round(seconds * FPS))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with av.open(out, "w") as c:
        vs = c.add_stream("libx264", rate=FPS)
        vs.width, vs.height, vs.pix_fmt = size[0], size[1], "yuv420p"
        vs.options = {"crf": "14", "preset": "medium"}
        ast = c.add_stream("aac", rate=RATE)
        ast.layout, ast.bit_rate = "stereo", 256_000
        for i in range(frames):
            check_cancel(cancel)
            t = i / FPS
            alpha = max(0.0, min(1.0, t / FADE, (seconds - t) / FADE))
            img = Image.blend(black, canvas, alpha) if alpha < 1 else canvas
            fr = av.VideoFrame.from_image(img)
            fr.pts = i
            for p in vs.encode(fr):
                c.mux(p)
            if progress and i % FPS == 0:
                progress(i / frames, "Ident: εικόνα")
        for p in vs.encode():
            c.mux(p)
        a = audio if audio is not None else np.zeros((int(seconds * RATE), 2), np.float32)
        for k in range(0, len(a), 1024):
            af = av.AudioFrame.from_ndarray(np.ascontiguousarray(a[k:k + 1024].T), format="fltp", layout="stereo")
            af.sample_rate, af.pts = RATE, k
            for p in ast.encode(af):
                c.mux(p)
        for p in ast.encode():
            c.mux(p)
    return out
