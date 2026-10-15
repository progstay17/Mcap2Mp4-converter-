"""Deteksi dan penggabungan SESI (PRD 0.3, FR-100..FR-111).

probe(path)  -> Trace     jejak waktu murah per file (FR-101): per topik video/audio waktu pesan pertama & terakhir dari INDEKS
                          CHUNK (bukan awal/akhir keseluruhan file: sensor lain membuatnya tumpang-tindih), periode frame,
                          sidik jari codec (profil/level/resolusi/hash VPS-SPS-PPS), set topik.
detect(...)  -> [Session] aturan sambung A->B (FR-102) berbukti dan menyebut alasan; urutan menurut waktu mulai video (FR-103).
sessions.json di folder: keputusan manual gabung/pisah menjadi sumber kebenaran (FR-104).
merge(...)   -> hasil konversi satu sesi menjadi satu MP4 per topik (FR-107), memakai worker.convert_file(task["srcs"]).
Semua aturan deterministik: tidak ada heuristik buram, tidak ada AI.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import audio as audiomod, naming, source_adapters as sa
from .h265 import HevcTrack, NAL_PPS, NAL_SPS, NAL_VPS
from .mcap_reader import iter_messages, read_summary, topic_time_range

DEFAULT_CFG = {"session_gap_tolerance_frames": 1.5, "session_max_gap_s": 2.0, "session_name_pattern": "{pertama}~{terakhir}"}
SESSIONS_FILE = "sessions.json"
_PROBE_MESSAGE_LIMIT = 2000


@dataclass
class TopicTrace:
    topic: str
    kind: str                         # video | audio
    first_ns: int
    last_ns: int
    count: int | None
    period_ns: float | None = None    # video
    fingerprint: str | None = None    # video
    sample_rate: int | None = None    # audio
    channels: int | None = None       # audio

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Trace:
    path: str
    name: str
    size: int
    mtime: float
    video: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)

    @property
    def folder(self): return str(Path(self.path).parent)

    @property
    def start_ns(self):
        return min((t.first_ns for t in self.video.values()), default=None)

    def to_dict(self):
        return {"file": self.name, "video": [t.to_dict() for t in self.video.values()],
                "audio": [t.to_dict() for t in self.audio.values()]}


@dataclass
class Session:
    id: str
    key: str                          # hash daftar file berurutan (FR-110)
    folder: str
    traces: list
    joins: list = field(default_factory=list)       # satu per sambungan antar bagian berurutan
    origin: str = "auto"
    split_reason: dict | None = None               # mengapa sesi berikutnya terpisah dari sesi ini

    @property
    def files(self): return [t.name for t in self.traces]

    @property
    def count(self): return len(self.traces)

    def duration_s(self):
        """Durasi total menurut topik video pertama (dari awal bagian pertama ke akhir bagian terakhir + 1 periode)."""
        a, b = self.traces[0], self.traces[-1]
        topic = sorted(a.video)[0]
        t0, t1 = a.video[topic], b.video.get(topic) or a.video[topic]
        return (t1.last_ns - t0.first_ns + (t1.period_ns or 0)) / 1e9

    def to_dict(self):
        return {"id": self.id, "origin": self.origin, "files": self.files, "joins": self.joins}


# ---------------------------------------------------------------- probe (FR-101)
def _h8(nals):
    return hashlib.sha1(b"".join(nals)).hexdigest()[:8]


def _fingerprint(tr: HevcTrack) -> str:
    i = tr.info
    return (f"hevc/p{i.profile_idc}/t{i.tier}/L{i.level}/{i.width}x{i.height}/c{i.chroma}/{i.luma_depth + 8}bit"
            f"/vps:{_h8(tr.params[NAL_VPS])}/sps:{_h8(tr.params[NAL_SPS])}/pps:{_h8(tr.params[NAL_PPS])}")


def probe(path) -> Trace:
    path = Path(path)
    st = path.stat()
    summary = read_summary(path)
    vcs = {v.topic: v for v in sa.find_video_channels(summary)}
    acs = {a.topic: a for a in audiomod.find_audio_channels(summary)}
    counts = {c.topic: summary.channel_message_counts.get(c.id) for c in summary.channels.values()}
    tr = Trace(str(path), path.name, st.st_size, st.st_mtime)
    if not vcs:
        return tr
    ranges = topic_time_range(path, set(vcs) | set(acs), summary)
    tracks = {t: HevcTrack() for t in vcs}
    fmts, ready, a_info, n = {}, set(), {}, 0
    for ch, msg in iter_messages(path, topics=set(vcs) | set(acs)):
        n += 1
        if ch.topic in vcs:
            if ch.topic in ready:
                pass
            else:
                try:
                    fr = sa.parse_frame(vcs[ch.topic].adapter, msg.data)
                except sa.AdapterError:
                    continue
                fmts[ch.topic] = fr.fmt
                if fr.fmt != "h265":
                    ready.add(ch.topic)
                else:
                    tracks[ch.topic].feed(fr.payload)
                    if tracks[ch.topic].ready():
                        ready.add(ch.topic)
        elif ch.topic not in a_info:
            try:
                c = audiomod.parse_raw_audio(msg.data)
                a_info[ch.topic] = (c.rate, c.channels)
            except audiomod.AudioError:
                a_info[ch.topic] = (None, None)
        if (len(ready) == len(vcs) and len(a_info) == len(acs)) or n >= _PROBE_MESSAGE_LIMIT:
            break
    for t in vcs:
        if t not in ranges:
            continue
        first, last = ranges[t]
        cnt = counts.get(t)
        period = (last - first) / (cnt - 1) if cnt and cnt > 1 else None
        fp = _fingerprint(tracks[t]) if tracks[t].ready() else (fmts.get(t) or "unknown")
        tr.video[t] = TopicTrace(t, "video", first, last, cnt, period_ns=period, fingerprint=fp)
    for t in acs:
        if t in ranges:
            r, c = a_info.get(t, (None, None))
            tr.audio[t] = TopicTrace(t, "audio", ranges[t][0], ranges[t][1], counts.get(t), sample_rate=r, channels=c)
    return tr


# ---------------------------------------------------------------- aturan sambung (FR-102)
def evaluate_join(a: Trace, b: Trace, cfg=None) -> dict:
    """Apakah B melanjutkan A? Kembalikan {join, reason, gap_frames}. Semua syarat harus terpenuhi."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    if a.folder != b.folder:
        return {"join": False, "reason": "different_folder", "gap_frames": None}
    if not a.video or not b.video:
        return {"join": False, "reason": "no_video", "gap_frames": None}
    if set(a.video) != set(b.video):
        return {"join": False, "reason": "topics_differ", "gap_frames": None}
    for t in a.video:
        if a.video[t].fingerprint != b.video[t].fingerprint:
            return {"join": False, "reason": "codec_differs", "gap_frames": None, "topic": t}
    tol, max_gap = float(cfg["session_gap_tolerance_frames"]), float(cfg["session_max_gap_s"])
    shown = None
    for t in sorted(a.video):
        period = a.video[t].period_ns or b.video[t].period_ns
        if not period:
            return {"join": False, "reason": "period_unknown", "gap_frames": None, "topic": t}
        gap = (b.video[t].first_ns - a.video[t].last_ns) / period
        shown = gap if shown is None else shown
        if gap < 0:
            return {"join": False, "reason": "time_backwards", "gap_frames": round(gap, 4), "topic": t}
        if gap < 0.5:
            return {"join": False, "reason": "overlap", "gap_frames": round(gap, 4), "topic": t}
        if gap > 1 + tol or gap * period / 1e9 > max_gap:
            return {"join": False, "reason": "gap_too_large", "gap_frames": round(gap, 4), "topic": t}
    return {"join": True, "reason": "continuous", "gap_frames": round(shown, 4)}


def _stem(name): return Path(name).stem


def _make(traces, joins, origin="auto") -> Session:
    sid = _stem(traces[0].name) if len(traces) == 1 else f"{_stem(traces[0].name)}~{_stem(traces[-1].name)}"
    key = hashlib.sha1("\n".join(t.name for t in traces).encode()).hexdigest()[:12]
    return Session(sid, key, traces[0].folder, traces, joins, origin)


def detect(traces, cfg=None, overrides=None) -> list[Session]:
    """Kelompokkan jejak menjadi sesi. overrides = {"join": [[a, b]], "split": [[a, b]]} (nama file) dari sessions.json."""
    ov = overrides or {}
    force_join = {tuple(p) for p in ov.get("join", [])}
    force_split = {tuple(p) for p in ov.get("split", [])}
    by_folder: dict[str, list] = {}
    for t in traces:
        by_folder.setdefault(t.folder, []).append(t)
    out = []
    for folder in sorted(by_folder):
        ordered = sorted(by_folder[folder], key=lambda t: (t.start_ns if t.start_ns is not None else float("inf"), t.name))
        cur, joins = [ordered[0]], []
        for prev, nxt in zip(ordered, ordered[1:]):
            ev = evaluate_join(prev, nxt, cfg)
            pair = (prev.name, nxt.name)
            if pair in force_split:
                ev = {**ev, "join": False, "reason": "manual_split"}
            elif pair in force_join and not ev["join"]:
                ev = {**ev, "join": True, "reason": "manual_join"}
            if ev["join"]:
                cur.append(nxt)
                joins.append({"from": prev.name, "to": nxt.name, "gap_frames": ev["gap_frames"], "reason": ev["reason"]})
            else:
                out.append(_make(cur, joins, "manual" if any(j["reason"] == "manual_join" for j in joins) else "auto"))
                out[-1].split_reason = {"to": nxt.name, **{k: ev[k] for k in ("reason", "gap_frames")}}
                cur, joins = [nxt], []
        out.append(_make(cur, joins, "manual" if any(j["reason"] == "manual_join" for j in joins) else "auto"))
    return out


# ---------------------------------------------------------------- sessions.json (FR-104)
def _sessions_path(folder): return Path(folder) / SESSIONS_FILE


def load_overrides(folder) -> dict:
    p = _sessions_path(folder)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ov = d.get("overrides") or {}
        return {"join": [list(x) for x in ov.get("join", [])], "split": [list(x) for x in ov.get("split", [])]}
    except (OSError, ValueError, AttributeError):
        return {"join": [], "split": []}


def save_sessions(folder, sessions=None, overrides=None) -> Path:
    """Tulis sessions.json (Lampiran D) secara atomik."""
    folder = Path(folder)
    ov = overrides if overrides is not None else load_overrides(folder)
    data = {"version": 1, "sessions": [s.to_dict() for s in (sessions or [])], "overrides": ov}
    p = _sessions_path(folder)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def set_override(folder, a: str, b: str, kind: str) -> dict:
    """kind: join | split | clear. Mengembalikan overrides terbaru dan menyimpannya."""
    if kind not in ("join", "split", "clear"):
        raise ValueError("kind harus join|split|clear")
    ov = load_overrides(folder)
    pair = [a, b]
    for k in ("join", "split"):
        ov[k] = [p for p in ov[k] if p != pair]
    if kind != "clear":
        ov[kind].append(pair)
    save_sessions(folder, None, ov) if not _sessions_path(folder).exists() else _rewrite_overrides(folder, ov)
    return ov


def _rewrite_overrides(folder, ov):
    p = _sessions_path(folder)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {"version": 1, "sessions": []}
    d["overrides"] = ov
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def detect_folder(folder, cfg=None, save=False) -> list[Session]:
    """Pindai satu folder (tanpa rekursi: sesi tidak melintasi folder, FR-100) dan deteksi sesi."""
    folder = Path(folder)
    traces = []
    for p in sorted(folder.glob("*.mcap"), key=lambda x: x.name.lower()):
        if not p.name.startswith("."):
            try:
                traces.append(probe(p))
            except Exception:
                continue
    sess = detect(traces, cfg, load_overrides(folder)) if traces else []
    if save and sess:
        save_sessions(folder, sess)
    return sess


# ---------------------------------------------------------------- penamaan & gabung (FR-105, FR-107)
def session_stem(files, pattern=None) -> str:
    pattern = pattern or DEFAULT_CFG["session_name_pattern"]
    first, last = _stem(files[0]), _stem(files[-1])
    return naming.sanitize(pattern.format(pertama=first, terakhir=last, jumlah=len(files)), "sesi")


def merge(session: Session, settings: dict, out_dir, **task_extra) -> dict:
    """Konversi satu sesi menjadi satu MP4 per topik (FR-107). Hasil sama bentuknya dengan worker.convert_file."""
    from . import worker
    task = {"src": session.traces[0].path, "srcs": [t.path for t in session.traces], "rel": session.files[0],
            "out_dir": str(out_dir), "stem": session_stem(session.files, settings.get("session_name_pattern")),
            "session": {"id": session.id, "index": 1, "count": session.count, "merged": True, "parts": session.files},
            "settings": settings}
    task.update(task_extra)
    return worker.convert_file(task)
