"""
Κοινά βοηθητικά για ανάγνωση ήχου με το PyAV: καρέ ήχου σε numpy, προσαρμογή καναλιών, seek,
και ο ήχος ενός reel DCP από το EntryPoint του (για αναπαραγωγή, σάρωση και DCP → MP4).
"""
_SCALE = {"int32": 2147483648.0, "int16": 32768.0}


def frame_to_float(frame):
    """av.AudioFrame -> numpy (δείγματα, κανάλια) float32 στο [-1, 1]."""
    import numpy as np
    a = frame.to_ndarray()
    layout = frame.layout
    ch = layout.nb_channels if hasattr(layout, "nb_channels") else len(layout.channels)
    a = a.T if frame.format.is_planar else a.reshape(-1, ch)
    if a.dtype.name in _SCALE:
        return a.astype(np.float32) / _SCALE[a.dtype.name]
    if a.dtype == np.uint8:
        return (a.astype(np.float32) - 128) / 128.0
    return a.astype(np.float32)


def fit_channels(a, nch):
    """Ίδιος αριθμός καναλιών (κόβει ή συμπληρώνει με σιωπή)."""
    import numpy as np
    if a.shape[1] == nch:
        return a
    out = np.zeros((len(a), nch), np.float32)
    k = min(nch, a.shape[1])
    out[:, :k] = a[:, :k]
    return out


def frame_at(container, stream, reel, t, exact=True):
    """
    Το καρέ εικόνας του reel τη στιγμή t (δευτ. από την αρχή του reel), ή None.
    exact=False: το πρώτο καρέ μετά το seek (αρκεί για codec όπου κάθε καρέ είναι κόμβος, π.χ. JPEG2000).
    """
    from fractions import Fraction
    mult = 2 if reel.stereo else 1
    target = Fraction((reel.pic_entry + int(max(0.0, t) * float(reel.rate))) * mult) / (reel.rate * mult)
    st0 = float((stream.start_time or 0) * stream.time_base)
    seek_seconds(container, stream, target)
    for fr in container.decode(stream):
        if exact and fr.time is not None and fr.time - st0 + 0.001 < float(target):
            continue
        return fr
    return None


def seek_seconds(container, stream, seconds):
    """Seek στον πλησιέστερο προηγούμενο κόμβο· αν δεν γίνεται, η ανάγνωση ξεκινά από την αρχή."""
    if seconds <= 0 or not stream.time_base:
        return
    try:
        container.seek(int(seconds / stream.time_base) + (stream.start_time or 0), stream=stream, backward=True)
    except Exception:  # μερικά MXF δεν δέχονται seek: τα δείγματα πριν από τον στόχο απλώς πετιούνται
        pass


def reel_audio(reel, rate, skip=0.0, limit=None, channels=None, stop=None):
    """
    Γεννήτρια με τον ήχο ενός reel, σε κομμάτια numpy (δείγματα, κανάλια) float32.
    Ξεκινά από το EntryPoint + skip δευτ. και δίνει το πολύ `limit` δευτ. (προεπιλογή: η διάρκεια του ήχου
    στο CPL μείον skip). channels: σταθερός αριθμός καναλιών. stop: threading.Event για διακοπή.
    Αν ο ήχος είναι κοντύτερος, δίνει λιγότερα δείγματα (ο καλών συμπληρώνει σιωπή αν χρειάζεται).
    """
    import av
    if not reel.snd:
        return
    if limit is None:
        limit = float(reel.snd_dur / reel.snd_rate) - skip
    need = int(round(max(0.0, limit) * rate))
    start_s = float(reel.snd_entry / reel.snd_rate) + skip
    start_sample = int(round(start_s * rate))
    with av.open(reel.snd) as c:
        st = c.streams.audio[0]
        a0 = float((st.start_time or 0) * st.time_base) if st.time_base else 0.0
        seek_seconds(c, st, start_s)
        pos = None
        for fr in c.decode(st):
            if need <= 0 or (stop is not None and stop.is_set()):
                return
            a = frame_to_float(fr)
            if channels:
                a = fit_channels(a, channels)
            fs = int(round((fr.time - a0) * rate)) if fr.time is not None else (pos if pos is not None else start_sample)
            pos = fs + len(a)
            if pos <= start_sample:
                continue
            if fs < start_sample:
                a = a[start_sample - fs:]
            a = a[:need]
            need -= len(a)
            yield a


class ReelPos:
    """Αρίθμηση καρέ ενός reel: δείκτης καρέ από timestamp (στα 3D δύο καρέ ανά εικόνα) και χρόνος ταινίας."""

    def __init__(self, reel, stream, offset_frames):
        self.reel = reel
        self.mult = 2 if reel.stereo else 1
        self.first = (reel.pic_entry + offset_frames) * self.mult
        self.last = (reel.pic_entry + reel.dur) * self.mult
        self.tb, self.st0 = stream.time_base, stream.start_time or 0
        self.frate = reel.rate * self.mult
        self.counter = self.first - 1

    def index(self, ts):
        return int(round((ts - self.st0) * self.tb * self.frate))

    def next_counter(self):
        """Για καρέ χωρίς timestamp: ο επόμενος αύξων αριθμός."""
        self.counter += 1
        return self.counter

    def time(self, idx):
        return self.reel.start + (idx // self.mult - self.reel.pic_entry) / float(self.reel.rate)


def reel_frames(reel, skip=0.0, limit=None, stop=None, lowres=None):
    """
    Γεννήτρια (χρόνος από την αρχή του reel, av.VideoFrame): από EntryPoint+skip, το πολύ limit δευτ.,
    μόνο το αριστερό μάτι στα 3D. lowres: '1'/'2' για γρήγορη αποκωδικοποίηση JPEG2000. Σταματά αν stop.is_set().
    """
    import av
    with av.open(reel.pic) as c:
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        if lowres and vs.codec_context.name == "jpeg2000":
            vs.codec_context.options = {"lowres": str(lowres)}
        pos = ReelPos(reel, vs, max(0, int(round(skip * float(reel.rate)))))
        last_t = reel.seconds if limit is None else min(reel.seconds, skip + limit)
        seek_seconds(c, vs, pos.first / pos.frate)
        for fr in c.decode(vs):
            if stop is not None and stop.is_set():
                return
            idx = pos.index(fr.pts) if fr.pts is not None else pos.next_counter()
            if idx < pos.first or (pos.mult == 2 and idx % 2):
                continue
            t = pos.time(idx) - reel.start
            if idx >= pos.last or t >= last_t:
                return
            yield t, fr
