"""Penulis MP4 streaming (PRD FR-11, FR-12, FR-13, FR-44). Port dari legacy write_mp4 + perbaikan D1-D3.

- D1: sampel ditulis langsung ke mdat; di RAM hanya tabel (ukuran, offset, timestamp), tanpa isi frame.
- D2: co64 + mdat largesize (auto / always / off).
- D3: stts per-frame dari timestamp (RLE), atau konstan pecahan (mis. 30000/1001).
moov ditulis di akhir. Tata letak mode auto: [ftyp][free 8B][mdat 8B ...] -- bila hasil >4 GB,
16 byte (free+mdat) ditimpa menjadi satu header mdat largesize tanpa menggeser data.
"""
from __future__ import annotations

import hashlib
import struct
from array import array
from fractions import Fraction
from pathlib import Path

TIMESCALE = 90000
MOVIE_TS = 1000
U32_MAX = 0xFFFFFFFF
MAC_EPOCH_OFFSET = 2082844800          # detik 1904-01-01 -> 1970-01-01


class Mp4TooLarge(Exception):
    """co64 'off' dan data melewati batas 4 GB."""


# ---------------------------------------------------------------- kotak
def b16(v): return struct.pack(">H", v)
def b24(v): return v.to_bytes(3, "big")
def b32(v): return struct.pack(">I", v)
def b64(v): return struct.pack(">Q", v)
def box(t, p): return b32(len(p) + 8) + t + p
def fbox(t, v, fl, p): return box(t, bytes([v]) + b24(fl) + p)


_MATRIX = (b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x40000000))


def hvcc(info, params) -> bytes:
    p = bytearray()
    p.append(1)
    p.append((info.profile_space << 6) | (info.tier << 5) | info.profile_idc)
    p += b32(info.compat) + info.constraints
    p.append(info.level)
    p += b16(0xF000)                                   # min_spatial_segmentation = 0
    p += bytes([0xFC, 0xFC | (info.chroma & 3), 0xF8 | (info.luma_depth & 7), 0xF8 | (info.chroma_depth & 7)])
    p += b16(0)                                        # avgFrameRate: 0 = tidak ditentukan
    p.append(((info.temporal_layers & 7) << 3) | ((info.temporal_nested & 1) << 2) | 3)
    p.append(3)                                        # jumlah array (VPS, SPS, PPS)
    for t in (32, 33, 34):
        p.append(0x80 | t)
        p += b16(len(params[t]))
        for n in params[t]:
            p += b16(len(n)) + n
    return box(b"hvcC", bytes(p))


def _ftyp(): return box(b"ftyp", b"isom" + b32(0x200) + b"isomiso6mp41hev1")


def _mvhd(ctime, dur):
    if dur > U32_MAX:                                   # versi 1: durasi 64-bit
        return fbox(b"mvhd", 1, 0, b64(ctime) + b64(ctime) + b32(MOVIE_TS) + b64(dur) + b32(0x10000) + b16(0x100)
                    + b"\0" * 10 + _MATRIX + b"\0" * 24 + b32(2))
    return fbox(b"mvhd", 0, 0, b32(ctime) + b32(ctime) + b32(MOVIE_TS) + b32(dur) + b32(0x10000) + b16(0x100)
                + b"\0" * 10 + _MATRIX + b"\0" * 24 + b32(2))


def _tkhd(ctime, dur, w, h):
    tail = b"\0" * 8 + b16(0) + b16(0) + b16(0) + b16(0) + _MATRIX + b32(w << 16) + b32(h << 16)
    if dur > U32_MAX:
        return fbox(b"tkhd", 1, 7, b64(ctime) + b64(ctime) + b32(1) + b32(0) + b64(dur) + tail)
    return fbox(b"tkhd", 0, 7, b32(ctime) + b32(ctime) + b32(1) + b32(0) + b32(dur) + tail)


def _mdhd(ctime, dur):
    if dur > U32_MAX:
        return fbox(b"mdhd", 1, 0, b64(ctime) + b64(ctime) + b32(TIMESCALE) + b64(dur) + b16(0x55C4) + b16(0))
    return fbox(b"mdhd", 0, 0, b32(ctime) + b32(ctime) + b32(TIMESCALE) + b32(dur) + b16(0x55C4) + b16(0))


def _stsd(info, params):
    entry = (b"\0" * 6 + b16(1) + b"\0" * 16 + b16(info.width) + b16(info.height) + b32(0x480000) + b32(0x480000)
             + b32(0) + b16(1) + b"\0" * 32 + b16(24) + b16(0xFFFF) + hvcc(info, params))
    return fbox(b"stsd", 0, 0, b32(1) + box(b"hev1", entry))


def rle_stts(durations) -> tuple[bytes, int]:
    """(isi tabel stts, jumlah entri) dari daftar durasi per-sampel."""
    entries, last, run = [], None, 0
    for d in durations:
        if d == last:
            run += 1
        else:
            if run:
                entries.append((run, last))
            last, run = d, 1
    if run:
        entries.append((run, last))
    return b"".join(b32(c) + b32(d) for c, d in entries), len(entries)


class Mp4Writer:
    def __init__(self, path, co64="auto", creation_unix=0, progress=None):
        if co64 not in ("auto", "always", "off"):
            raise ValueError("co64 harus auto|always|off")
        self.path = Path(path)
        self.co64_mode = co64
        self.creation_unix = creation_unix
        self.sizes = array("I")
        self.offsets = array("Q")
        self.keys = array("I")                          # indeks sampel (basis 1) yang keyframe
        self.times = array("q")                         # timestamp per sampel dalam ns
        self.sha = hashlib.sha256()
        self._f = self.path.open("wb")
        self._f.write(_ftyp())
        self._free_pos = self._f.tell()
        if co64 == "always":
            self._f.write(b32(1) + b"mdat" + b64(0))     # header besar, ukuran ditambal saat tutup
            self._hdr_len = 16
        else:
            if co64 == "auto":
                self._f.write(box(b"free", b""))         # 8 byte cadangan
            self._f.write(b32(0) + b"mdat")
            self._hdr_len = 8
        self._mdat_pos = self._f.tell() - self._hdr_len
        self._data_start = self._f.tell()
        self._pos = self._data_start
        self.closed = False

    # -- penulisan sampel
    def add_sample(self, data: bytes, is_key: bool, t_ns: int):
        n = len(data)
        if self.co64_mode == "off" and self._pos + n + 8192 > U32_MAX:
            raise Mp4TooLarge("data melewati 4 GB sedangkan co64 dimatikan (Mati); ubah ke Otomatis/Selalu")
        self.sizes.append(n)
        self.offsets.append(self._pos)
        if is_key:
            self.keys.append(len(self.sizes))
        self.times.append(t_ns)
        self._f.write(data)
        self.sha.update(data)
        self._pos += n

    @property
    def count(self): return len(self.sizes)

    def times_strictly_increasing(self) -> bool:
        t = self.times
        return all(t[i + 1] > t[i] for i in range(len(t) - 1))

    @property
    def payload_bytes(self): return self._pos - self._data_start

    def truncate_samples(self, n: int):
        """Buang sampel setelah ke-n (kebijakan sinkron 'pangkas'); data mdat dipotong, hash dihitung ulang."""
        if n >= self.count:
            return
        end = self.offsets[n]
        del self.sizes[n:], self.offsets[n:], self.times[n:]
        self.keys = array("I", (k for k in self.keys if k <= n))
        self._f.flush()
        self._f.truncate(end)
        self._f.seek(end)
        self._pos = end
        self.sha = hashlib.sha256()                      # hitung ulang dari data yang tersisa
        with self.path.open("rb") as r:
            r.seek(self._data_start)
            left = end - self._data_start
            while left > 0:
                chunk = r.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.sha.update(chunk)
                left -= len(chunk)

    # -- penutupan
    def _stts(self, fps_fraction):
        n = self.count
        if fps_fraction is not None:                     # konstan (pecahan/bulat/session)
            fr = Fraction(fps_fraction)
            delta = max(1, round(TIMESCALE / fr))
            tbl, cnt = b32(n) + b32(delta), 1
            return tbl, cnt, n * delta, {"mode": "constant", "fps": float(fr), "delta": delta}
        t = self.times
        pts = [round((t[i] - t[0]) * TIMESCALE / 1e9) for i in range(n)]
        dur = [pts[i + 1] - pts[i] for i in range(n - 1)]
        fixed = sum(1 for d in dur if d <= 0)
        dur = [d if d > 0 else 1 for d in dur]           # durasi minimal 1 tick
        dur.append(dur[-1] if dur else TIMESCALE // 30)
        tbl, cnt = rle_stts(dur)
        total = sum(dur)
        return tbl, cnt, total, {"mode": "timestamp", "fps": round(n * TIMESCALE / total, 4) if total else None,
                                 "nonmonotonic_fixed": fixed}

    def finish(self, info, params, fps_fraction=None) -> dict:
        """Tulis moov dan tambal header mdat. Kembalikan statistik penulisan."""
        if self.closed:
            raise RuntimeError("sudah ditutup")
        if self.count == 0:
            raise ValueError("tidak ada sampel video")
        mdat_size = self._pos - self._mdat_pos
        large_mdat = self._hdr_len == 16
        if self.co64_mode == "auto" and mdat_size > U32_MAX:
            large_mdat = True
        use_co64 = self.co64_mode == "always" or (self.co64_mode == "auto" and self._pos > U32_MAX)
        if self.co64_mode == "off" and self._pos > U32_MAX:
            raise Mp4TooLarge("data melewati 4 GB sedangkan co64 dimatikan (Mati)")
        self.ctime = self.creation_unix + MAC_EPOCH_OFFSET if 0 < self.creation_unix < 2**32 - MAC_EPOCH_OFFSET else 0
        f = self._f
        f.flush()
        # tambal header mdat
        if large_mdat and self._hdr_len == 8:                         # auto: timpa free+mdat
            f.seek(self._free_pos)
            f.write(b32(1) + b"mdat" + b64(mdat_size + 8))
            mdat_size += 8                                            # data tidak bergeser
        elif self._hdr_len == 16:
            f.seek(self._mdat_pos)
            f.write(b32(1) + b"mdat" + b64(mdat_size))
        else:
            f.seek(self._mdat_pos)
            f.write(b32(mdat_size) + b"mdat")
        f.seek(0, 2)
        f.seek(self._pos)

        stts, nstts, media_dur, timing = self._stts(fps_fraction)
        n = self.count
        movie_dur = round(media_dur * MOVIE_TS / TIMESCALE)
        stss = fbox(b"stss", 0, 0, b32(len(self.keys)) + b"".join(b32(k) for k in self.keys))
        stsc = fbox(b"stsc", 0, 0, b32(1) + b32(1) + b32(1) + b32(1))
        stsz = fbox(b"stsz", 0, 0, b32(0) + b32(n) + b"".join(b32(s) for s in self.sizes))
        if use_co64:
            co = fbox(b"co64", 0, 0, b32(n) + b"".join(b64(o) for o in self.offsets))
        else:
            co = fbox(b"stco", 0, 0, b32(n) + b"".join(b32(o) for o in self.offsets))
        stbl = box(b"stbl", _stsd(info, params) + fbox(b"stts", 0, 0, b32(nstts) + stts) + stss + stsc + stsz + co)
        dref = fbox(b"dref", 0, 0, b32(1) + fbox(b"url ", 0, 1, b""))
        minf = box(b"minf", fbox(b"vmhd", 0, 1, b16(0) * 4) + box(b"dinf", dref) + stbl)
        mdhd = _mdhd(self.ctime, media_dur)
        hdlr = fbox(b"hdlr", 0, 0, b32(0) + b"vide" + b"\0" * 12 + b"VideoHandler\0")
        trak = box(b"trak", _tkhd(self.ctime, movie_dur, info.width, info.height) + box(b"mdia", mdhd + hdlr + minf))
        moov = box(b"moov", _mvhd(self.ctime, movie_dur) + trak)
        f.write(moov)
        f.flush()
        f.close()
        self.closed = True
        return {"frames": n, "keyframes": len(self.keys), "payload_bytes": self.payload_bytes,
                "co64": use_co64, "mdat_largesize": large_mdat, "duration_s": media_dur / TIMESCALE,
                "timing": timing, "sample_sha256": self.sha.hexdigest() if self.sha else None,
                "file_bytes": self._pos + len(moov)}

    def abort(self):
        if not self.closed:
            try:
                self._f.close()
            finally:
                self.closed = True
