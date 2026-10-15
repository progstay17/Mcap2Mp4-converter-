"""Penulis MP4 streaming (PRD FR-11, FR-12, FR-13, FR-44). Port dari legacy write_mp4 + perbaikan D1-D3.

- D1: sampel ditulis langsung ke mdat; di RAM hanya tabel (ukuran, offset, timestamp), tanpa isi frame.
- D2: co64 + mdat largesize (auto / always / off).
- D3: stts per-frame dari timestamp (RLE), atau konstan pecahan (mis. 30000/1001).
- M5 (FR-92..96): track audio PCM (`ipcm`+`chnl`+`pcmC` atau `sowt`) di-interleave ke mdat menurut urutan penulisan
  (pemanggil menulis menurut log_time); tabel audio ringkas (stsz konstan, stts satu entri, stsc run-length, stco/co64).
  Offset awal tiap track terhadap track lain ditulis sebagai edit list (empty edit), skala film 1000.
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


def _mvhd(ctime, dur, next_id=2):
    if dur > U32_MAX:                                   # versi 1: durasi 64-bit
        return fbox(b"mvhd", 1, 0, b64(ctime) + b64(ctime) + b32(MOVIE_TS) + b64(dur) + b32(0x10000) + b16(0x100)
                    + b"\0" * 10 + _MATRIX + b"\0" * 24 + b32(next_id))
    return fbox(b"mvhd", 0, 0, b32(ctime) + b32(ctime) + b32(MOVIE_TS) + b32(dur) + b32(0x10000) + b16(0x100)
                + b"\0" * 10 + _MATRIX + b"\0" * 24 + b32(next_id))


def _tkhd(ctime, dur, w, h, track_id=1, volume=0):
    tail = b"\0" * 8 + b16(0) + b16(0) + b16(volume) + b16(0) + _MATRIX + b32(w << 16) + b32(h << 16)
    if dur > U32_MAX:
        return fbox(b"tkhd", 1, 7, b64(ctime) + b64(ctime) + b32(track_id) + b32(0) + b64(dur) + tail)
    return fbox(b"tkhd", 0, 7, b32(ctime) + b32(ctime) + b32(track_id) + b32(0) + b32(dur) + tail)


def _mdhd(ctime, dur, timescale=TIMESCALE):
    if dur > U32_MAX:
        return fbox(b"mdhd", 1, 0, b64(ctime) + b64(ctime) + b32(timescale) + b64(dur) + b16(0x55C4) + b16(0))
    return fbox(b"mdhd", 0, 0, b32(ctime) + b32(ctime) + b32(timescale) + b32(dur) + b16(0x55C4) + b16(0))


def _edts(delay, dur):
    """Edit list: empty edit sepanjang `delay` (skala film) lalu seluruh media. Kembalikan b'' bila delay <= 0."""
    if delay <= 0:
        return b""
    if max(delay, dur) > U32_MAX:
        body = (b32(2) + b64(delay) + struct.pack(">q", -1) + b16(1) + b16(0)
                + b64(dur) + struct.pack(">q", 0) + b16(1) + b16(0))
        return box(b"edts", fbox(b"elst", 1, 0, body))
    body = b32(2) + b32(delay) + struct.pack(">i", -1) + b16(1) + b16(0) + b32(dur) + struct.pack(">i", 0) + b16(1) + b16(0)
    return box(b"edts", fbox(b"elst", 0, 0, body))


def _audio_stsd(tag, rate, channels):
    """Sample entry audio PCM s16le: 'ipcm' (ISO/IEC 23003-5, + chnl + pcmC) atau 'sowt' (QuickTime)."""
    entry = (b"\0" * 6 + b16(1) + b16(0) + b16(0) + b32(0) + b16(channels) + b16(16) + b16(0) + b16(0)
             + b32((rate << 16) if rate < 65536 else 0))
    if tag == b"ipcm":
        if channels in (1, 2):                          # susunan standar (ISO 23001-8: 1 = mono, 2 = L/R)
            entry += fbox(b"chnl", 0, 0, bytes([1, channels]) + b64(0))
        entry += fbox(b"pcmC", 0, 0, bytes([1, 16]))   # format_flags=1 (little-endian), ukuran sampel 16 bit
    return fbox(b"stsd", 0, 0, b32(1) + box(tag, entry))


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


class _AudioTrack:
    def __init__(self, name, rate, channels, tag):
        self.name, self.rate, self.channels, self.tag = name, rate, channels, tag
        self.chunk_offsets = array("Q")
        self.chunk_frames = array("I")
        self.frames = 0
        self.sha = hashlib.sha256()

    @property
    def bytes_per_frame(self): return 2 * self.channels


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
        self.audio: list[_AudioTrack] = []
        self.orphan_bytes = 0                           # data video yang dipangkas tetapi masih ada di mdat
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

    # -- audio (FR-92, FR-96)
    def add_audio_track(self, name, rate, channels, codec_tag="ipcm") -> int:
        if codec_tag not in ("ipcm", "sowt"):
            raise ValueError("audio_codec_tag harus ipcm|sowt")
        self.audio.append(_AudioTrack(name, int(rate), int(channels), codec_tag.encode()))
        return len(self.audio) - 1

    def add_audio_block(self, idx: int, data: bytes):
        """Tulis satu blok PCM (kelipatan satu frame) sebagai satu chunk; PCM disalin apa adanya."""
        tr = self.audio[idx]
        n = len(data)
        if n == 0:
            return
        if n % tr.bytes_per_frame:
            raise ValueError("blok audio bukan kelipatan frame PCM")
        if self.co64_mode == "off" and self._pos + n + 8192 > U32_MAX:
            raise Mp4TooLarge("data melewati 4 GB sedangkan co64 dimatikan (Mati); ubah ke Otomatis/Selalu")
        tr.chunk_offsets.append(self._pos)
        tr.chunk_frames.append(n // tr.bytes_per_frame)
        tr.frames += n // tr.bytes_per_frame
        self._f.write(data)
        tr.sha.update(data)
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
        if self.audio:                                   # mdat berisi audio sesudah frame ke-n: jangan dipotong
            self.orphan_bytes += sum(self.sizes[n:])
            del self.sizes[n:], self.offsets[n:], self.times[n:]
            self.keys = array("I", (k for k in self.keys if k <= n))
            self._f.flush()
            self.sha = hashlib.sha256()
            with self.path.open("rb") as r:
                for off, sz in zip(self.offsets, self.sizes):
                    r.seek(off)
                    self.sha.update(r.read(sz))
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

    def finish(self, info, params, fps_fraction=None, video_delay_ms=0.0, audio_delays_ms=None, udta: bytes = b"") -> dict:
        """Tulis moov dan tambal header mdat. Kembalikan statistik penulisan.

        video_delay_ms / audio_delays_ms: offset awal tiap track (>= 0) terhadap awal file, ditulis sebagai empty edit."""
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
        v_delay = max(0, round(float(video_delay_ms) * MOVIE_TS / 1000))
        v_dur = round(media_dur * MOVIE_TS / TIMESCALE)
        movie_dur = v_delay + v_dur
        stss = fbox(b"stss", 0, 0, b32(len(self.keys)) + b"".join(b32(k) for k in self.keys))
        stsc = fbox(b"stsc", 0, 0, b32(1) + b32(1) + b32(1) + b32(1))
        stsz = fbox(b"stsz", 0, 0, b32(0) + b32(n) + b"".join(b32(s) for s in self.sizes))
        co = self._co(self.offsets, use_co64)
        stbl = box(b"stbl", _stsd(info, params) + fbox(b"stts", 0, 0, b32(nstts) + stts) + stss + stsc + stsz + co)
        dref = fbox(b"dref", 0, 0, b32(1) + fbox(b"url ", 0, 1, b""))
        minf = box(b"minf", fbox(b"vmhd", 0, 1, b16(0) * 4) + box(b"dinf", dref) + stbl)
        mdhd = _mdhd(self.ctime, media_dur)
        hdlr = fbox(b"hdlr", 0, 0, b32(0) + b"vide" + b"\0" * 12 + b"VideoHandler\0")
        trak = box(b"trak", _tkhd(self.ctime, movie_dur, info.width, info.height) + _edts(v_delay, v_dur)
                   + box(b"mdia", mdhd + hdlr + minf))
        traks, audio_stats = [trak], []
        delays = list(audio_delays_ms or [])
        for i, tr in enumerate(a for a in self.audio if a.frames):
            d_ms = delays[self.audio.index(tr)] if self.audio.index(tr) < len(delays) else 0.0
            a_delay = max(0, round(float(d_ms) * MOVIE_TS / 1000))
            a_dur = round(tr.frames * MOVIE_TS / tr.rate)
            movie_dur = max(movie_dur, a_delay + a_dur)
            nch = len(tr.chunk_offsets)
            runs, last = [], None
            for k, c in enumerate(tr.chunk_frames):
                if c != last:
                    runs.append((k + 1, c))
                    last = c
            a_stsc = fbox(b"stsc", 0, 0, b32(len(runs)) + b"".join(b32(f) + b32(c) + b32(1) for f, c in runs))
            a_stbl = box(b"stbl", _audio_stsd(tr.tag, tr.rate, tr.channels) + fbox(b"stts", 0, 0, b32(1) + b32(tr.frames) + b32(1))
                         + a_stsc + fbox(b"stsz", 0, 0, b32(tr.bytes_per_frame) + b32(tr.frames))
                         + self._co(tr.chunk_offsets, use_co64))
            a_minf = box(b"minf", fbox(b"smhd", 0, 0, b16(0) + b16(0)) + box(b"dinf", dref) + a_stbl)
            a_hdlr = fbox(b"hdlr", 0, 0, b32(0) + b"soun" + b"\0" * 12 + b"SoundHandler\0")
            traks.append(box(b"trak", _tkhd(self.ctime, a_delay + a_dur, 0, 0, track_id=2 + i, volume=0x100)
                             + _edts(a_delay, a_dur)
                             + box(b"mdia", _mdhd(self.ctime, tr.frames, tr.rate) + a_hdlr + a_minf)))
            audio_stats.append({"name": tr.name, "samples": tr.frames, "chunks": nch, "sample_rate": tr.rate,
                                "channels": tr.channels, "codec_tag": tr.tag.decode(), "delay_ms": a_delay * 1000 / MOVIE_TS,
                                "duration_s": tr.frames / tr.rate, "sha256": tr.sha.hexdigest()})
        user = box(b"udta", box(b"m2gp", udta)) if udta else b""          # penanda pasangan kamera (opsional); pemutar lain mengabaikannya
        moov = box(b"moov", _mvhd(self.ctime, movie_dur, 2 + len(traks) - 1) + b"".join(traks) + user)
        f.write(moov)
        f.flush()
        f.close()
        self.closed = True
        return {"frames": n, "keyframes": len(self.keys), "payload_bytes": self.payload_bytes,
                "co64": use_co64, "mdat_largesize": large_mdat, "duration_s": media_dur / TIMESCALE,
                "timing": timing, "sample_sha256": self.sha.hexdigest() if self.sha else None,
                "file_bytes": self._pos + len(moov), "video_delay_ms": v_delay * 1000 / MOVIE_TS,
                "orphan_bytes": self.orphan_bytes, "audio": audio_stats}

    @staticmethod
    def _co(offsets, use_co64):
        n = len(offsets)
        if use_co64:
            return fbox(b"co64", 0, 0, b32(n) + b"".join(b64(o) for o in offsets))
        return fbox(b"stco", 0, 0, b32(n) + b"".join(b32(o) for o in offsets))

    def abort(self):
        if not self.closed:
            try:
                self._f.close()
            finally:
                self.closed = True
