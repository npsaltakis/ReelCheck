"""Ανάλυση εικόνας/ήχου και καρέ (thumbnail) με PyAV."""
import io
from fractions import Fraction

from app.core.config import log


# ----------------------------------------------------------------- PROBE / THUMBNAIL
def probe(cpl):
    """Ανάλυση εικόνας, codec και κανάλια ήχου από το πρώτο reel."""
    import av
    info = {}
    r = next((r for r in cpl.reels if r.pic), None)
    if r:
        try:
            with av.open(r.pic) as c:
                v = c.streams.video[0]
                info["size"] = (v.codec_context.width, v.codec_context.height)
                info["pix"] = v.codec_context.format.name if v.codec_context.format else "?"
                info["codec"] = v.codec_context.name
        except Exception as e:
            log.exception("Δεν ανοίγει η εικόνα %s", r.pic)
            info["pic_err"] = str(e)
    r = next((r for r in cpl.reels if r.snd), None)
    if r:
        try:
            with av.open(r.snd) as c:
                a = c.streams.audio[0]
                info["ch"] = a.codec_context.channels
                info["sr"] = a.codec_context.sample_rate
        except Exception as e:
            log.exception("Δεν ανοίγει ο ήχος %s", r.snd)
            info["snd_err"] = str(e)
    return info


def thumbnail(cpl, at=None, width=480):
    """PNG (bytes) ενός καρέ: προεπιλογή στο 10% της διάρκειας."""
    import av
    from PIL import Image
    at = cpl.seconds * 0.1 if at is None else at
    reel = next((r for r in cpl.reels if r.pic and r.start <= at < r.start + r.seconds), None)
    reel = reel or next((r for r in cpl.reels if r.pic), None)
    if not reel:
        return None
    with av.open(reel.pic) as c:
        vs = c.streams.video[0]
        if vs.codec_context.name == "jpeg2000":
            vs.codec_context.options = {"lowres": "1"}
        mult = 2 if reel.stereo else 1
        frame_no = (reel.pic_entry + int((max(0.0, at - reel.start)) * float(reel.rate))) * mult
        target = Fraction(frame_no) / (reel.rate * mult)
        start = vs.start_time or 0
        try:
            c.seek(int(target / vs.time_base) + start, stream=vs, backward=True)
        except Exception:
            pass
        for fr in c.decode(vs):
            if fr.time is not None and fr.time - float(start * vs.time_base) + 0.001 < float(target):
                continue
            h = max(2, int(fr.height * width / fr.width) // 2 * 2)
            img = Image.fromarray(fr.reformat(width=width, height=h, format="rgb24").to_ndarray())
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
    return None
