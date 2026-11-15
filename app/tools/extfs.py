"""
Ανάγνωση συστημάτων αρχείων ext2 / ext3 / ext4 (δίσκοι DCP για servers αιθουσών) ΜΟΝΟ ΓΙΑ ΑΝΑΓΝΩΣΗ,
χωρίς οδηγό (driver): από αρχείο εικόνας δίσκου ή απευθείας από φυσικό δίσκο (\\\\.\\PhysicalDriveN,
χρειάζεται δικαιώματα διαχειριστή). Δεν γράφει ποτέ τίποτα στον δίσκο.

Υποστηρίζει: block maps (ext2/ext3: άμεσα, έμμεσα, διπλά, τριπλά έμμεσα blocks), extents (ext4),
group descriptors 32/64 bit, κατάλογοι (γραμμική ανάγνωση, δουλεύει και με htree), αραιά αρχεία.
"""
import os
import stat
import struct

from app.core.config import NO_WINDOW

EXT_MAGIC = 0xEF53
INCOMPAT_EXTENTS = 0x40
INCOMPAT_64BIT = 0x80
EXTENTS_FL = 0x80000
EXTENT_MAGIC = 0xF30A
SECTOR = 4096  # ανάγνωση φυσικών δίσκων σε ευθυγραμμισμένα κομμάτια


class ExtError(Exception):
    pass


class AlignedReader:
    """Οι φυσικοί δίσκοι στα Windows δέχονται μόνο ευθυγραμμισμένες αναγνώσεις (πολλαπλάσια του τομέα)."""

    def __init__(self, f, align=SECTOR, cache_blocks=64):
        self.f, self.align = f, align
        self.cache, self.order, self.cache_blocks = {}, [], cache_blocks

    def _chunk(self, idx):
        if idx in self.cache:
            return self.cache[idx]
        self.f.seek(idx * self.align)
        data = self.f.read(self.align)
        if len(data) < self.align:
            data = data + b"\0" * (self.align - len(data))
        self.cache[idx] = data
        self.order.append(idx)
        if len(self.order) > self.cache_blocks:
            self.cache.pop(self.order.pop(0), None)
        return data

    def read_at(self, offset, size):
        if size <= 0:
            return b""
        first, last = offset // self.align, (offset + size - 1) // self.align
        if last - first > 8:  # μεγάλη ανάγνωση: απευθείας, ευθυγραμμισμένη, χωρίς cache
            start = first * self.align
            self.f.seek(start)
            data = self.f.read((last - first + 1) * self.align)
            return data[offset - start:offset - start + size].ljust(size, b"\0")
        buf = b"".join(self._chunk(i) for i in range(first, last + 1))
        s = offset - first * self.align
        return buf[s:s + size]


class Inode:
    def __init__(self, num, raw):
        self.num = num
        (self.mode,) = struct.unpack_from("<H", raw, 0)
        size_lo, = struct.unpack_from("<I", raw, 4)
        size_hi, = struct.unpack_from("<I", raw, 108)
        self.size = size_lo | (size_hi << 32 if stat.S_ISREG(self.mode) else 0)
        self.mtime, = struct.unpack_from("<I", raw, 16)
        self.flags, = struct.unpack_from("<I", raw, 32)
        self.block = raw[40:100]  # i_block[15]

    @property
    def is_dir(self):
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self):
        return stat.S_ISREG(self.mode)


class ExtFS:
    """Σύστημα αρχείων ext σε ένα αρχείο / δίσκο, από τη θέση offset (αρχή του partition)."""

    def __init__(self, f, offset=0):
        self.dev = f if isinstance(f, AlignedReader) else AlignedReader(f)
        self.offset = offset
        sb = self._read(1024, 1024)
        magic, = struct.unpack_from("<H", sb, 56)
        if magic != EXT_MAGIC:
            raise ExtError("Δεν είναι σύστημα αρχείων ext2/ext3/ext4")
        self.inodes_count, self.blocks_lo = struct.unpack_from("<II", sb, 0)
        log_block, = struct.unpack_from("<I", sb, 24)
        self.block_size = 1024 << log_block
        self.blocks_per_group, = struct.unpack_from("<I", sb, 32)
        self.inodes_per_group, = struct.unpack_from("<I", sb, 40)
        self.first_data_block, = struct.unpack_from("<I", sb, 20)
        rev, = struct.unpack_from("<I", sb, 76)
        self.inode_size = struct.unpack_from("<H", sb, 88)[0] if rev >= 1 else 128
        self.feature_incompat, = struct.unpack_from("<I", sb, 96)
        self.feature_compat, = struct.unpack_from("<I", sb, 92)
        desc_size = struct.unpack_from("<H", sb, 254)[0]
        self.desc_size = desc_size if (self.feature_incompat & INCOMPAT_64BIT) and desc_size >= 64 else 32
        self.label = sb[120:136].split(b"\0")[0].decode("utf-8", "replace")
        self.version = self._version()
        self._gd_cache = {}

    def _version(self):
        if self.feature_incompat & INCOMPAT_EXTENTS:
            return "ext4"
        return "ext3" if self.feature_compat & 0x4 else "ext2"  # has_journal

    # ------------------------------------------------------------------ χαμηλό επίπεδο
    def _read(self, pos, size):
        return self.dev.read_at(self.offset + pos, size)

    def read_block(self, n, count=1):
        return self._read(n * self.block_size, self.block_size * count)

    def _inode_table(self, group):
        if group not in self._gd_cache:
            gdt_block = self.first_data_block + 1
            raw = self._read(gdt_block * self.block_size + group * self.desc_size, self.desc_size)
            lo, = struct.unpack_from("<I", raw, 8)
            hi = struct.unpack_from("<I", raw, 40)[0] if self.desc_size >= 64 else 0
            self._gd_cache[group] = lo | (hi << 32)
        return self._gd_cache[group]

    def inode(self, num):
        if num < 1 or num > self.inodes_count:
            raise ExtError(f"Λάθος inode {num}")
        group, index = divmod(num - 1, self.inodes_per_group)
        pos = self._inode_table(group) * self.block_size + index * self.inode_size
        return Inode(num, self._read(pos, max(128, min(self.inode_size, 256))))

    # ------------------------------------------------------------------ blocks αρχείου
    def _extent_runs(self, node):
        """(λογικό block, φυσικό block, πλήθος) από δέντρο extents (ext4)."""
        magic, entries, _max, depth = struct.unpack_from("<HHHH", node, 0)
        if magic != EXTENT_MAGIC:
            raise ExtError("Χαλασμένο δέντρο extents")
        for i in range(entries):
            e = 12 + i * 12
            if depth == 0:
                lblk, length, hi, lo = struct.unpack_from("<IHHI", node, e)
                if length > 32768:  # μη αρχικοποιημένο extent: διαβάζεται ως μηδενικά
                    continue
                yield lblk, (hi << 32) | lo, length
            else:
                _lblk, lo, hi = struct.unpack_from("<IIH", node, e)
                yield from self._extent_runs(self.read_block((hi << 32) | lo))

    def _blockmap_runs(self, ino, nblocks):
        """(λογικό, φυσικό, 1) από τους δείκτες ext2/ext3 (άμεσοι, έμμεσοι)."""
        ptrs = struct.unpack("<15I", ino.block)
        per = self.block_size // 4
        logical = 0

        def walk(block, level):
            nonlocal logical
            if logical >= nblocks:
                return
            if block == 0:  # τρύπα (αραιό αρχείο)
                logical += per ** level
                return
            if level == 0:
                yield logical, block, 1
                logical += 1
                return
            for p in struct.unpack(f"<{per}I", self.read_block(block)):
                if logical >= nblocks:
                    return
                yield from walk(p, level - 1)

        for p in ptrs[:12]:
            yield from walk(p, 0)
        for level, p in ((1, ptrs[12]), (2, ptrs[13]), (3, ptrs[14])):
            yield from walk(p, level)

    def runs(self, ino):
        nblocks = (ino.size + self.block_size - 1) // self.block_size
        if ino.flags & EXTENTS_FL:
            return sorted(self._extent_runs(ino.block))
        out, cur = [], None
        for lb, pb, _n in self._blockmap_runs(ino, nblocks):  # συνένωση συνεχόμενων blocks
            if cur and cur[0] + cur[2] == lb and cur[1] + cur[2] == pb:
                cur[2] += 1
            else:
                if cur:
                    out.append(tuple(cur))
                cur = [lb, pb, 1]
        if cur:
            out.append(tuple(cur))
        return out

    def read_chunks(self, ino, chunk=8 * 1024 * 1024):
        """Γεννήτρια με τα bytes του αρχείου, σε κομμάτια (οι τρύπες δίνουν μηδενικά)."""
        bs, remaining, pos = self.block_size, ino.size, 0
        for lb, pb, n in self.runs(ino):
            if remaining <= 0:
                break
            start = lb * bs
            if start > pos:  # τρύπα
                gap = min(start - pos, remaining)
                yield b"\0" * gap
                pos += gap
                remaining -= gap
            done = 0
            while done < n and remaining > 0:
                take = min(n - done, max(1, chunk // bs))
                data = self.read_block(pb + done, take)[:min(take * bs, remaining)]
                yield data
                pos += len(data)
                remaining -= len(data)
                done += take
        if remaining > 0:
            yield b"\0" * remaining

    # ------------------------------------------------------------------ κατάλογοι
    def listdir(self, ino):
        """[(όνομα, inode)] ενός καταλόγου (χωρίς . και ..)."""
        data = b"".join(self.read_chunks(ino))
        out, pos = [], 0
        while pos + 8 <= len(data):
            inode, rec_len, name_len = struct.unpack_from("<IHB", data, pos)
            if rec_len < 8:
                break
            if inode:
                name = data[pos + 8:pos + 8 + name_len].decode("utf-8", "replace")
                if name not in (".", ".."):
                    out.append((name, inode))
            pos += rec_len
        return out

    def lookup(self, path):
        ino = self.inode(2)  # ρίζα
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            entry = next((n for name, n in self.listdir(ino) if name == part), None)
            if entry is None:
                raise ExtError(f"Δεν βρέθηκε: {path}")
            ino = self.inode(entry)
        return ino

    def walk(self, path="/", max_depth=6):
        """Σαν os.walk: (διαδρομή, [φάκελοι], [(αρχείο, inode)])."""
        stack = [(path.rstrip("/") or "/", self.lookup(path), 0)]
        while stack:
            p, ino, depth = stack.pop()
            dirs, files = [], []
            for name, num in self.listdir(ino):
                child = self.inode(num)
                if child.is_dir:
                    dirs.append(name)
                    if depth < max_depth:
                        stack.append((f"{p.rstrip('/')}/{name}", child, depth + 1))
                elif child.is_file:
                    files.append((name, child))
            yield p, dirs, files

    def find_dcps(self, max_depth=4):
        """Φάκελοι που περιέχουν ASSETMAP: [(διαδρομή, μέγεθος, πλήθος αρχείων)]."""
        out = []
        for p, _dirs, files in self.walk("/", max_depth):
            if any(name.upper().startswith("ASSETMAP") for name, _ in files):
                size = sum(i.size for _n, i in files)
                out.append((p, size, len(files)))
        return sorted(out)

    def copy_tree(self, src, dest, progress=None, cancel=None, chunk=8 * 1024 * 1024):
        """Αντιγραφή φακέλου (με υποφακέλους) από το ext στον δίσκο των Windows. Επιστρέφει [(αρχείο, μέγεθος)]."""
        jobs = []
        for p, _dirs, files in self.walk(src, 8):
            rel = os.path.relpath(p, src) if p != src else ""
            for name, ino in files:
                jobs.append((os.path.join(dest, rel, name), ino))
        total = sum(ino.size for _p, ino in jobs) or 1
        done = 0
        out = []
        for path, ino in jobs:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                for data in self.read_chunks(ino, chunk):
                    if cancel is not None and cancel.is_set():
                        f.close()
                        os.remove(tmp)
                        from app.core.util import Cancelled
                        raise Cancelled()
                    f.write(data)
                    done += len(data)
                    if progress:
                        progress(done / total, f"Αντιγραφή από Linux: {os.path.basename(path)[:40]}")
            os.replace(tmp, path)
            try:
                os.utime(path, (ino.mtime, ino.mtime))
            except OSError:
                pass
            out.append((path, ino.size))
        return out


# ======================================================================== ΔΙΣΚΟΙ ΤΩΝ WINDOWS
LINUX_GPT = "0fc63daf-8483-4772-8e79-3d69d8477de4"
_LINUX_GPT_BYTES = bytes.fromhex("af3dc60f838472478e793d69d8477de4")  # το ίδιο GUID όπως γράφεται στον δίσκο


def ext_offsets(reader):
    """
    Θέσεις (bytes) όπου υπάρχει σύστημα ext σε μια εικόνα δίσκου: χωρίς partitions (0), ή μέσα από
    πίνακα MBR / GPT. reader: AlignedReader.
    """
    def is_ext(off):
        return struct.unpack_from("<H", reader.read_at(off + 1024 + 56, 2))[0] == EXT_MAGIC

    out = []
    if is_ext(0):
        return [0]
    mbr = reader.read_at(0, 512)
    if mbr[510:512] == b"\x55\xaa":
        for i in range(4):
            e = 446 + i * 16
            ptype, lba = mbr[e + 4], struct.unpack_from("<I", mbr, e + 8)[0]
            if ptype == 0xEE:  # προστατευτικό MBR: ο πίνακας είναι GPT
                hdr = reader.read_at(512, 92)
                if hdr[:8] == b"EFI PART":
                    entries_lba, count, size = struct.unpack_from("<QII", hdr, 72)
                    table = reader.read_at(entries_lba * 512, count * size)
                    for k in range(count):
                        ent = table[k * size:(k + 1) * size]
                        first = struct.unpack_from("<Q", ent, 32)[0]
                        if ent[:16] == _LINUX_GPT_BYTES or (first and is_ext(first * 512)):
                            out.append(first * 512)
            elif ptype and lba and is_ext(lba * 512):
                out.append(lba * 512)
    if not out:  # χωρίς πίνακα (ή χαλασμένο): οι συνηθισμένες θέσεις αρχής partition
        out = [o for o in (1 << 20, 63 * 512, 2 << 20, 4 << 20) if is_ext(o)]
    return sorted({o for o in out if is_ext(o)})


def list_linux_partitions():
    """
    Partitions τύπου Linux (MBR 0x83 ή GPT Linux) από το PowerShell (χωρίς δικαιώματα διαχειριστή):
    [{'disk', 'partition', 'offset', 'size', 'model', 'bus'}].
    """
    import json
    import subprocess
    ps = ("Get-Partition | ForEach-Object { $d = Get-Disk -Number $_.DiskNumber; "
          "[pscustomobject]@{disk=$_.DiskNumber; partition=$_.PartitionNumber; offset=$_.Offset; size=$_.Size; "
          "mbr=$_.MbrType; gpt=\"$($_.GptType)\"; letter=\"$($_.DriveLetter)\"; model=$d.FriendlyName; "
          "bus=\"$($d.BusType)\"} } | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW)
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    rows = rows if isinstance(rows, list) else [rows]
    out = []
    for p in rows:
        gpt = (p.get("gpt") or "").strip("{}").lower()
        if p.get("mbr") == 0x83 or gpt == LINUX_GPT:
            out.append({k: p.get(k) for k in ("disk", "partition", "offset", "size", "model", "bus")})
    return out


def open_partition(disk, offset):
    """ExtFS πάνω σε φυσικό δίσκο (θέλει δικαιώματα διαχειριστή). Ο δίσκος ανοίγει ΜΟΝΟ για ανάγνωση."""
    f = open(rf"\\.\PhysicalDrive{int(disk)}", "rb", buffering=0)
    return ExtFS(AlignedReader(f), int(offset)), f


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def unique_targets(dest, paths):
    """
    Φάκελοι προορισμού για DCP από δίσκο Linux. Αν δύο έχουν το ίδιο όνομα (σε άλλους φακέλους του δίσκου),
    στο δεύτερο μπαίνει και ο γονικός φάκελος, ώστε να μην ανακατευτούν.
    """
    out, used = [], set()
    for p in paths:
        parts = p.strip("/").split("/")
        name = parts[-1]
        if name.lower() in used or os.path.exists(os.path.join(dest, name)):
            name = f"{parts[-1]} ({parts[-2] if len(parts) > 1 else 'δίσκος'})"
        n, base = 2, name
        while name.lower() in used:
            name, n = f"{base} {n}", n + 1
        used.add(name.lower())
        out.append(os.path.join(dest, name))
    return out
