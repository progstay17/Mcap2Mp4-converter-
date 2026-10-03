from __future__ import annotations

import argparse
import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path


CAMS = {"/ego/camera/0": "camera_0", "/ego/camera/1": "camera_1"}


def b16(v): return struct.pack(">H", v)
def b24(v): return v.to_bytes(3, "big")
def b32(v): return struct.pack(">I", v)
def box(t, p): return b32(len(p) + 8) + t + p
def fbox(t, v, fl, p): return box(t, bytes([v]) + b24(fl) + p)
def u16(b, o): return struct.unpack_from("<H", b, o)[0], o + 2
def u32(b, o): return struct.unpack_from("<I", b, o)[0], o + 4
def u64(b, o): return struct.unpack_from("<Q", b, o)[0], o + 8


def mstr(b, o):
    n, o = u32(b, o)
    return b[o:o + n].decode("utf-8", "replace"), o + n


def mbytes(b, o):
    n, o = u64(b, o)
    return b[o:o + n], o + n


def skip_map(b, o):
    n, o = u32(b, o)
    return o + n


def varint(b, o):
    v = 0
    s = 0
    while True:
        c = b[o]
        o += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, o
        s += 7


def compressed_video_payload(msg):
    o = 0
    data = None
    fmt = None
    while o < len(msg):
        key, o = varint(msg, o)
        field = key >> 3
        wt = key & 7
        if wt == 0:
            _, o = varint(msg, o)
            continue
        if wt == 1:
            val = msg[o:o + 8]
            o += 8
        elif wt == 2:
            n, o = varint(msg, o)
            val = msg[o:o + n]
            o += n
        elif wt == 5:
            val = msg[o:o + 4]
            o += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wt}")
        if wt == 2 and field == 3:
            data = val
        elif wt == 2 and field == 4:
            fmt = val.decode("utf-8", "replace").lower()
    return data, fmt


def iter_records(path, progress=None):
    total_bytes = path.stat().st_size
    if progress: progress(0, total_bytes)
    with path.open("rb") as f:
        if f.read(8) != b"\x89MCAP0\r\n":
            raise ValueError(f"{path} is not an MCAP file")
        while True:
            if progress: progress(f.tell(), total_bytes)
            op = f.read(1)
            if not op:
                return
            raw_len = f.read(8)
            if len(raw_len) != 8:
                return
            rec = f.read(struct.unpack("<Q", raw_len)[0])
            if op[0] != 0x06:
                continue
            o = 8 + 8 + 8 + 4
            comp, o = mstr(rec, o)
            inner, o = mbytes(rec, o)
            if comp == "zstd":
                import zstandard
                inner = zstandard.ZstdDecompressor().decompress(
                    inner, max_output_size=struct.unpack_from("<Q", rec, 16)[0])
            elif comp == "lz4":
                import lz4.frame
                inner = lz4.frame.decompress(inner)
            elif comp:
                raise ValueError(f"unsupported compressed chunk: {comp}")
            io = 0
            while io < len(inner):
                iop = inner[io]
                io += 1
                ilen = struct.unpack_from("<Q", inner, io)[0]
                io += 8
                yield iop, inner[io:io + ilen]
                io += ilen
                if progress: progress(f.tell() - len(inner) + io, total_bytes)


def split_annex_b(data):
    # Same result as a byte-by-byte scan, but jumps between start codes with bytes.find().
    starts = []
    limit = len(data) - 3
    i = 0
    while i < limit:
        p = data.find(b"\0\0\1", i)
        if p < 0:
            break
        if p > i and data[p - 1] == 0:
            s, plen = p - 1, 4
        else:
            s, plen = p, 3
        if s >= limit:
            break
        starts.append((s, plen))
        i = s + plen
    out = []
    for idx, (start, plen) in enumerate(starts):
        ns = start + plen
        ne = starts[idx + 1][0] if idx + 1 < len(starts) else len(data)
        nalu = data[ns:ne].rstrip(b"\0")
        if nalu:
            out.append(nalu)
    return out


def ntype(nalu): return (nalu[0] >> 1) & 0x3F


def rbsp(nalu):
    src = nalu[2:]
    out = bytearray()
    zeros = 0
    for c in src:
        if zeros >= 2 and c == 3:
            zeros = 0
            continue
        out.append(c)
        zeros = zeros + 1 if c == 0 else 0
    return bytes(out)


class Bits:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def read(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.i // 8] >> (7 - self.i % 8)) & 1)
            self.i += 1
        return v

    def bit(self): return self.read(1)

    def ue(self):
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + self.read(zeros)


@dataclass
class Info:
    width: int = 1920
    height: int = 1080
    profile_space: int = 0
    tier: int = 0
    profile_idc: int = 1
    compat: int = 0x60000000
    constraints: bytes = b"\0\0\0\0\0\0"
    level: int = 120
    chroma: int = 1
    luma_depth: int = 0
    chroma_depth: int = 0
    temporal_layers: int = 1
    temporal_nested: int = 1


def profile_tier_level(br, max_sub):
    profile_space = br.read(2)
    tier = br.read(1)
    profile_idc = br.read(5)
    compat = br.read(32)
    constraints = br.read(48).to_bytes(6, "big")
    level = br.read(8)
    pp = []
    lp = []
    for _ in range(max_sub):
        pp.append(br.bit())
        lp.append(br.bit())
    if max_sub:
        for _ in range(8 - max_sub):
            br.read(2)
    for i in range(max_sub):
        if pp[i]:
            br.read(2 + 1 + 5 + 32 + 48)
        if lp[i]:
            br.read(8)
    return profile_space, tier, profile_idc, compat, constraints, level


def parse_sps(sps):
    br = Bits(rbsp(sps))
    br.read(4)
    max_sub = br.read(3)
    nested = br.read(1)
    ps, tier, pidc, compat, cons, level = profile_tier_level(br, max_sub)
    br.ue()
    chroma = br.ue()
    if chroma == 3:
        br.read(1)
    width = br.ue()
    height = br.ue()
    if br.bit():
        left, right, top, bottom = br.ue(), br.ue(), br.ue(), br.ue()
        sw = 1 if chroma in (0, 3) else 2
        sh = 2 if chroma == 1 else 1
        width -= sw * (left + right)
        height -= sh * (top + bottom)
    ldepth = br.ue()
    cdepth = br.ue()
    return Info(width, height, ps, tier, pidc, compat, cons, level, chroma, ldepth, cdepth, max_sub + 1, nested)


@dataclass
class Sample:
    data: bytes
    key: bool


@dataclass
class Track:
    raw: list[tuple[list[bytes], bool]] = field(default_factory=list)
    params: dict[int, list[bytes]] = field(default_factory=lambda: {32: [], 33: [], 34: []})
    info: Info = field(default_factory=Info)
    first: int | None = None


def uniq(a, v):
    if v not in a:
        a.append(v)


def add_payload(track, payload, fmt, topic):
    if fmt and fmt != "h265":
        raise ValueError(f"{topic} is {fmt}, not h265")
    nalus = split_annex_b(payload)
    types = {ntype(n) for n in nalus}
    for n in nalus:
        t = ntype(n)
        if t in track.params:
            uniq(track.params[t], n)
            if t == 33 and len(track.params[33]) == 1:
                track.info = parse_sps(n)
    key = any(16 <= t <= 21 for t in types)
    if track.first is None and key and all(track.params[t] for t in (32, 33, 34)):
        track.first = len(track.raw)
    track.raw.append((nalus, key))


def collect_all(path, progress=None):
    channels = {}
    tracks = {topic: Track() for topic in CAMS}
    for op, rec in iter_records(path, progress=progress):
        if op == 0x04:
            o = 0
            cid, o = u16(rec, o)
            _, o = u16(rec, o)
            name, o = mstr(rec, o)
            _, o = mstr(rec, o)
            skip_map(rec, o)
            channels[cid] = name
        elif op == 0x05:
            o = 0
            cid, o = u16(rec, o)
            _, o = u32(rec, o)
            _, o = u64(rec, o)
            _, o = u64(rec, o)
            topic = channels.get(cid)
            if topic not in tracks:
                continue
            payload, fmt = compressed_video_payload(rec[o:])
            if payload is not None:
                add_payload(tracks[topic], payload, fmt, topic)
    if progress: progress(path.stat().st_size, path.stat().st_size)
    return tracks


def finalize(track, topic):
    if not track.raw:
        raise ValueError(f"no video samples found for {topic}")
    if not all(track.params[t] for t in (32, 33, 34)):
        raise ValueError(f"missing HEVC VPS/SPS/PPS for {topic}")
    samples = []
    for nalus, key in track.raw[track.first or 0:]:
        data = bytearray()
        for n in nalus:
            data += b32(len(n)) + n
        samples.append(Sample(bytes(data), key))
    samples[0].key = True
    return samples


def hvcc(info, params, fps):
    p = bytearray()
    p.append(1)
    p.append((info.profile_space << 6) | (info.tier << 5) | info.profile_idc)
    p += b32(info.compat) + info.constraints
    p.append(info.level)
    p += b16(0xF000)
    p += bytes([0xFC, 0xFC | (info.chroma & 3), 0xF8 | (info.luma_depth & 7), 0xF8 | (info.chroma_depth & 7)])
    p += b16(fps)
    p.append(((info.temporal_layers & 7) << 3) | ((info.temporal_nested & 1) << 2) | 3)
    p.append(3)
    for t in (32, 33, 34):
        p.append(0x80 | t)
        p += b16(len(params[t]))
        for n in params[t]:
            p += b16(len(n)) + n
    return box(b"hvcC", bytes(p))


def ftyp():
    return box(b"ftyp", b"isom" + b32(0x200) + b"isomiso6mp41hev1")


def mvhd(ts, dur):
    matrix = b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x40000000)
    return fbox(b"mvhd", 0, 0, b32(0) + b32(0) + b32(ts) + b32(dur) + b32(0x10000) + b16(0x100) + b"\0" * 10 + matrix + b"\0" * 24 + b32(2))


def tkhd(dur, w, h):
    matrix = b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x10000) + b32(0) + b32(0) + b32(0) + b32(0x40000000)
    return fbox(b"tkhd", 0, 7, b32(0) + b32(0) + b32(1) + b32(0) + b32(dur) + b"\0" * 8 + b16(0) + b16(0) + b16(0) + b16(0) + matrix + b32(w << 16) + b32(h << 16))


def stsd(info, params, fps):
    entry = b"\0" * 6 + b16(1) + b"\0" * 16 + b16(info.width) + b16(info.height) + b32(0x480000) + b32(0x480000) + b32(0) + b16(1) + b"\0" * 32 + b16(24) + b16(0xFFFF) + hvcc(info, params, fps)
    return fbox(b"stsd", 0, 0, b32(1) + box(b"hev1", entry))


def moov(samples, info, params, fps, offsets):
    movie_ts = 1000
    media_ts = 90000
    movie_dur = round(len(samples) * movie_ts / fps)
    delta = media_ts // fps
    media_dur = len(samples) * delta
    stts = fbox(b"stts", 0, 0, b32(1) + b32(len(samples)) + b32(delta))
    keys = b"".join(b32(i + 1) for i, s in enumerate(samples) if s.key)
    stss = fbox(b"stss", 0, 0, b32(len(keys) // 4) + keys)
    stsc = fbox(b"stsc", 0, 0, b32(1) + b32(1) + b32(1) + b32(1))
    stsz = fbox(b"stsz", 0, 0, b32(0) + b32(len(samples)) + b"".join(b32(len(s.data)) for s in samples))
    stco = fbox(b"stco", 0, 0, b32(len(offsets)) + b"".join(b32(o) for o in offsets))
    stbl = box(b"stbl", stsd(info, params, fps) + stts + stss + stsc + stsz + stco)
    dref = fbox(b"dref", 0, 0, b32(1) + fbox(b"url ", 0, 1, b""))
    minf = box(b"minf", fbox(b"vmhd", 0, 1, b16(0) + b16(0) + b16(0) + b16(0)) + box(b"dinf", dref) + stbl)
    mdhd = fbox(b"mdhd", 0, 0, b32(0) + b32(0) + b32(media_ts) + b32(media_dur) + b16(0x55C4) + b16(0))
    hdlr = fbox(b"hdlr", 0, 0, b32(0) + b"vide" + b"\0" * 12 + b"VideoHandler\0")
    trak = box(b"trak", tkhd(movie_dur, info.width, info.height) + box(b"mdia", mdhd + hdlr + minf))
    return box(b"moov", mvhd(movie_ts, movie_dur) + trak)


def write_mp4(out, samples, info, params, fps, progress=None):
    head = ftyp()
    meta = moov(samples, info, params, fps, [0] * len(samples))
    for _ in range(2):
        cur = len(head) + len(meta) + 8
        offsets = []
        for s in samples:
            offsets.append(cur)
            cur += len(s.data)
        meta = moov(samples, info, params, fps, offsets)
    with out.open("wb") as f:
        f.write(head)
        f.write(meta)
        f.write(b32(8 + sum(len(s.data) for s in samples)) + b"mdat")
        if progress: progress(0, len(samples))
        for index, sample in enumerate(samples, 1):
            f.write(sample.data)
            if progress: progress(index, len(samples))


def get_fps(source_dir, explicit):
    if explicit:
        return explicit
    meta = source_dir / "session.json"
    if meta.exists():
        return int(json.loads(meta.read_text(encoding="utf-8")).get("fps", 30))
    return 30


def selected_sources(source_dir, files, indices):
    if files and indices:
        raise SystemExit("Use either --files or --indices, not both.")
    if files:
        names = [name if name.lower().endswith(".mcap") else f"{name}.mcap" for name in files]
        sources = [source_dir / name for name in names]
    elif indices:
        sources = []
        for index in indices:
            normalized = index
            if index.lower().startswith("ego_"):
                normalized = index[4:]
            if normalized.lower().endswith(".mcap"):
                normalized = normalized[:-5]
            sources.append(source_dir / f"ego_{int(normalized):04d}.mcap")
    else:
        sources = sorted(source_dir.glob("*.mcap"))
    missing = [str(source) for source in sources if not source.exists()]
    if missing:
        raise SystemExit("Missing source file(s):\n" + "\n".join(missing))
    return sources

def discover_sources(root):
    root = root.resolve()
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() == ".mcap"
                  and p.parent != root and root in p.resolve().parents)


def elapsed(seconds):
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class Progress:
    def __init__(self, label, file_start, total_start):
        self.label, self.file_start, self.total_start = label, file_start, total_start
        self.last = None
        self.completed = False

    def __call__(self, done, total):
        now = time.monotonic()
        finished = total > 0 and done >= total
        if self.completed or (self.last is not None and not finished and now-self.last < 1):return
        self.last = now
        self.completed = finished
        percent = min(100, done/total*100) if total else 0
        print(f"  {self.label}：{percent:5.1f}% ({done:,}/{total:,}) | 本文件耗时 {elapsed(now-self.file_start)} | 累计耗时 {elapsed(now-self.total_start)}", flush=True)


def main():
    import os
    import tempfile
    total_start = time.monotonic()
    script_dir = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="自动扫描同级文件夹的 MCAP，在各 MCAP 旁输出双目 MP4，并打印进度及耗时。")
    ap.add_argument("source_dir", type=Path, nargs="?", help="可选输入文件夹；不填则递归扫描脚本同级文件夹。")
    ap.add_argument("--fps", type=int, help="覆盖帧率；默认各文件夹 session.json 的 fps，否则 30。")
    ap.add_argument("--files", nargs="+", help="指定 source_dir 后只处理这些文件。")
    ap.add_argument("--indices", nargs="+", help="指定 source_dir 后只处理这些 ego 编号。")
    args = ap.parse_args()
    if args.source_dir:
        root = args.source_dir.resolve()
        if not root.is_dir():ap.error("输入文件夹不存在")
        sources = selected_sources(root, args.files, args.indices)
    else:
        if args.files or args.indices:ap.error("--files / --indices 需要指定 source_dir")
        root = script_dir
        sources = discover_sources(root)
    print(f"扫描目录：{root}\n找到 {len(sources)} 个 MCAP；MP4 直接保存到各源文件旁，不创建目录。", flush=True)
    successes = failures = skipped = 0
    for index, src in enumerate(sources, 1):
        file_start = time.monotonic()
        temps = []
        published = []
        print(f"\n[{index}/{len(sources)}] 正在处理：{src}", flush=True)
        try:
            outputs = {topic: src.with_name(f"{src.stem}_{suffix}.mp4") for topic, suffix in CAMS.items()}
            if any(out.exists() for out in outputs.values()):
                skipped += 1
                print("  跳过：已有同名 MP4，不覆盖；如只有一个相机文件，请先检查。", flush=True)
                continue
            fps = get_fps(src.parent, args.fps)
            if not 1 <= fps <= 90000:raise ValueError("帧率必须在 1–90000 之间")
            tracks = collect_all(src, progress=Progress("读取 MCAP（字节）", file_start, total_start))
            for topic, out in outputs.items():
                print(f"  正在整理 {CAMS[topic]} 视频帧…", flush=True)
                track = tracks[topic]
                samples = finalize(track, topic)
                # Temporary FILES only, in the source directory; no new directories.
                fd, name = tempfile.mkstemp(prefix='.'+out.stem+'_', suffix='.partial', dir=src.parent)
                os.close(fd)
                temp = Path(name)
                temps.append((temp, out))
                write_mp4(temp, samples, track.info, track.params, fps,
                          progress=Progress(f"生成 {CAMS[topic]}（帧）", file_start, total_start))
                del samples
            for temp, out in temps:
                os.link(temp, out)  # Atomic publication without replacing existing files.
                published.append(out)
                print(f"  已输出：{out}", flush=True)
            successes += 1
            print("  本文件处理成功。", flush=True)
        except Exception as exc:
            failures += 1
            print(f"  失败：{type(exc).__name__}: {exc}", flush=True)
            for out in published:out.unlink(missing_ok=True)
        finally:
            for temp, _ in temps:temp.unlink(missing_ok=True)
            now = time.monotonic()
            print(f"[{index}/{len(sources)}] 总进度 {index/len(sources)*100:.1f}% | 本文件耗时 {elapsed(now-file_start)} | 累计耗时 {elapsed(now-total_start)}", flush=True)
    if not sources:print("未找到 MCAP 文件，请先放入脚本同级的文件夹。", flush=True)
    print(f"\n处理结束：共 {len(sources)} 个，成功 {successes} 个，失败 {failures} 个，跳过 {skipped} 个；总耗时 {elapsed(time.monotonic()-total_start)}。", flush=True)
    if failures:raise SystemExit(1)


if __name__ == "__main__":
    main()
