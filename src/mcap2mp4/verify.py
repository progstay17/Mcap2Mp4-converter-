"""Verifikasi hasil (PRD FR-30..FR-32): parse ulang struktur MP4, hash aliran payload."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

U32_MAX = 0xFFFFFFFF
_CONTAINERS = {b"moov", b"mdia", b"minf", b"stbl", b"edts"}
_LEAVES = {b"stsz", b"stco", b"co64", b"stts", b"stss", b"stsc", b"mdhd", b"mvhd", b"hdlr", b"stsd", b"elst"}


def _boxes(buf, start, end):
    o = start
    while o + 8 <= end:
        size, typ = struct.unpack_from(">I4s", buf, o)
        hdr = 8
        if size == 1:
            if o + 16 > end:
                raise ValueError("largesize terpotong")
            size = struct.unpack_from(">Q", buf, o + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - o
        if size < hdr or o + size > end:
            raise ValueError(f"kotak {typ!r} melewati batas")
        yield typ, o + hdr, o + size
        o += size


def _top_level(f, file_size):
    """Daftar kotak tingkat atas tanpa memuat mdat: (tipe, awal_isi, akhir)."""
    out, o = [], 0
    while o + 8 <= file_size:
        f.seek(o)
        h = f.read(16)
        size, typ = struct.unpack_from(">I4s", h, 0)
        hdr = 8
        if size == 1:
            size = struct.unpack_from(">Q", h, 8)[0]
            hdr = 16
        elif size == 0:
            size = file_size - o
        if size < hdr or o + size > file_size:
            raise ValueError(f"kotak tingkat atas {typ!r} melewati akhir file (file terpotong?)")
        out.append((typ, o + hdr, o + size))
        o += size
    if o != file_size:
        raise ValueError("sisa byte setelah kotak terakhir")
    return out


def _parse_track(buf, t):
    """Ubah tabel mentah satu trak menjadi struktur sampel."""
    for need in (b"stsz", b"stts", b"stsc", b"mdhd"):
        if need not in t:
            raise ValueError(f"kotak {need.decode()} tidak ada")
    if b"stco" not in t and b"co64" not in t:
        raise ValueError("stco/co64 tidak ada")
    s, e = t[b"stsz"]
    fixed, count = struct.unpack_from(">II", buf, s + 4)
    sizes = None if fixed else list(struct.unpack_from(f">{count}I", buf, s + 12))
    use_co64 = b"co64" in t
    s, e = t[b"co64" if use_co64 else b"stco"]
    n_off = struct.unpack_from(">I", buf, s + 4)[0]
    offsets = list(struct.unpack_from(f">{n_off}{'Q' if use_co64 else 'I'}", buf, s + 8))
    s, e = t[b"stts"]
    n_ent = struct.unpack_from(">I", buf, s + 4)[0]
    stts = [struct.unpack_from(">II", buf, s + 8 + 8 * i) for i in range(n_ent)]
    s, e = t[b"stsc"]
    n_ent = struct.unpack_from(">I", buf, s + 4)[0]
    stsc = [struct.unpack_from(">III", buf, s + 8 + 12 * i) for i in range(n_ent)]
    s, e = t[b"mdhd"]
    if buf[s] == 1:
        timescale = struct.unpack_from(">I", buf, s + 20)[0]
        mdur = struct.unpack_from(">Q", buf, s + 24)[0]
    else:
        timescale, mdur = struct.unpack_from(">II", buf, s + 12)
    keys = struct.unpack_from(">I", buf, t[b"stss"][0] + 4)[0] if b"stss" in t else 0
    kind = buf[t[b"hdlr"][0] + 8: t[b"hdlr"][0] + 12] if b"hdlr" in t else b""
    tr = {"kind": kind.decode("latin1"), "offsets": offsets, "stts": stts, "stsc": stsc, "timescale": timescale,
          "media_duration": mdur, "co64": use_co64, "keyframes": keys, "stsz_fixed": fixed, "stsz_count": count}
    if b"stsd" in t:
        s = t[b"stsd"][0]
        tr["entry"] = buf[s + 12: s + 16].decode("latin1")
        if kind == b"soun":
            tr["channels"], tr["bits"] = struct.unpack_from(">HH", buf, s + 8 + 8 + 16)
    tr["empty_edit_ms"] = 0
    if b"elst" in t:
        s = t[b"elst"][0]
        ver, cnt = buf[s], struct.unpack_from(">I", buf, s + 4)[0]
        if cnt and ver == 0:
            dur, mt = struct.unpack_from(">Ii", buf, s + 8)
            tr["empty_edit_ms"] = dur if mt == -1 else 0
        elif cnt and ver == 1:
            dur, mt = struct.unpack_from(">Qq", buf, s + 8)
            tr["empty_edit_ms"] = dur if mt == -1 else 0
    # ukuran per sampel/chunk
    if fixed:                                       # audio: ukuran chunk dari stsc x ukuran sampel tetap
        chunk_samples, k = [], 0
        for i in range(n_off):
            while k + 1 < len(stsc) and stsc[k + 1][0] <= i + 1:
                k += 1
            chunk_samples.append(stsc[k][1])
        tr["chunk_samples"] = chunk_samples
        tr["sizes"] = [c * fixed for c in chunk_samples]          # satu entri per chunk
        tr["samples"] = count
        if sum(chunk_samples) != count:
            raise ValueError("jumlah sampel menurut stsc != stsz")
    else:
        tr["sizes"] = sizes
        tr["samples"] = count
    return tr


def parse_mp4(path) -> dict:
    """Struktur MP4: kunci tingkat atas = trak video pertama (kompatibel v0.2); 'audio' = daftar trak audio."""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as f:
        tops = _top_level(f, size)
        kinds = [t[0] for t in tops]
        if b"ftyp" not in kinds or b"moov" not in kinds or b"mdat" not in kinds:
            raise ValueError("ftyp/moov/mdat tidak lengkap")
        mdat = next(t for t in tops if t[0] == b"mdat")
        moov = next(t for t in tops if t[0] == b"moov")
        f.seek(moov[1])
        buf = f.read(moov[2] - moov[1])
    raw, movie = [], {}

    def walk(start, end, cur):
        for typ, s, e in _boxes(buf, start, end):
            if typ == b"trak":
                t = {}
                raw.append(t)
                walk(s, e, t)
            elif typ in _CONTAINERS:
                walk(s, e, cur)
            elif typ in _LEAVES:
                if cur is None:
                    movie.setdefault(typ, (s, e))
                else:
                    cur.setdefault(typ, (s, e))
    walk(0, len(buf), None)
    if b"mvhd" not in movie:
        raise ValueError("kotak mvhd tidak ada")
    tracks = [_parse_track(buf, t) for t in raw]
    vids = [t for t in tracks if t["kind"] == "vide"]
    if not vids:
        raise ValueError("tidak ada trak video")
    out = dict(vids[0])
    out.update(mdat=(mdat[1], mdat[2]), file_size=size, audio=[t for t in tracks if t["kind"] == "soun"],
               tracks=tracks)
    return out


def _ranges(m):
    """(awal, akhir) setiap sampel video dan chunk audio."""
    r = list(zip(m["offsets"], (o + z for o, z in zip(m["offsets"], m["sizes"]))))
    for a in m["audio"]:
        r += [(o, o + z) for o, z in zip(a["offsets"], a["sizes"])]
    return r


def quick(path, expected_frames: int | None = None, expected_audio: dict | None = None) -> dict:
    """Verifikasi cepat (FR-30, FR-97). expected_audio: {indeks_trak_audio: jumlah_sampel}."""
    errors = []
    try:
        m = parse_mp4(path)
    except (ValueError, struct.error, OSError) as exc:
        return {"ok": False, "errors": [f"struktur: {exc}"], "frames": None}
    n = len(m["sizes"])
    if len(m["offsets"]) != n:
        errors.append(f"jumlah offset ({len(m['offsets'])}) != jumlah stsz ({n})")
    if sum(c for c, _ in m["stts"]) != n:
        errors.append("jumlah sampel di stts != stsz")
    if expected_frames is not None and n != expected_frames:
        errors.append(f"jumlah frame {n} != {expected_frames}")
    ms, me = m["mdat"]
    for i, a in enumerate(m["audio"]):
        if len(a["offsets"]) != len(a["sizes"]):
            errors.append(f"audio {i}: jumlah offset chunk != jumlah chunk stsc")
        if sum(c * d for c, d in a["stts"]) != a["samples"]:
            errors.append(f"audio {i}: durasi stts != jumlah sampel")
        if expected_audio is not None and i in expected_audio and a["samples"] != expected_audio[i]:
            errors.append(f"audio {i}: jumlah sampel {a['samples']} != {expected_audio[i]}")
    if expected_audio and len(m["audio"]) < len(expected_audio):
        errors.append("track audio hilang")
    if n and len(m["offsets"]) == n:
        if not m["audio"]:
            if m["offsets"][0] < ms:
                errors.append("sampel pertama berada sebelum mdat")
            if m["offsets"][-1] + m["sizes"][-1] != me:
                errors.append("offset terakhir + ukuran != akhir mdat")
            if any(m["offsets"][i] + m["sizes"][i] != m["offsets"][i + 1] for i in range(n - 1)):
                errors.append("sampel tidak berurutan rapat di mdat")
        else:                                              # interleaved: tanpa tumpang-tindih dan di dalam mdat
            rg = sorted(_ranges(m))
            if rg[0][0] < ms or rg[-1][1] > me:
                errors.append("sampel di luar mdat")
            if any(rg[i][1] > rg[i + 1][0] for i in range(len(rg) - 1)):
                errors.append("sampel/chunk saling tumpang-tindih")
    dur = sum(c * d for c, d in m["stts"]) / m["timescale"]
    return {"ok": not errors, "errors": errors, "frames": n, "duration_s": dur, "co64": m["co64"],
            "keyframes": m["keyframes"], "timescale": m["timescale"],
            "audio": [{"samples": a["samples"], "rate": a["timescale"], "channels": a.get("channels"),
                       "entry": a.get("entry"), "empty_edit_ms": a["empty_edit_ms"]} for a in m["audio"]]}


def payload_sha256(path) -> tuple[str, int]:
    """Hash aliran payload sampel (FR-32), dibaca balik dari file hasil."""
    m = parse_mp4(path)
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for off, sz in zip(m["offsets"], m["sizes"]):
            f.seek(off)
            h.update(f.read(sz))
    return h.hexdigest(), len(m["sizes"])


def audio_payload_sha256(path, index=0) -> tuple[str, int]:
    """Hash PCM track audio ke-`index` (FR-97) dan jumlah sampelnya, dibaca balik dari file hasil."""
    m = parse_mp4(path)
    a = m["audio"][index]
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for off, sz in zip(a["offsets"], a["sizes"]):
            f.seek(off)
            h.update(f.read(sz))
    return h.hexdigest(), a["samples"]


def read_pairing(path):
    """Penanda pasangan kamera (kotak moov/udta/m2gp, JSON UTF-8) atau None. Hanya membaca moov: murah."""
    import json
    path = Path(path)
    try:
        with path.open("rb") as f:
            tops = _top_level(f, path.stat().st_size)
            moov = next((t for t in tops if t[0] == b"moov"), None)
            if moov is None:
                return None
            f.seek(moov[1])
            buf = f.read(moov[2] - moov[1])
        for typ, s0, e0 in _boxes(buf, 0, len(buf)):
            if typ == b"udta":
                for t2, s1, e1 in _boxes(buf, s0, e0):
                    if t2 == b"m2gp":
                        d = json.loads(bytes(buf[s1:e1]).decode("utf-8"))
                        return d if isinstance(d, dict) and d.get("group") else None
    except (ValueError, OSError, struct.error):
        return None
    return None
