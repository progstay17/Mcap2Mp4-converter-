"""Parser NAL/VPS/SPS/PPS H.265 (PRD FR-41) + pelacak trek streaming.

Port dari legacy: split_annex_b, ntype, rbsp, Bits, parse_sps, add_payload.
HevcTrack bekerja streaming: frame sebelum keyframe pertama yang lengkap parameter-setnya dibuang
(perilaku legacy), selebihnya langsung diserahkan ke penulis; tidak ada frame yang ditampung.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

NAL_VPS, NAL_SPS, NAL_PPS = 32, 33, 34


def b32(v): return struct.pack(">I", v)


def split_annex_b(data) -> list[bytes]:
    """Pisahkan NAL unit Annex-B. Hasil sama dengan pemindaian byte-per-byte, tetapi memakai find()."""
    data = bytes(data)
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


def ntype(nalu) -> int:
    return (nalu[0] >> 1) & 0x3F


def is_keyframe_type(t: int) -> bool:
    return 16 <= t <= 21           # BLA/IDR/CRA


def rbsp(nalu) -> bytes:
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
            if self.i // 8 >= len(self.d):
                raise ValueError("SPS terpotong")
            v = (v << 1) | ((self.d[self.i // 8] >> (7 - self.i % 8)) & 1)
            self.i += 1
        return v

    def bit(self): return self.read(1)

    def ue(self):
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("Exp-Golomb tidak valid")
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
    parsed: bool = False           # True bila berasal dari SPS sungguhan


def _profile_tier_level(br, max_sub):
    profile_space = br.read(2)
    tier = br.read(1)
    profile_idc = br.read(5)
    compat = br.read(32)
    constraints = br.read(48).to_bytes(6, "big")
    level = br.read(8)
    pp, lp = [], []
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


def parse_sps(sps) -> Info:
    br = Bits(rbsp(sps))
    br.read(4)
    max_sub = br.read(3)
    nested = br.read(1)
    ps, tier, pidc, compat, cons, level = _profile_tier_level(br, max_sub)
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
    return Info(width, height, ps, tier, pidc, compat, cons, level, chroma, ldepth, cdepth,
                max_sub + 1, nested, parsed=True)


def describe(info: Info) -> dict:
    """Lapis B metadata (FR-41), sebagian; VUI/GOP menyusul di M2."""
    return {"width": info.width, "height": info.height, "profile_idc": info.profile_idc,
            "tier": "high" if info.tier else "main", "level_idc": info.level,
            "level": round(info.level / 30, 1), "chroma_format_idc": info.chroma,
            "bit_depth_luma": info.luma_depth + 8, "bit_depth_chroma": info.chroma_depth + 8,
            "temporal_layers": info.temporal_layers}


@dataclass
class HevcTrack:
    """Status aliran H.265 satu channel. feed() mengembalikan (sample_bytes, is_key) atau None bila dibuang."""
    params: dict = field(default_factory=lambda: {NAL_VPS: [], NAL_SPS: [], NAL_PPS: []})
    info: Info = field(default_factory=Info)
    started: bool = False
    dropped_leading: int = 0
    frames: int = 0
    keyframes: int = 0
    gop_lengths: list = field(default_factory=list)
    _since_key: int = 0

    def feed(self, payload):
        nalus = split_annex_b(payload)
        types = set()
        for n in nalus:
            t = ntype(n)
            types.add(t)
            if t in self.params and n not in self.params[t]:
                self.params[t].append(n)
                if t == NAL_SPS and not self.info.parsed:
                    self.info = parse_sps(n)
        key = any(is_keyframe_type(t) for t in types)
        if not self.started:
            if key and all(self.params[t] for t in (NAL_VPS, NAL_SPS, NAL_PPS)):
                self.started = True
            else:
                self.dropped_leading += 1
                return None
        data = b"".join(b32(len(n)) + n for n in nalus)
        if key:
            self.keyframes += 1
            if self.frames:
                self.gop_lengths.append(self._since_key)
            self._since_key = 0
        self._since_key += 1
        self.frames += 1
        return data, key

    def ready(self) -> bool:
        return self.started and all(self.params[t] for t in (NAL_VPS, NAL_SPS, NAL_PPS))

    def stats(self) -> dict:
        g = self.gop_lengths
        return {"frames": self.frames, "keyframes": self.keyframes,
                "dropped_leading_frames": self.dropped_leading,
                "gop_min": min(g) if g else None, "gop_max": max(g) if g else None,
                **describe(self.info)}
