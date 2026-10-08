"""Verifikasi hasil (PRD FR-30..FR-32): parse ulang struktur MP4, hash aliran payload."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

U32_MAX = 0xFFFFFFFF
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


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


def parse_mp4(path) -> dict:
    """Kembalikan struktur sampel video pertama; ValueError bila struktur tidak valid."""
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
    # telusuri moov
    tables, o_ctx = {}, 0

    def walk(start, end):
        for typ, s, e in _boxes(buf, start, end):
            if typ in _CONTAINERS:
                walk(s, e)
            elif typ in (b"stsz", b"stco", b"co64", b"stts", b"stss", b"stsc", b"mdhd", b"mvhd"):
                tables.setdefault(typ, (s, e))
    walk(0, len(buf))
    for need in (b"stsz", b"stts", b"stsc", b"mdhd", b"mvhd"):
        if need not in tables:
            raise ValueError(f"kotak {need.decode()} tidak ada")
    if b"stco" not in tables and b"co64" not in tables:
        raise ValueError("stco/co64 tidak ada")
    s, e = tables[b"stsz"]
    fixed, count = struct.unpack_from(">II", buf, s + 4)
    if fixed:
        sizes = [fixed] * count
    else:
        sizes = list(struct.unpack_from(f">{count}I", buf, s + 12))
    use_co64 = b"co64" in tables
    s, e = tables[b"co64" if use_co64 else b"stco"]
    n_off = struct.unpack_from(">I", buf, s + 4)[0]
    offsets = list(struct.unpack_from(f">{n_off}{'Q' if use_co64 else 'I'}", buf, s + 8))
    s, e = tables[b"stts"]
    n_ent = struct.unpack_from(">I", buf, s + 4)[0]
    stts = [struct.unpack_from(">II", buf, s + 8 + 8 * i) for i in range(n_ent)]
    s, e = tables[b"mdhd"]
    if buf[s] == 1:                                    # mdhd versi 1 (durasi 64-bit)
        timescale = struct.unpack_from(">I", buf, s + 20)[0]
        mdur = struct.unpack_from(">Q", buf, s + 24)[0]
    else:
        timescale, mdur = struct.unpack_from(">II", buf, s + 12)
    keys = 0
    if b"stss" in tables:
        keys = struct.unpack_from(">I", buf, tables[b"stss"][0] + 4)[0]
    return {"sizes": sizes, "offsets": offsets, "stts": stts, "timescale": timescale, "media_duration": mdur,
            "mdat": (mdat[1], mdat[2]), "co64": use_co64, "keyframes": keys, "file_size": size}


def quick(path, expected_frames: int | None = None) -> dict:
    """Verifikasi cepat (FR-30). Hasil: {ok, errors[], frames, duration_s, co64}."""
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
    if n and len(m["offsets"]) == n:
        ms, me = m["mdat"]
        if m["offsets"][0] < ms:
            errors.append("sampel pertama berada sebelum mdat")
        if m["offsets"][-1] + m["sizes"][-1] != me:
            errors.append("offset terakhir + ukuran != akhir mdat")
        if any(m["offsets"][i] + m["sizes"][i] != m["offsets"][i + 1] for i in range(n - 1)):
            errors.append("sampel tidak berurutan rapat di mdat")
    dur = sum(c * d for c, d in m["stts"]) / m["timescale"]
    return {"ok": not errors, "errors": errors, "frames": n, "duration_s": dur, "co64": m["co64"],
            "keyframes": m["keyframes"], "timescale": m["timescale"]}


def payload_sha256(path) -> tuple[str, int]:
    """Hash aliran payload sampel (FR-32), dibaca balik dari file hasil."""
    m = parse_mp4(path)
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for off, sz in zip(m["offsets"], m["sizes"]):
            f.seek(off)
            h.update(f.read(sz))
    return h.hexdigest(), len(m["sizes"])
