"""
Μετατροπή βίντεο με το FFmpeg που περιέχει το PyAV (δεν χρειάζεται ξεχωριστό ffmpeg.exe):
MP4 H.264 / H.265, MOV ProRes, αλλαγή κοντέινερ χωρίς επανακωδικοποίηση, κάρτα γραφικών όταν υπάρχει,
αλλαγή ανάλυσης, σωστή περιστροφή βίντεο κινητού, υπότιτλοι (.srt δίπλα ή «καμένοι» στην εικόνα),
και DCP → MP4 (screener) με υπότιτλους του DCP.
"""
import io
import os
import shutil
from fractions import Fraction

from app.core.config import log
from app.core.hashing import Cancelled

PRESETS = {
    "h264": {"label": "MP4 H.264 (παίζει παντού)", "ext": ".mp4", "format": "mp4", "video": "h264", "audio": "aac"},
    "h265": {"label": "MP4 H.265 (μικρότερο αρχείο)", "ext": ".mp4", "format": "mp4", "video": "hevc", "audio": "aac"},
    "prores": {"label": "MOV ProRes 422 HQ (για μοντάζ)", "ext": ".mov", "format": "mov", "video": "prores",
               "audio": "pcm"},
}
QUALITY_LABELS = {"high": "Υψηλή", "normal": "Κανονική", "small": "Μικρό αρχείο"}
SIZE_LABELS = {0: "Ίδια ανάλυση", 2160: "4K (2160p)", 1080: "1080p", 720: "720p"}
SUB_MODES = {"none": "Χωρίς υπότιτλους", "copy": ".srt δίπλα στο νέο αρχείο", "burn": "Καμένοι στην εικόνα"}
AUDIO_MODES = {"stereo": "Στέρεο", "keep": "Ίδια κανάλια (π.χ. 5.1)"}

# ποιότητα: CRF για x264/x265, CQ/QP για κάρτες γραφικών
_CRF = {"h264": {"high": 18, "normal": 21, "small": 26}, "hevc": {"high": 20, "normal": 24, "small": 28}}
_HW_Q = {"high": 19, "normal": 23, "small": 28}
_HW_ENCODERS = {"h264": ["h264_nvenc", "h264_amf", "h264_qsv"], "hevc": ["hevc_nvenc", "hevc_amf", "hevc_qsv"]}
_SW_ENCODER = {"h264": "libx264", "hevc": "libx265", "prores": "prores_ks"}
_REMUX_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
_encoder_cache = {}


# ======================================================================== ENCODERS
def _hw_options(name, quality):
    q = _HW_Q[quality]
    if name.endswith("_nvenc"):
        return {"preset": "p5", "rc": "vbr", "cq": str(q), "b": "0"}
    if name.endswith("_amf"):
        return {"quality": "quality", "rc": "cqp", "qp_i": str(q), "qp_p": str(q + 2), "qp_b": str(q + 4)}
    if name.endswith("_qsv"):
        return {"global_quality": str(q), "preset": "medium"}
    return {}


def encoder_works(name, options=None, pix_fmt="yuv420p"):
    """Δοκιμαστική κωδικοποίηση λίγων καρέ: True αν ο encoder δουλεύει σε αυτόν τον υπολογιστή."""
    key = (name, tuple(sorted((options or {}).items())), pix_fmt)
    if key in _encoder_cache:
        return _encoder_cache[key]
    import av
    import numpy as np
    ok = False
    try:
        oc = av.open(io.BytesIO(), "w", format="mov" if name.startswith("prores") else "mp4")
        s = oc.add_stream(name, rate=25)
        s.width, s.height, s.pix_fmt = 320, 180, pix_fmt
        if options:
            s.options = dict(options)
        for i in range(3):
            f = av.VideoFrame.from_ndarray(np.full((180, 320, 3), 40 * i, np.uint8), format="rgb24").reformat(
                format=pix_fmt)
            f.pts = i
            for p in s.encode(f):
                oc.mux(p)
        for p in s.encode():
            oc.mux(p)
        oc.close()
        ok = True
    except Exception as e:
        log.info("Encoder %s δεν δουλεύει εδώ: %s", name, str(e)[:120])
    _encoder_cache[key] = ok
    return ok


def pick_encoder(preset, quality, use_gpu=True):
    """(όνομα encoder, options, pix_fmt). Πρώτα κάρτα γραφικών (αν επιτρέπεται και δουλεύει), αλλιώς CPU."""
    kind = PRESETS[preset]["video"]
    if kind == "prores":
        return "prores_ks", {"profile": "3", "vendor": "apl0"}, "yuv422p10le"
    if use_gpu:
        for name in _HW_ENCODERS.get(kind, []):
            opts = _hw_options(name, quality)
            if encoder_works(name, opts, "nv12"):
                return name, opts, "nv12"
    opts = {"crf": str(_CRF[kind][quality]), "preset": "medium"}
    if kind == "hevc":
        opts["x265-params"] = "log-level=error"
        opts["tag"] = "hvc1"
    return _SW_ENCODER[kind], opts, "yuv420p"


def gpu_name(preset="h264"):
    """Ποια κάρτα θα χρησιμοποιηθεί (για εμφάνιση), ή None."""
    name, _o, _p = pick_encoder(preset, "normal", True)
    for key, label in (("nvenc", "NVIDIA"), ("amf", "AMD"), ("qsv", "Intel")):
        if key in name:
            return label
    return None


# ======================================================================== ΒΟΗΘΗΤΙΚΑ
def output_path(src, out_dir, preset, suffix=""):
    base = os.path.splitext(os.path.basename(src.rstrip("\\/")))[0] + suffix
    path = os.path.join(out_dir, base + PRESETS[preset]["ext"])
    n = 1
    while os.path.exists(path) or os.path.normcase(path) == os.path.normcase(src):
        path = os.path.join(out_dir, f"{base} ({n}){PRESETS[preset]['ext']}")
        n += 1
    return path


def target_size(w, h, max_h):
    """Νέες διαστάσεις (ζυγές) με όριο ύψους, χωρίς μεγέθυνση."""
    if max_h and h > max_h:
        w, h = w * max_h / h, max_h
    return max(2, int(round(w / 2)) * 2), max(2, int(round(h / 2)) * 2)


def rotation_filters(rotation):
    """Φίλτρα FFmpeg για βίντεο κινητού με ένδειξη περιστροφής (όπως κάνει το ffmpeg αυτόματα)."""
    theta = (-int(round(rotation or 0))) % 360
    return {90: [("transpose", "clock")], 270: [("transpose", "cclock")],
            180: [("hflip", ""), ("vflip", "")]}.get(theta, [])


def can_remux(info, preset, opts):
    """Μόνο αλλαγή κοντέινερ (χωρίς απώλεια): ίδιος κωδικοποιητής, χωρίς αλλαγή ανάλυσης/υποτίτλων/ήχου."""
    p = PRESETS[preset]
    if p["format"] != "mp4" or info.get("vcodec") != p["video"]:
        return False
    if opts.get("max_h") and info.get("height", 0) > opts["max_h"]:
        return False
    if opts.get("subs") == "burn" or (opts.get("audio") == "stereo" and info.get("channels", 0) > 2):
        return False
    return info.get("acodec") in _REMUX_AUDIO or not info.get("acodec")


def probe(path):
    """Κωδικοποιητές και διαστάσεις για την απόφαση remux / μετατροπή."""
    import av
    info = {"vcodec": None, "acodec": None, "width": 0, "height": 0, "channels": 0, "duration": 0.0}
    with av.open(path) as c:
        if c.duration:
            info["duration"] = c.duration / 1_000_000
        if c.streams.video:
            v = c.streams.video[0].codec_context
            info.update(vcodec=v.name, width=v.width, height=v.height)
        if c.streams.audio:
            a = c.streams.audio[0].codec_context
            info.update(acodec=a.name, channels=a.channels or 0)
    return info


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# ======================================================================== ΥΠΟΤΙΤΛΟΙ ΣΤΗΝ ΕΙΚΟΝΑ
class SubtitleBurner:
    """Γράφει υπότιτλους πάνω σε καρέ RGB (κείμενο με περίγραμμα, ή εικόνες PNG των DCP)."""

    def __init__(self, entries, width, height, src_width=None):
        self.entries = sorted(entries, key=lambda e: e[0])
        self.w, self.h = width, height
        self.src_w = src_width or width
        self.font = self._load_font(max(16, int(height * 0.055)))

    @staticmethod
    def _load_font(size):
        from PIL import ImageFont
        for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def active(self, t):
        return [e for e in self.entries if e[0] <= t < e[1]]

    def burn(self, frame, t):
        """Επιστρέφει νέο av.VideoFrame με τους υπότιτλους, ή το ίδιο αν δεν υπάρχουν τη στιγμή t."""
        act = self.active(t)
        if not act:
            return frame
        import av
        from PIL import ImageDraw
        img = frame.to_image()
        draw = ImageDraw.Draw(img)
        for _a, _b, items in act:
            self._draw_images(img, [i for i in items if i["type"] == "image"])
            texts = [i["text"] for i in items if i["type"] == "text"]
            if texts:
                top = all(i.get("y", 90) < 40 for i in items if i["type"] == "text")
                self._draw_text(draw, texts, top)
        out = av.VideoFrame.from_image(img)
        out.pts, out.time_base = frame.pts, frame.time_base
        return out

    def _draw_text(self, draw, lines, top):
        line_h = int(self.font.size * 1.25)
        y = int(self.h * 0.06) if top else self.h - int(self.h * 0.07) - line_h * len(lines)
        stroke = max(2, self.font.size // 12)
        for line in lines:
            w = draw.textlength(line, font=self.font)
            draw.text(((self.w - w) / 2, y), line, font=self.font, fill=(255, 255, 255),
                      stroke_width=stroke, stroke_fill=(0, 0, 0))
            y += line_h

    def _draw_images(self, img, items):
        from PIL import Image
        for it in items:
            sub = Image.open(io.BytesIO(it["png"])).convert("RGBA")
            scale = self.w / max(1, self.src_w)
            if abs(scale - 1) > 0.01:
                sub = sub.resize((max(1, int(sub.width * scale)), max(1, int(sub.height * scale))))
            x = (self.w - sub.width) // 2
            y = self.h - int(self.h * it.get("vpos", 8) / 100) - sub.height
            img.paste(sub, (x, max(0, y)), sub)


# ======================================================================== ΑΡΧΕΙΑ ΒΙΝΤΕΟ
def remux(src, dst, progress=None, cancel=None):
    """Αντιγραφή εικόνας/ήχου σε νέο κοντέινερ χωρίς επανακωδικοποίηση (γρήγορο, χωρίς απώλεια)."""
    import av
    with av.open(src) as ic, av.open(dst, "w") as oc:
        dur = (ic.duration or 0) / 1_000_000
        streams = [s for s in (ic.streams.video[:1] + ic.streams.audio[:1])]
        out = {s.index: oc.add_stream_from_template(s) for s in streams}
        for pkt in ic.demux(streams):
            _check_cancel(cancel)
            if pkt.dts is None:
                continue
            pkt.stream = out[pkt.stream.index]
            oc.mux(pkt)
            if progress and pkt.pts is not None and pkt.time_base and dur:
                progress(min(1.0, float(pkt.pts * pkt.time_base) / dur), "χωρίς επανακωδικοποίηση")


def _audio_setup(oc, preset, channels, mode):
    import av
    if PRESETS[preset]["audio"] == "pcm":
        st = oc.add_stream("pcm_s16le", rate=48000)
        fmt = "s16"
    else:
        st = oc.add_stream("aac", rate=48000)
        fmt = "fltp"
    layout = "stereo" if mode == "stereo" or channels <= 2 else ("5.1" if channels >= 6 else "stereo")
    st.layout = layout
    if st.codec_context.name == "aac":
        st.bit_rate = 192_000 if layout == "stereo" else 384_000
    return st, av.AudioResampler(format=fmt, layout=layout, rate=48000)


def _video_graph(in_stream, enc_stream, rotation, w, h, pix_fmt):
    import av
    g = av.filter.Graph()
    nodes = [g.add_buffer(template=in_stream)]
    for name, arg in rotation_filters(rotation):
        nodes.append(g.add(name, arg) if arg else g.add(name))
    nodes.append(g.add("scale", f"{w}:{h}:flags=bicubic"))
    nodes.append(g.add("format", pix_fmt))
    nodes.append(g.add("buffersink"))
    g.link_nodes(*nodes).configure()
    return g


def _pull(graph):
    import av
    out = []
    while True:
        try:
            out.append(graph.pull())
        except (av.BlockingIOError, av.EOFError, BlockingIOError, EOFError):
            return out


def first_frame_geometry(src):
    """(πλάτος, ύψος, περιστροφή) από το πρώτο καρέ: χρειάζονται πριν ξεκινήσει η εγγραφή του αρχείου."""
    import av
    with av.open(src) as c:
        for fr in c.decode(video=0):
            return fr.width, fr.height, getattr(fr, "rotation", 0) or 0
    raise ValueError("Το αρχείο δεν έχει εικόνα")


def transcode_file(src, dst, preset="h264", opts=None, progress=None, cancel=None):
    """
    opts: quality ('high'|'normal'|'small'), max_h (0 = ίδια), gpu (bool), audio ('stereo'|'keep'),
          subs ('none'|'copy'|'burn'), srt (διαδρομή .srt ή None), remux (bool: χωρίς επανακωδικοποίηση όταν γίνεται).
    Επιστρέφει dict με το τι έγινε.
    """
    import av
    from app.core.subtitles import parse_srt
    opts = dict({"quality": "normal", "max_h": 0, "gpu": True, "audio": "stereo", "subs": "none", "srt": None,
                 "remux": True}, **(opts or {}))
    info = probe(src)
    if opts["remux"] and can_remux(info, preset, opts):
        remux(src, dst, progress, cancel)
        _copy_srt(opts, dst)
        return {"mode": "remux", "encoder": "copy"}
    with av.open(src) as ic:
        vin = ic.streams.video[0] if ic.streams.video else None
        ain = ic.streams.audio[0] if ic.streams.audio else None
        if vin is None:
            raise ValueError("Το αρχείο δεν έχει εικόνα")
        vin.thread_type = "AUTO"
        dur = (ic.duration or 0) / 1_000_000
        enc_name, enc_opts, pix_fmt = pick_encoder(preset, opts["quality"], opts["gpu"])
        rate = vin.average_rate or vin.guessed_rate or Fraction(25)
        # διαστάσεις και περιστροφή πριν ξεκινήσει η εγγραφή (ο ήχος μπορεί να έρθει πριν από την εικόνα)
        fw, fh, rot = first_frame_geometry(src)
        if abs(round(rot)) % 180 == 90:  # κάθετο βίντεο κινητού: πλάτος και ύψος αλλάζουν θέση
            fw, fh = fh, fw
        w, h = target_size(fw, fh, opts["max_h"])
        entries = parse_srt(opts["srt"]) if opts["subs"] == "burn" and opts.get("srt") else []
        oc = av.open(dst, "w", format=PRESETS[preset]["format"])
        try:
            vout = oc.add_stream(enc_name, rate=rate)
            vout.width, vout.height, vout.pix_fmt = w, h, pix_fmt
            vout.codec_context.time_base = vin.time_base
            vout.options = enc_opts
            graph = _video_graph(vin, vout, rot, w, h, "rgb24" if entries else pix_fmt)
            burner = SubtitleBurner(entries, w, h) if entries else None
            aout = resampler = None
            if ain is not None:
                aout, resampler = _audio_setup(oc, preset, info["channels"], opts["audio"])
            for pkt in ic.demux(*(s for s in (vin, ain) if s is not None)):
                _check_cancel(cancel)
                for frame in pkt.decode():
                    if pkt.stream is vin:
                        graph.push(frame)
                        for f in _pull(graph):
                            if burner is not None:
                                f = burner.burn(f, float(f.pts * f.time_base) if f.pts is not None else 0.0)
                                f = f.reformat(format=pix_fmt)
                            for p in vout.encode(f):
                                oc.mux(p)
                        if progress and frame.time is not None and dur:
                            progress(min(1.0, frame.time / dur), f"{enc_name}")
                    elif aout is not None:
                        frame.pts = None
                        for rf in resampler.resample(frame):
                            for p in aout.encode(rf):
                                oc.mux(p)
            graph.push(None)  # άδειασμα φίλτρων
            for f in _pull(graph):
                if burner is not None:
                    f = burner.burn(f, float(f.pts * f.time_base) if f.pts is not None else 0.0)
                    f = f.reformat(format=pix_fmt)
                for p in vout.encode(f):
                    oc.mux(p)
            for p in vout.encode():
                oc.mux(p)
            if aout is not None:
                for rf in resampler.resample(None):
                    for p in aout.encode(rf):
                        oc.mux(p)
                for p in aout.encode():
                    oc.mux(p)
        finally:
            oc.close()
    if opts["subs"] == "copy":
        _copy_srt(opts, dst)
    return {"mode": "encode", "encoder": enc_name}


def _copy_srt(opts, dst):
    if opts.get("subs") == "copy" and opts.get("srt"):
        shutil.copy2(opts["srt"], os.path.splitext(dst)[0] + ".srt")


# ======================================================================== DCP → MP4
def _dcp_audio(reel, nch_out, cancel):
    """Γεννήτρια (χρόνος_αρχής_σε_δείγματα, numpy (n, ch) float32) για τον ήχο ενός reel."""
    import av
    import numpy as np
    if not reel.snd:
        return
    sr = 48000
    start_s = float(reel.snd_entry / reel.snd_rate)
    need = int(round(reel.seconds * sr))
    with av.open(reel.snd) as c:
        st = c.streams.audio[0]
        if start_s > 0:
            try:
                c.seek(int(start_s / st.time_base) + (st.start_time or 0), stream=st, backward=True)
            except Exception:
                pass
        a0 = float((st.start_time or 0) * st.time_base) if st.time_base else 0.0
        start_sample, pos, done = int(round(start_s * sr)), None, 0
        for fr in c.decode(st):
            _check_cancel(cancel)
            if done >= need:
                break
            a = fr.to_ndarray()
            ch = fr.layout.nb_channels if hasattr(fr.layout, "nb_channels") else len(fr.layout.channels)
            a = a.T if fr.format.is_planar else a.reshape(-1, ch)
            a = (a.astype(np.float32) / 2147483648.0) if a.dtype == np.int32 else a.astype(np.float32)
            fs = int(round((fr.time - a0) * sr)) if fr.time is not None else (pos or start_sample)
            pos = fs + len(a)
            if pos <= start_sample:
                continue
            if fs < start_sample:
                a = a[start_sample - fs:]
            a = a[:need - done]
            done += len(a)
            yield a


def _mix(a, nch_out):
    import numpy as np
    from app.player.audio import downmix
    if nch_out == 2:
        return downmix(a)
    out = np.zeros((len(a), nch_out), np.float32)
    k = min(nch_out, a.shape[1])
    out[:, :k] = a[:, :k]
    return out


class AudioFeeder:
    """Γράφει τον ήχο των reels στο αρχείο εξόδου, λίγο μπροστά από την εικόνα (για σωστό interleaving)."""

    RATE = 48000

    def __init__(self, oc, preset, mode):
        import av
        self.av = av
        self.nch = 2 if mode == "stereo" else 6
        self.stream, self.resampler = _audio_setup(oc, preset, self.nch, mode)
        self.layout = "stereo" if self.nch == 2 else "5.1"
        self.oc = oc
        self.samples = 0
        self.source = None

    def start_reel(self, reel, cancel):
        self.source = _dcp_audio(reel, self.nch, cancel) if reel.snd else None

    @property
    def seconds(self):
        return self.samples / self.RATE

    def _write(self, a):
        import numpy as np
        af = self.av.AudioFrame.from_ndarray(np.ascontiguousarray(_mix(a, self.nch).T), format="fltp",
                                             layout=self.layout)
        af.sample_rate = self.RATE
        af.pts, af.time_base = self.samples, Fraction(1, self.RATE)
        self.samples += len(a)
        for rf in self.resampler.resample(af):
            for p in self.stream.encode(rf):
                self.oc.mux(p)

    def fill_until(self, t):
        """Ήχος μέχρι τη χρονική στιγμή t (δευτ.)."""
        while self.source is not None and self.seconds < t:
            a = next(self.source, None)
            if a is None:
                self.source = None
                return
            self._write(a)

    def silence_until(self, t):
        """Σιωπή για reel χωρίς ήχο, ώστε να μη χαθεί ο συγχρονισμός."""
        import numpy as np
        while self.seconds < t - 0.001:
            n = min(4800, int(round((t - self.seconds) * self.RATE)))
            if n <= 0:
                break
            self._write(np.zeros((n, self.nch), np.float32))

    def finish(self):
        for rf in self.resampler.resample(None):
            for p in self.stream.encode(rf):
                self.oc.mux(p)
        for p in self.stream.encode():
            self.oc.mux(p)


def _dcp_reel_frames(reel, max_h, cancel):
    """Γεννήτρια καρέ (av.VideoFrame) ενός reel: μόνο το αριστερό μάτι στα 3D, από EntryPoint για Duration καρέ."""
    import av
    with av.open(reel.pic) as c:
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        if vs.codec_context.name == "jpeg2000" and max_h and max_h <= 1080 and (vs.codec_context.height or 0) > 2000:
            vs.codec_context.options = {"lowres": "1"}  # 4K DCP: αποκωδικοποίηση κατευθείαν σε 2K
        mult = 2 if reel.stereo else 1
        st0 = vs.start_time or 0
        first, last = reel.pic_entry * mult, (reel.pic_entry + reel.dur) * mult
        if first:
            c.seek(int(Fraction(first) / (reel.rate * mult) / vs.time_base) + st0, stream=vs, backward=True)
        counter = first - 1
        for fr in c.decode(vs):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            counter += 1
            idx = int(round((fr.pts - st0) * vs.time_base * reel.rate * mult)) if fr.pts is not None else counter
            if idx < first or (mult == 2 and idx % 2):
                continue
            if idx >= last:
                break
            yield fr


def transcode_dcp(cpl, dst, preset="h264", opts=None, progress=None, cancel=None):
    """
    DCP (μη κρυπτογραφημένο) → MP4/MOV, π.χ. screener για επιτροπή. Υπότιτλοι του DCP καμένοι στην εικόνα
    αν opts['subs'] == 'burn'. Χρώματα από XYZ σε Rec.709 (προσεγγιστικά, όπως στην αναπαραγωγή).
    """
    import av
    from app.core.subtitles import load_tracks
    opts = dict({"quality": "normal", "max_h": 1080, "gpu": True, "audio": "stereo", "subs": "burn"}, **(opts or {}))
    if cpl.encrypted:
        raise ValueError("Κρυπτογραφημένο DCP: χρειάζεται KDM")
    enc_name, enc_opts, pix_fmt = pick_encoder(preset, opts["quality"], opts["gpu"])
    rate, total = cpl.rate, cpl.seconds
    tracks = load_tracks(cpl) if opts["subs"] == "burn" else []
    entries = tracks[0]["entries"] if tracks else []
    has_audio = any(r.snd for r in cpl.reels)
    oc = av.open(dst, "w", format=PRESETS[preset]["format"])
    try:
        vout = oc.add_stream(enc_name, rate=rate)
        vout.codec_context.time_base = Fraction(1) / rate
        feeder = burner = None
        frame_no = 0
        for reel in cpl.reels:
            if not reel.pic:
                continue
            if feeder is not None:
                feeder.start_reel(reel, cancel)
            for fr in _dcp_reel_frames(reel, opts["max_h"], cancel):
                if burner is None and frame_no == 0:  # ρυθμίσεις από το πρώτο καρέ
                    w, h = target_size(fr.width, fr.height, opts["max_h"])
                    vout.width, vout.height, vout.pix_fmt = w, h, pix_fmt
                    vout.options = enc_opts
                    if entries:
                        burner = SubtitleBurner(entries, w, h, fr.width)
                    if has_audio:
                        feeder = AudioFeeder(oc, preset, opts["audio"])
                        feeder.start_reel(reel, cancel)
                t = frame_no / float(rate)
                if burner is not None:
                    out = burner.burn(fr.reformat(width=vout.width, height=vout.height, format="rgb24"), t)
                    out = out.reformat(format=pix_fmt)
                else:
                    out = fr.reformat(width=vout.width, height=vout.height, format=pix_fmt)
                out.pts = frame_no
                frame_no += 1
                for p in vout.encode(out):
                    oc.mux(p)
                if feeder is not None:
                    feeder.fill_until(t + 0.5)
                if progress and total and frame_no % 12 == 0:
                    progress(min(1.0, t / total), f"DCP → {PRESETS[preset]['ext'][1:].upper()} ({enc_name})")
            if feeder is not None:  # τέλος reel: ό,τι έμεινε από τον ήχο, ή σιωπή αν δεν είχε
                feeder.fill_until(frame_no / float(rate))
                feeder.silence_until(frame_no / float(rate))
        for p in vout.encode():
            oc.mux(p)
        if feeder is not None:
            feeder.finish()
    finally:
        oc.close()
    return {"mode": "dcp", "encoder": enc_name}
