"""Penjelajah file (FR-129): ubah isi folder menjadi daftar rekaman yang dapat diputar. Tanpa Qt/libVLC.

- MP4 hasil converter dikenali lewat sidecar `<nama>.metadata.json`; MP4 tiap kamera dipasangkan (topik naik = kiri ke kanan).
- Sidecar sesi per-file (session.index/count) dikelompokkan menjadi satu rekaman multi-bagian (sesi virtual, FR-127).
- Sidecar sesi gabungan (merged) = satu rekaman satu bagian. MP4 tanpa sidecar = satu kamera.
- `.mcap` yang belum punya hasil konversi tampil sebagai Recording(converted=False); sesinya dideteksi lewat session.py dengan cache
  per (path, ukuran, mtime)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Part, Recording


def _load_sidecar(p: Path):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and "video" in d else None
    except (OSError, ValueError):
        return None


def _part_from_sidecar(folder: Path, side: dict, sidecar: str = ""):
    cams, dur = {}, 0.0
    for v in side.get("video", []):
        out = folder / v.get("output", "")
        if v.get("output") and out.exists():
            cams[v["topic"]] = str(out)
            dur = max(dur, float((v.get("mp4") or {}).get("duration_s") or 0))
    if not cams:
        return None
    return Part(cams, dur, bool(side.get("audio")), sidecar)


def scan_folder(folder, session_cache: dict | None = None, detect_mcap=True) -> list[Recording]:
    folder = Path(folder)
    recs, used_mp4, covered_src = [], set(), set()
    groups: dict[str, list] = {}                  # id sesi per-file -> [(index, Part, sumber)]
    for sc in sorted(folder.glob("*.metadata.json")):
        side = _load_sidecar(sc)
        if side is None:
            continue
        part = _part_from_sidecar(folder, side, str(sc))
        if part is None:
            continue
        used_mp4.update(Path(x).name for x in part.cameras.values())
        src = side.get("source")
        srcs = src if isinstance(src, list) else [src] if src else []
        covered_src.update(srcs)
        ses = side.get("session") or {}
        if ses.get("merged") or not ses.get("count") or ses.get("count", 1) < 2:
            title = Path(sc.name[: -len(".metadata.json")]).name
            recs.append(Recording(title, [part], "session" if ses.get("merged") else "single", srcs))
        else:
            groups.setdefault(ses.get("id", sc.name), []).append((int(ses.get("index", 1)), part, srcs, sc.name))
    for sid, items in groups.items():
        items.sort(key=lambda x: x[0])
        recs.append(Recording(sid, [p for _, p, _, _ in items], "session" if len(items) > 1 else "single",
                              [s for _, _, ss, _ in items for s in ss]))
    plain = [m for m in sorted(folder.glob("*.mp4")) if m.name not in used_mp4 and not m.name.startswith(".")]
    recs.extend(pair_plain_mp4(plain))
    sub = folder / "mp4"                                  # hasil 'konversi lalu putar' (autoconvert.AUTOCONVERT_DIR)
    if sub.is_dir():
        srecs = scan_folder(sub, detect_mcap=False)
        recs.extend(srecs)
        for r in srecs:
            covered_src.update(r.source_files)
    if detect_mcap:
        recs.extend(_unconverted(folder, covered_src, session_cache))
    return sorted(recs, key=lambda r: (not r.converted, r.title.lower()))


def _unconverted(folder: Path, covered: set, cache: dict | None):
    from .. import session as sessmod
    files = [p for p in sorted(folder.glob("*.mcap")) if p.name not in covered and not p.name.startswith(".")]
    if not files:
        return []
    traces = []
    for p in files:
        st = p.stat()
        key = f"{p}|{st.st_size}|{int(st.st_mtime)}"
        tr = cache.get(key) if cache is not None else None
        if tr is None:
            try:
                tr = sessmod.probe(p)
            except Exception:
                continue
            if cache is not None:
                cache[key] = tr
        traces.append(tr)
    out = []
    if not traces:
        return out
    for se in sessmod.detect(traces, None, sessmod.load_overrides(folder)):
        out.append(Recording(se.id, [], "mcap", se.files, converted=False, folder=str(folder)))
    return out


def recording_for_path(path) -> Recording:
    """Rekaman yang memuat MP4 ini (lewat sidecar/sesi di foldernya); MP4 tanpa sidecar = satu kamera."""
    path = Path(path)
    for rec in scan_folder(path.parent, detect_mcap=False):
        if any(Path(c).name == path.name for part in rec.parts for c in part.cameras.values()):
            return rec
    for rec in pair_plain_mp4(sorted(path.parent.glob("*.mp4"))):
        if any(Path(c).name == path.name for part in rec.parts for c in part.cameras.values()):
            return rec
    return Recording(path.stem, [_plain_part({"video": str(path)})], "single")


# nama kamera: ...cam...<satu digit>.mp4, digit tidak didahului digit lain (ego_0014.mp4 BUKAN kamera), awalan memuat "cam"
_CAM_RE = re.compile(r"^(?P<pre>.*?[^0-9])(?P<idx>[0-9])\.(?:mp4|m4v|mov)$", re.I)


def _plain_part(paths: dict) -> Part:
    """Part dari MP4 tanpa sidecar; durasi & audio dibaca dari moov (murah)."""
    from .. import verify
    dur, audio = 0.0, False
    for p in paths.values():
        try:
            q = verify.quick(p)
            dur = max(dur, float(q.get("duration_s") or 0))
            audio = audio or bool(q.get("audio"))
        except Exception:
            pass
    return Part(dict(paths), dur, audio)


def _common_title(stems):
    import os
    pre = os.path.commonprefix(stems).rstrip("_-. 0123456789")
    for tok in ("_camera", "_cam", "camera", "cam"):
        if pre.lower().endswith(tok):
            pre = pre[: -len(tok)].rstrip("_-. ")
            break
    return pre


def pair_plain_mp4(mp4s) -> list:
    """MP4 tanpa sidecar: kamera berpasangan bila nama sama kecuali satu digit akhir (mis. *_camera_0.mp4 / *_camera_1.mp4).
    Aturan deterministik; pasangan lain bisa dibuka lewat tombol 'Buka 2 video'."""
    from .. import verify
    recs = []
    marked: dict[str, list] = {}
    rest = []
    for m in mp4s:                                          # 1) penanda pasangan di dalam MP4 (opsi embed_pairing): paling andal
        mk = verify.read_pairing(m)
        if mk:
            marked.setdefault(mk["group"], []).append((int(mk.get("index", 0)), m, mk))
        else:
            rest.append(m)
    for gid, items in marked.items():
        items.sort(key=lambda x: x[0])
        if len(items) >= 2:
            ses = items[0][2].get("session") or {}
            stems = [m.stem for _, m, _ in items]
            title = ses.get("id") or (_common_title(stems) or stems[0])
            recs.append(Recording(title, [_plain_part({f"{i:02d}_{mk.get('topic', i)}": str(m) for i, m, mk in items})], "single"))
        else:
            rest.append(items[0][1])
    mp4s = rest
    groups: dict[str, dict] = {}
    singles = []
    for m in mp4s:
        mt = _CAM_RE.match(m.name)
        if mt and "cam" in mt.group("pre").lower():
            groups.setdefault(mt.group("pre"), {})[mt.group("idx")] = m
        else:
            singles.append(m)
    for pre, cams in groups.items():
        if len(cams) >= 2:
            title = pre.rstrip("_- .") or cams[sorted(cams)[0]].stem
            recs.append(Recording(title, [_plain_part({f"cam{i}": str(cams[i]) for i in sorted(cams)})], "single"))
        else:
            singles.extend(cams.values())
    for m in singles:
        recs.append(Recording(m.stem, [_plain_part({"video": str(m)})], "single"))
    return recs


def recording_for_files(paths) -> Recording:
    """Dua (atau lebih) MP4 pilihan pengguna dibuka sebagai kiri-kanan, urut sesuai pilihan/nama."""
    paths = [Path(p) for p in paths]
    if len(paths) == 1:
        return recording_for_path(paths[0])
    cams = {f"cam{i}": str(p) for i, p in enumerate(paths[:2])}
    return Recording(" + ".join(p.stem for p in paths[:2]), [_plain_part(cams)], "single")
