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

from app.core.avio import fit_channels, reel_audio, seek_seconds
from app.core.config import log
from app.core.util import check_cancel

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
        streams = ic.streams.video[:1] + ic.streams.audio[:1]
        out = {s.index: oc.add_stream_from_template(s) for s in streams}
        for pkt in ic.demux(streams):
            check_cancel(cancel)
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


def _video_graph(in_stream, rotation, w, h, pix_fmt):
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


DEFAULT_FILE_OPTS = {"quality": "normal", "max_h": 0, "gpu": True, "audio": "stereo", "subs": "none", "srt": None,
                     "remux": True}


class _FileEncoder:
    """Κωδικοποίηση ενός αρχείου: εικόνα από φίλτρα (περιστροφή, κλίμακα, υπότιτλοι) και ήχος με resampler."""

    def __init__(self, oc, vin, ain, preset, opts, channels, src):
        from app.core.subtitles import parse_srt
        self.oc = oc
        self.enc_name, enc_opts, self.pix_fmt = pick_encoder(preset, opts["quality"], opts["gpu"])
        # διαστάσεις και περιστροφή πριν ξεκινήσει η εγγραφή (ο ήχος μπορεί να έρθει πριν από την εικόνα)
        fw, fh, rot = first_frame_geometry(src)
        if abs(round(rot)) % 180 == 90:  # κάθετο βίντεο κινητού: πλάτος και ύψος αλλάζουν θέση
            fw, fh = fh, fw
        w, h = target_size(fw, fh, opts["max_h"])
        entries = parse_srt(opts["srt"]) if opts["subs"] == "burn" and opts.get("srt") else []
        self.vout = oc.add_stream(self.enc_name, rate=vin.average_rate or vin.guessed_rate or Fraction(25))
        self.vout.width, self.vout.height, self.vout.pix_fmt = w, h, self.pix_fmt
        self.vout.codec_context.time_base = vin.time_base
        self.vout.options = enc_opts
        self.graph = _video_graph(vin, rot, w, h, "rgb24" if entries else self.pix_fmt)
        self.burner = SubtitleBurner(entries, w, h) if entries else None
        self.aout = self.resampler = None
        if ain is not None:
            self.aout, self.resampler = _audio_setup(oc, preset, channels, opts["audio"])

    def _mux(self, packets):
        for p in packets:
            self.oc.mux(p)

    def _encode_filtered(self):
        for f in _pull(self.graph):
            if self.burner is not None:
                f = self.burner.burn(f, float(f.pts * f.time_base) if f.pts is not None else 0.0)
                f = f.reformat(format=self.pix_fmt)
            self._mux(self.vout.encode(f))

    def video(self, frame):
        self.graph.push(frame)
        self._encode_filtered()

    def audio(self, frame):
        if self.aout is None:
            return
        frame.pts = None
        for rf in self.resampler.resample(frame):
            self._mux(self.aout.encode(rf))

    def finish(self):
        self.graph.push(None)  # άδειασμα φίλτρων
        self._encode_filtered()
        self._mux(self.vout.encode())
        if self.aout is not None:
            for rf in self.resampler.resample(None):
                self._mux(self.aout.encode(rf))
            self._mux(self.aout.encode())


def transcode_file(src, dst, preset="h264", opts=None, progress=None, cancel=None):
    """
    opts: quality ('high'|'normal'|'small'), max_h (0 = ίδια), gpu (bool), audio ('stereo'|'keep'),
          subs ('none'|'copy'|'burn'), srt (διαδρομή .srt ή None), remux (bool: χωρίς επανακωδικοποίηση όταν γίνεται).
    Επιστρέφει dict με το τι έγινε.
    """
    import av
    opts = dict(DEFAULT_FILE_OPTS, **(opts or {}))
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
        oc = av.open(dst, "w", format=PRESETS[preset]["format"])
        try:
            enc = _FileEncoder(oc, vin, ain, preset, opts, info["channels"], src)
            for pkt in ic.demux(*(s for s in (vin, ain) if s is not None)):
                check_cancel(cancel)
                for frame in pkt.decode():
                    if pkt.stream is not vin:
                        enc.audio(frame)
                        continue
                    enc.video(frame)
                    if progress and frame.time is not None and dur:
                        progress(min(1.0, frame.time / dur), enc.enc_name)
            enc.finish()
        finally:
            oc.close()
    _copy_srt(opts, dst)
    return {"mode": "encode", "encoder": enc.enc_name}


def _copy_srt(opts, dst):
    if opts.get("subs") == "copy" and opts.get("srt"):
        shutil.copy2(opts["srt"], os.path.splitext(dst)[0] + ".srt")


# ======================================================================== DCP → MP4
def _mix(a, nch_out):
    from app.player.audio import downmix
    return downmix(a) if nch_out == 2 else fit_channels(a, nch_out)


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
        self.source = reel_audio(reel, self.RATE, limit=reel.seconds, stop=cancel) if reel.snd else None

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
        seek_seconds(c, vs, Fraction(first) / (reel.rate * mult))
        counter = first - 1
        for fr in c.decode(vs):
            check_cancel(cancel)
            counter += 1
            idx = int(round((fr.pts - st0) * vs.time_base * reel.rate * mult)) if fr.pts is not None else counter
            if idx < first or (mult == 2 and idx % 2):
                continue
            if idx >= last:
                break
            yield fr


class _DcpEncoder:
    """Κωδικοποίηση καρέ DCP: οι διαστάσεις, οι υπότιτλοι και ο ήχος ρυθμίζονται από το πρώτο καρέ."""

    def __init__(self, oc, preset, opts, entries, has_audio, rate):
        self.oc, self.preset, self.opts, self.entries, self.has_audio = oc, preset, opts, entries, has_audio
        self.rate = float(rate)
        self.enc_name, self.enc_opts, self.pix_fmt = pick_encoder(preset, opts["quality"], opts["gpu"])
        self.vout = oc.add_stream(self.enc_name, rate=rate)
        self.vout.codec_context.time_base = Fraction(1) / rate
        self.feeder = self.burner = None
        self.frame_no = 0

    @property
    def seconds(self):
        return self.frame_no / self.rate

    def start_reel(self, reel, cancel):
        self.reel, self.cancel = reel, cancel
        if self.feeder is not None:
            self.feeder.start_reel(reel, cancel)

    def _setup(self, fr):
        w, h = target_size(fr.width, fr.height, self.opts["max_h"])
        self.vout.width, self.vout.height, self.vout.pix_fmt = w, h, self.pix_fmt
        self.vout.options = self.enc_opts
        if self.entries:
            self.burner = SubtitleBurner(self.entries, w, h, fr.width)
        if self.has_audio:
            self.feeder = AudioFeeder(self.oc, self.preset, self.opts["audio"])
            self.feeder.start_reel(self.reel, self.cancel)

    def add(self, fr):
        if self.frame_no == 0:
            self._setup(fr)
        t = self.seconds
        size = {"width": self.vout.width, "height": self.vout.height}
        if self.burner is not None:
            out = self.burner.burn(fr.reformat(format="rgb24", **size), t).reformat(format=self.pix_fmt)
        else:
            out = fr.reformat(format=self.pix_fmt, **size)
        out.pts = self.frame_no
        self.frame_no += 1
        for p in self.vout.encode(out):
            self.oc.mux(p)
        if self.feeder is not None:
            self.feeder.fill_until(t + 0.5)

    def end_reel(self):
        """Τέλος reel: ό,τι έμεινε από τον ήχο, ή σιωπή αν δεν είχε."""
        if self.feeder is not None:
            self.feeder.fill_until(self.seconds)
            self.feeder.silence_until(self.seconds)

    def finish(self):
        for p in self.vout.encode():
            self.oc.mux(p)
        if self.feeder is not None:
            self.feeder.finish()


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
    tracks = load_tracks(cpl) if opts["subs"] == "burn" else []
    entries = tracks[0]["entries"] if tracks else []
    label = f"DCP → {PRESETS[preset]['ext'][1:].upper()}"
    oc = av.open(dst, "w", format=PRESETS[preset]["format"])
    try:
        enc = _DcpEncoder(oc, preset, opts, entries, any(r.snd for r in cpl.reels), cpl.rate)
        for reel in cpl.reels:
            if not reel.pic:
                continue
            enc.start_reel(reel, cancel)
            for fr in _dcp_reel_frames(reel, opts["max_h"], cancel):
                enc.add(fr)
                if progress and cpl.seconds and enc.frame_no % 12 == 0:
                    progress(min(1.0, enc.seconds / cpl.seconds), f"{label} ({enc.enc_name})")
            enc.end_reel()
        enc.finish()
    finally:
        oc.close()
    return {"mode": "dcp", "encoder": enc.enc_name}
