"""Pembaca MCAP streaming + summary/indeks (PRD FR-10, FR-40, FR-11).

Port dari legacy: iter_records, u16, mstr. Perubahan:
- Streaming: hanya satu chunk yang berada di RAM pada satu waktu.
- Pesan di luar chunk (file tanpa chunk) ikut dibaca.
- Summary dibaca dari footer; bila tidak ada/rusak -> fallback pemindaian penuh.
- Kesalahan struktur memunculkan McapError dengan pesan jelas (tidak diam-diam).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

MAGIC = b"\x89MCAP0\r\n"

OP_HEADER, OP_FOOTER, OP_SCHEMA, OP_CHANNEL, OP_MESSAGE = 0x01, 0x02, 0x03, 0x04, 0x05
OP_CHUNK, OP_MESSAGE_INDEX, OP_CHUNK_INDEX = 0x06, 0x07, 0x08
OP_ATTACHMENT, OP_ATTACHMENT_INDEX, OP_STATISTICS = 0x09, 0x0A, 0x0B
OP_METADATA, OP_METADATA_INDEX, OP_SUMMARY_OFFSET, OP_DATA_END = 0x0C, 0x0D, 0x0E, 0x0F

_FOOTER_REC_LEN = 1 + 8 + (8 + 8 + 4)      # opcode + length + isi footer
_TAIL_LEN = _FOOTER_REC_LEN + len(MAGIC)


class McapError(ValueError):
    """File MCAP rusak atau tidak didukung."""


# ---------------------------------------------------------------- primitif
def u16(b, o): return struct.unpack_from("<H", b, o)[0], o + 2
def u32(b, o): return struct.unpack_from("<I", b, o)[0], o + 4
def u64(b, o): return struct.unpack_from("<Q", b, o)[0], o + 8


def mstr(b, o):
    n, o = u32(b, o)
    if o + n > len(b):
        raise McapError("string melewati batas record")
    return bytes(b[o:o + n]).decode("utf-8", "replace"), o + n


def mbytes32(b, o):
    n, o = u32(b, o)
    if o + n > len(b):
        raise McapError("bytes melewati batas record")
    return bytes(b[o:o + n]), o + n


def mbytes64(b, o):
    n, o = u64(b, o)
    if o + n > len(b):
        raise McapError("bytes melewati batas record")
    return b[o:o + n], o + n


def mmap_str(b, o):
    """map<string,string> berawalan panjang byte u32."""
    n, o = u32(b, o)
    end = o + n
    if end > len(b):
        raise McapError("map melewati batas record")
    out = {}
    while o < end:
        k, o = mstr(b, o)
        v, o = mstr(b, o)
        out[k] = v
    return out, end


# ---------------------------------------------------------------- tipe data
@dataclass
class Schema:
    id: int
    name: str
    encoding: str
    data: bytes = b""


@dataclass
class Channel:
    id: int
    schema_id: int
    topic: str
    message_encoding: str
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    channel_id: int
    sequence: int
    log_time: int
    publish_time: int
    data: bytes


@dataclass
class Summary:
    profile: str = ""
    library: str = ""
    schemas: dict = field(default_factory=dict)
    channels: dict = field(default_factory=dict)
    message_count: int | None = None
    message_start_time: int | None = None
    message_end_time: int | None = None
    channel_message_counts: dict = field(default_factory=dict)
    chunk_count: int | None = None
    attachment_count: int | None = None
    metadata_count: int | None = None
    compression: set = field(default_factory=set)
    from_index: bool = True            # False = hasil fallback pemindaian penuh


def parse_schema(rec) -> Schema:
    sid, o = u16(rec, 0)
    name, o = mstr(rec, o)
    enc, o = mstr(rec, o)
    data, o = mbytes32(rec, o)
    return Schema(sid, name, enc, data)


def parse_channel(rec) -> Channel:
    cid, o = u16(rec, 0)
    sid, o = u16(rec, o)
    topic, o = mstr(rec, o)
    menc, o = mstr(rec, o)
    meta, o = mmap_str(rec, o)
    return Channel(cid, sid, topic, menc, meta)


def parse_message(rec) -> Message:
    if len(rec) < 22:
        raise McapError("record Message terlalu pendek")
    cid, o = u16(rec, 0)
    seq, o = u32(rec, o)
    lt, o = u64(rec, o)
    pt, o = u64(rec, o)
    return Message(cid, seq, lt, pt, bytes(rec[o:]))


# ---------------------------------------------------------------- dekompresi
def _decompress(comp, inner, uncompressed_size):
    if comp == "":
        return inner
    if comp == "zstd":
        import zstandard
        return zstandard.ZstdDecompressor().decompress(bytes(inner), max_output_size=uncompressed_size)
    if comp == "lz4":
        import lz4.frame
        return lz4.frame.decompress(bytes(inner))
    raise McapError(f"kompresi chunk tidak didukung: {comp!r}")


def _iter_inner(buf):
    mv = memoryview(buf)
    o, n = 0, len(mv)
    while o < n:
        if o + 9 > n:
            raise McapError("record di dalam chunk terpotong")
        op = mv[o]
        (ln,) = struct.unpack_from("<Q", mv, o + 1)
        o += 9
        if o + ln > n:
            raise McapError("record di dalam chunk melewati batas")
        yield op, mv[o:o + ln]
        o += ln


# ---------------------------------------------------------------- pembaca inti
def _open_checked(path):
    f = path.open("rb")
    if f.read(len(MAGIC)) != MAGIC:
        f.close()
        raise McapError(f"{path.name} bukan file MCAP (magic tidak cocok)")
    return f


def iter_records(path, progress=None, expand_chunks=True) -> Iterator:
    """Hasilkan (opcode, isi) berurutan, streaming. Chunk dibuka (dekompresi) lalu
    record di dalamnya dihasilkan; di RAM hanya satu chunk. progress(done, total) dalam byte."""
    path = Path(path)
    total = path.stat().st_size
    with _open_checked(path) as f:
        if progress:
            progress(f.tell(), total)
        while True:
            head = f.read(9)
            if not head:
                return
            if len(head) < 9:
                raise McapError("header record terpotong (file terpotong?)")
            op = head[0]
            (ln,) = struct.unpack_from("<Q", head, 1)
            if op == OP_FOOTER:
                if progress:
                    progress(total, total)
                return
            rec = f.read(ln)
            if len(rec) != ln:
                raise McapError("isi record terpotong (file terpotong?)")
            if op == OP_CHUNK and expand_chunks:
                o = 8 + 8
                usize, o = u64(rec, o)
                o += 4                                  # crc
                comp, o = mstr(rec, o)
                inner, o = mbytes64(rec, o)
                yield from _iter_inner(_decompress(comp, inner, usize))
            else:
                yield op, rec
            if progress:
                progress(f.tell(), total)


def iter_messages(path, topics=None, progress=None) -> Iterator:
    """Hasilkan (channel, pesan). topics=None -> semua; selain itu set nama topik."""
    channels = {}
    want = set(topics) if topics is not None else None
    for op, rec in iter_records(path, progress=progress):
        if op == OP_CHANNEL:
            ch = parse_channel(rec)
            channels[ch.id] = ch
        elif op == OP_MESSAGE:
            cid = struct.unpack_from("<H", rec, 0)[0]
            ch = channels.get(cid)
            if ch is None:
                raise McapError(f"pesan merujuk channel {cid} yang belum didefinisikan")
            if want is None or ch.topic in want:
                yield ch, parse_message(rec)


# ---------------------------------------------------------------- summary
def _read_header(f):
    f.seek(len(MAGIC))
    head = f.read(9)
    if len(head) < 9 or head[0] != OP_HEADER:
        return "", ""
    (ln,) = struct.unpack_from("<Q", head, 1)
    rec = f.read(ln)
    prof, o = mstr(rec, 0)
    lib, o = mstr(rec, o)
    return prof, lib


def _summary_from_index(path):
    size = path.stat().st_size
    if size < len(MAGIC) * 2 + _FOOTER_REC_LEN:
        return None
    with _open_checked(path) as f:
        f.seek(size - _TAIL_LEN)
        tail = f.read(_TAIL_LEN)
        if tail[-len(MAGIC):] != MAGIC or tail[0] != OP_FOOTER:
            return None
        summary_start, _, _ = struct.unpack_from("<QQI", tail, 9)
        end = size - _TAIL_LEN
        if summary_start == 0 or not (len(MAGIC) < summary_start <= end):
            return None                                 # tidak ada / tidak valid
        s = Summary()
        s.profile, s.library = _read_header(f)
        f.seek(summary_start)
        pos = summary_start
        while pos < end:
            head = f.read(9)
            if len(head) < 9:
                return None
            op = head[0]
            (ln,) = struct.unpack_from("<Q", head, 1)
            rec = f.read(ln)
            if len(rec) != ln:
                return None
            pos += 9 + ln
            if op == OP_SCHEMA:
                sc = parse_schema(rec)
                s.schemas[sc.id] = sc
            elif op == OP_CHANNEL:
                ch = parse_channel(rec)
                s.channels[ch.id] = ch
            elif op == OP_STATISTICS:
                s.message_count, o = u64(rec, 0)
                _, o = u16(rec, o)                      # schema_count
                _, o = u32(rec, o)                      # channel_count
                s.attachment_count, o = u32(rec, o)
                s.metadata_count, o = u32(rec, o)
                s.chunk_count, o = u32(rec, o)
                s.message_start_time, o = u64(rec, o)
                s.message_end_time, o = u64(rec, o)
                n, o = u32(rec, o)
                stop = o + n
                while o < stop:
                    cid, o = u16(rec, o)
                    cnt, o = u64(rec, o)
                    s.channel_message_counts[cid] = cnt
            elif op == OP_CHUNK_INDEX:
                o = 16                                  # start+end time
                _, o = u64(rec, o)                      # chunk_start_offset
                _, o = u64(rec, o)                      # chunk_length
                n, o = u32(rec, o)                      # message_index_offsets (byte)
                o += n
                _, o = u64(rec, o)                      # message_index_length
                comp, o = mstr(rec, o)
                s.compression.add(comp)
        return s


def _summary_by_scan(path):
    s = Summary(from_index=False)
    with _open_checked(path) as f:
        s.profile, s.library = _read_header(f)
    counts, tmin, tmax, n = {}, None, None, 0
    for op, rec in iter_records(path):
        if op == OP_SCHEMA:
            sc = parse_schema(rec)
            s.schemas[sc.id] = sc
        elif op == OP_CHANNEL:
            ch = parse_channel(rec)
            s.channels[ch.id] = ch
        elif op == OP_MESSAGE:
            cid = struct.unpack_from("<H", rec, 0)[0]
            lt = struct.unpack_from("<Q", rec, 6)[0]
            counts[cid] = counts.get(cid, 0) + 1
            n += 1
            tmin = lt if tmin is None else min(tmin, lt)
            tmax = lt if tmax is None else max(tmax, lt)
    s.message_count, s.message_start_time, s.message_end_time = n, tmin, tmax
    s.channel_message_counts = counts
    return s


def read_summary(path, allow_scan=True) -> Summary:
    """Baca summary dari indeks di akhir file; fallback pemindaian penuh (FR-40)."""
    path = Path(path)
    try:
        s = _summary_from_index(path)
    except (McapError, struct.error):
        s = None
    if s is not None and s.channels:
        return s
    if not allow_scan:
        raise McapError("file tidak punya summary/indeks yang valid")
    return _summary_by_scan(path)
