"""Βοηθητικά για τα τεστ: μικρά DCP (μόνο XML και κενά MXF) και μικρά βίντεο φτιαγμένα με PyAV."""
import os
import uuid

SMPTE_CPL_NS = "http://www.smpte-ra.org/schemas/429-7/2006/CPL"


def new_id():
    return str(uuid.uuid4())


def write_assetmap(folder, assets):
    """assets: {id: όνομα αρχείου}."""
    items = "".join(f"<Asset><Id>urn:uuid:{aid}</Id><ChunkList><Chunk><Path>{name}</Path></Chunk></ChunkList></Asset>"
                    for aid, name in assets.items())
    with open(os.path.join(folder, "ASSETMAP.xml"), "w", encoding="utf-8") as f:
        f.write(f'<?xml version="1.0"?><AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">'
                f"<AssetList>{items}</AssetList></AssetMap>")


def write_cpl(folder, cpl_id, title, reels, rate="25 1"):
    """reels: [{"MainPicture": id, "MainSound": id, "MainMarkers": id, ...}] με διάρκεια 250 καρέ."""
    body = ""
    for reel in reels:
        assets = "".join(f"<{kind}><Id>urn:uuid:{aid}</Id><EditRate>{rate}</EditRate><IntrinsicDuration>250"
                         f"</IntrinsicDuration><EntryPoint>0</EntryPoint><Duration>250</Duration></{kind}>"
                         for kind, aid in reel.items())
        body += f"<Reel><Id>urn:uuid:{new_id()}</Id><AssetList>{assets}</AssetList></Reel>"
    path = os.path.join(folder, f"CPL_{cpl_id}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<?xml version="1.0"?><CompositionPlaylist xmlns="{SMPTE_CPL_NS}"><Id>urn:uuid:{cpl_id}</Id>'
                f"<ContentTitleText>{title}</ContentTitleText><ContentKind>feature</ContentKind>"
                f"<ReelList>{body}</ReelList></CompositionPlaylist>")
    return path


def touch(folder, name):
    open(os.path.join(folder, name), "wb").close()


def make_ov_vf(root):
    """OV με εικόνα+ήχο και VF δίπλα που παίρνει εικόνα/ήχο από το OV. Επιστρέφει (ov_dir, vf_dir, ids)."""
    ov, vf = os.path.join(root, "FILM_FTR_OV"), os.path.join(root, "FILM_FTR_VF")
    os.makedirs(ov)
    os.makedirs(vf)
    pic, snd, sub, markers = new_id(), new_id(), new_id(), new_id()
    touch(ov, "pic.mxf")
    touch(ov, "snd.mxf")
    touch(vf, "sub.mxf")
    write_assetmap(ov, {pic: "pic.mxf", snd: "snd.mxf"})
    write_assetmap(vf, {sub: "sub.mxf"})
    write_cpl(ov, new_id(), "FILM_FTR-25_S_PL-XX_51_2K_20260101_SMPTE_OV", [{"MainPicture": pic, "MainSound": snd}])
    write_cpl(vf, new_id(), "FILM_FTR-25_S_XX-EN_51_2K_20260101_SMPTE_VF",
              [{"MainMarkers": markers, "MainPicture": pic, "MainSound": snd, "MainSubtitle": sub}])
    return ov, vf, {"pic": pic, "snd": snd, "sub": sub, "markers": markers}


def make_video(path, seconds=1.0, size=(320, 180), fps=25, codec="libx264", audio=True, container=None):
    """Μικρό βίντεο με χρωματιστά καρέ και ήχο (ημίτονο)."""
    import av
    import numpy as np
    from fractions import Fraction
    with av.open(path, "w", format=container) as oc:
        v = oc.add_stream(codec, rate=fps)
        v.width, v.height = size
        v.pix_fmt = "yuv422p10le" if codec == "prores_ks" else "yuv420p"
        a = None
        if audio:
            a = oc.add_stream("aac" if codec != "prores_ks" else "pcm_s16le", rate=48000)
            a.layout = "stereo"
        n = int(seconds * fps)
        for i in range(n):
            img = np.zeros((size[1], size[0], 3), np.uint8)
            img[:, :, i % 3] = 200
            f = av.VideoFrame.from_ndarray(img, format="rgb24").reformat(format=v.pix_fmt)
            f.pts = i
            for p in v.encode(f):
                oc.mux(p)
        for p in v.encode():
            oc.mux(p)
        if a is not None:
            t = np.arange(int(seconds * 48000)) / 48000
            tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            fmt = "fltp" if a.codec_context.name == "aac" else "s16"
            step = 1024
            for k in range(0, len(tone), step):
                chunk = tone[k:k + step]
                data = np.vstack([chunk, chunk])
                if fmt == "s16":
                    data = (data.T * 32767).astype(np.int16).reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(data, format=fmt, layout="stereo")
                frame.sample_rate = 48000
                frame.pts, frame.time_base = k, Fraction(1, 48000)
                for p in a.encode(frame):
                    oc.mux(p)
            for p in a.encode():
                oc.mux(p)
    return path
