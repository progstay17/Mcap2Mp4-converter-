"""Konversi satu file MCAP -> satu/lebih MP4 (PRD FR-04, FR-10..FR-14, FR-20..FR-22, FR-30..FR-33, FR-46).

convert_file(task) dijalankan di proses terpisah dan mengembalikan dict hasil (picklable, kode status netral
bahasa). Alur: baca summary -> temukan channel video -> cek keluaran yang sudah ada -> SATU lintasan streaming
(tulis ke .partial) -> sinkronisasi -> tutup moov -> verifikasi -> publikasi atomik -> sidecar.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from array import array
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from . import __version__, metadata, naming, source_adapters as sa, sync as syncmod, verify
from .h265 import HevcTrack
from .mcap_reader import McapError, iter_messages, read_summary
from .mp4_writer import Mp4TooLarge, Mp4Writer
from .paths import lp

_QUEUE = None
_CANCEL = None
_PAUSE = None


def init_worker(queue, cancel=None, pause=None):
    global _QUEUE, _CANCEL, _PAUSE
    _QUEUE, _CANCEL, _PAUSE = queue, cancel, pause


def _checkpoint():
    """Dipanggil berkala di lintasan panjang: Batal (FR-03) dan Jeda."""
    if _CANCEL is not None and _CANCEL.is_set():
        raise ConvertError("cancelled", "dibatalkan oleh pengguna")
    while _PAUSE is not None and _PAUSE.is_set():
        if _CANCEL is not None and _CANCEL.is_set():
            raise ConvertError("cancelled", "dibatalkan oleh pengguna")
        time.sleep(0.2)


def _emit(ev):
    if _QUEUE is not None:
        try:
            _QUEUE.put_nowait(ev)
        except Exception:
            pass


class ConvertError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class _Ch:
    def __init__(self, vc, name, partial):
        self.vc, self.name, self.partial = vc, name, partial
        self.track = HevcTrack()
        self.writer = None
        self.stamps = array("q")
        self.logs = array("q")
        self.error = None                  # (kode, pesan) untuk channel yang dilewati
        self.messages = 0


def read_session_fps(folder):
    f = Path(folder) / "session.json"
    if f.exists():
        try:
            v = json.loads(f.read_text(encoding="utf-8")).get("fps")
            if v:
                fr = Fraction(str(v))
                if 0 < fr <= 90000:
                    return fr
        except (ValueError, ZeroDivisionError, OSError):
            pass
    return None


def _strictly_inc(a):
    return all(a[i + 1] > a[i] for i in range(len(a) - 1))


def choose_timing(st, s, session_fps):
    """Kembalikan (array waktu ns | None, fps_fraction | None, sumber). FR-13."""
    mode = s.get("fps_mode", "timestamp")
    if mode == "integer":
        v = s.get("fps_value")
        return None, Fraction(int(v)), "manual_integer"
    if mode == "fraction":
        return None, Fraction(str(s.get("fps_value"))), "manual_fraction"
    if mode == "timestamp":
        if len(st.stamps) and all(x >= 0 for x in st.stamps) and _strictly_inc(st.stamps):
            return st.stamps, None, "payload_timestamp"
        if _strictly_inc(st.logs):
            return st.logs, None, "log_time"
    if session_fps is not None:
        return None, session_fps, "session_json"
    return None, Fraction(30), "default_30"


def creation_time(st, src_mtime):
    """FR-46: stempel absolut di pesan -> log_time -> waktu modifikasi file."""
    if len(st.stamps) and sa.is_absolute(st.stamps[0]):
        return st.stamps[0] // 10**9, "payload_timestamp"
    if len(st.logs) and sa.is_absolute(st.logs[0]):
        return st.logs[0] // 10**9, "log_time"
    return int(src_mtime), "file_mtime"


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        c = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not c.exists():
            return c
        n += 1


def _publish(pairs):
    done = []
    try:
        for partial, final in sorted(pairs, key=lambda p: str(p[1])):
            try:
                os.link(lp(partial), lp(final))             # tidak pernah menimpa
            except FileExistsError:
                raise ConvertError("exists", f"keluaran sudah ada: {final.name}")
            except OSError:
                os.rename(lp(partial), lp(final))           # exFAT/FAT; di Windows gagal bila sudah ada
                partial = None
            done.append(final)
            if partial is not None:
                try:
                    os.unlink(lp(partial))
                except OSError:
                    pass
    except Exception:
        for f in done:
            try:
                os.unlink(lp(f))
            except OSError:
                pass
        raise


def _source_hashes(src, chans, limits):
    import hashlib
    hs = {c.vc.topic: hashlib.sha256() for c in chans}
    tr = {c.vc.topic: HevcTrack() for c in chans}
    n = {c.vc.topic: 0 for c in chans}
    by_topic = {c.vc.topic: c for c in chans}
    k = 0
    for ch, msg in iter_messages(src, topics=set(hs)):
        k += 1
        if k % 256 == 0:
            _checkpoint()
        t = ch.topic
        if n[t] >= limits[t]:
            continue
        fr = sa.parse_frame(by_topic[t].vc.adapter, msg.data)
        r = tr[t].feed(fr.payload)
        if r is not None:
            hs[t].update(r[0])
            n[t] += 1
    return {t: h.hexdigest() for t, h in hs.items()}


def plan(task, summary=None):
    """Tentukan channel video, nama keluaran, dan keadaan keluaran yang sudah ada tanpa menulis apa pun."""
    s = task["settings"]
    src = Path(task["src"])
    stem = task.get("stem") or src.stem
    summary = summary or read_summary(src)
    vcs = sa.find_video_channels(summary)
    sel = s.get("channels", "all")
    if sel != "all":
        vcs = [v for v in vcs if v.topic in set(sel)]
    if not vcs:
        raise ConvertError("no_video_channel", "tidak ada channel video yang dikenali dari skema")
    out_dir = Path(task["out_dir"])
    date = datetime.fromtimestamp(src.stat().st_mtime)
    try:
        names = naming.build_names(stem, [v.topic for v in vcs], s.get("name_pattern"), date,
                                   fail_on_collision=s.get("on_name_collision") == "fail")
    except naming.NameCollisionError as exc:
        raise ConvertError("name_collision", str(exc))
    paths = {v.topic: out_dir / names[v.topic] for v in vcs}
    exist = [t for t, p in paths.items() if p.exists()]
    state = "pending"
    if exist and len(exist) == len(paths):
        state = "complete" if all(verify.quick(lp(p))["ok"] for p in paths.values()) else "incomplete"
    elif exist:
        state = "incomplete"
    if state == "incomplete" and s.get("on_exists") == "number":
        k = 2
        while True:
            alt = naming.build_names(f"{stem}_{k}", [v.topic for v in vcs], s.get("name_pattern"), date)
            ap = {v.topic: out_dir / alt[v.topic] for v in vcs}
            if not any(p.exists() for p in ap.values()):
                paths, state = ap, "pending"
                break
            k += 1
    return summary, vcs, paths, state


def convert_file(task: dict) -> dict:
    if os.environ.get("MCAP2MP4_TEST_CRASH") == Path(task["src"]).name:      # kait uji: simulasi worker mati mendadak
        os._exit(3)
    t0 = time.monotonic()
    s = task["settings"]
    src = Path(task["src"])
    rel = task["rel"]
    res = {"source": rel, "source_path": str(src), "status": "failed", "outputs": [], "warnings": [],
           "app_version": __version__, "source_bytes": src.stat().st_size if src.exists() else None}
    partials = []
    chans = []
    try:
        res.update(_convert(task, s, src, res, partials, chans))
    except ConvertError as exc:
        res.update(status="failed", error_code=exc.code, error=exc.message, **exc.extra)
        if exc.code in ("out_of_sync",):
            res["status"] = "out_of_sync"
        if exc.code == "exists_incomplete":
            res["status"] = "incomplete"
    except McapError as exc:
        res.update(status="failed", error_code="mcap_error", error=str(exc))
    except (sa.AdapterError, ValueError) as exc:
        res.update(status="failed", error_code="bitstream_error", error=f"{type(exc).__name__}: {exc}")
    except Mp4TooLarge as exc:
        res.update(status="failed", error_code="too_large", error=str(exc))
    except KeyboardInterrupt:
        res.update(status="failed", error_code="cancelled", error="dibatalkan")
        raise
    except Exception as exc:                          # jaring pengaman: satu file gagal tidak menghentikan antrean
        res.update(status="failed", error_code="internal_error", error=f"{type(exc).__name__}: {exc}")
    finally:
        for c in chans:
            if c.writer is not None:
                c.writer.abort()
        for p in partials:
            try:
                os.unlink(lp(p))
            except OSError:
                pass
        el = time.monotonic() - t0
        res["elapsed_s"] = round(el, 3)
        if res.get("source_bytes") and el > 0:
            res["mb_per_s"] = round(res["source_bytes"] / 1e6 / el, 2)
    return res


def _convert(task, s, src, res, partials, chans):
    summary, vcs, paths, state = plan(task)
    out_dir = Path(task["out_dir"])
    res["mcap_messages"] = summary.message_count
    res["topics"] = {c.topic: summary.channel_message_counts.get(c.id) for c in summary.channels.values()}
    if state == "complete":
        outs = [{"topic": t, "path": str(p), "bytes": p.stat().st_size} for t, p in paths.items()]
        return {"status": "skipped", "outputs": outs}
    if state == "incomplete":
        raise ConvertError("exists_incomplete",
                           "sebagian keluaran sudah ada; tidak ditimpa (bersihkan sisa lalu ulangi, atau pakai on_exists=number)",
                           existing=[str(p) for p in paths.values() if p.exists()])

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(task.get("temp_dir") or out_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    need = int(res["source_bytes"] * 1.02) + 32 * 2**20
    if free < need:
        raise ConvertError("disk_low", f"ruang disk tidak cukup: butuh ±{need // 2**20} MB, tersedia {free // 2**20} MB")

    for v in vcs:
        final = paths[v.topic]
        partial = tmp_dir / f".{final.stem}.{os.getpid()}.partial"
        partials.append(partial)
        chans.append(_Ch(v, final.name, partial))
    by_id = {c.vc.channel_id: c for c in chans}
    by_topic = {c.vc.topic: c for c in chans}

    # ---- lintasan streaming tunggal
    last_emit = [0.0]

    def progress(done, total):
        now = time.monotonic()
        if now - last_emit[0] > 0.25 or done >= total:
            last_emit[0] = now
            _emit(("progress", task["rel"], done, total))

    k = 0
    for ch, msg in iter_messages(src, topics={c.vc.topic for c in chans}, progress=progress):
        k += 1
        if k % 256 == 0:
            _checkpoint()
        st = by_topic[ch.topic]
        if st.error:
            continue
        st.messages += 1
        fr = sa.parse_frame(st.vc.adapter, msg.data)
        if fr.fmt != "h265":
            st.error = ("unsupported_codec", f"codec '{fr.fmt}' belum didukung (hanya H.265)")
            if st.writer is not None:
                st.writer.abort()
                st.writer = None
            continue
        r = st.track.feed(fr.payload)
        if r is None:
            continue
        data, key = r
        if st.writer is None:
            st.writer = Mp4Writer(lp(st.partial), co64=s.get("co64", "auto"))
        st.writer.add_sample(data, key, msg.log_time)
        st.stamps.append(fr.stamp_ns if fr.stamp_ns is not None else -1)
        st.logs.append(msg.log_time)

    warnings, channel_errors = res["warnings"], []
    good = []
    for c in chans:
        if c.error:
            channel_errors.append({"topic": c.vc.topic, "code": c.error[0], "message": c.error[1]})
        elif c.writer is None or c.writer.count == 0:
            channel_errors.append({"topic": c.vc.topic, "code": "no_video_samples",
                                   "message": "tidak ada frame video (tidak ditemukan keyframe dengan VPS/SPS/PPS lengkap)"})
        elif not c.track.ready():
            channel_errors.append({"topic": c.vc.topic, "code": "missing_parameter_sets",
                                   "message": "VPS/SPS/PPS H.265 tidak lengkap"})
        else:
            good.append(c)
    res["channel_errors"] = channel_errors
    if not good:
        first = channel_errors[0]
        raise ConvertError(first["code"], first["message"], channel_errors=channel_errors)
    for e in channel_errors:
        warnings.append({"code": e["code"], "topic": e["topic"], "message": e["message"]})

    # ---- sinkronisasi antar-channel (FR-33)
    sync_res = None
    if len(good) > 1:
        pol = s.get("sync", {}).get("policy", "strict")
        sync_res = syncmod.compare({c.vc.topic: c.logs for c in good}, s.get("sync", {}).get("tolerance_frames", 0.5))
        res["sync"] = sync_res
        if not sync_res["ok"]:
            reason = sync_res["reason"]
            if pol == "trim" and sync_res.get("trim_to"):
                n = sync_res["trim_to"]
                for c in good:
                    c.writer.truncate_samples(n)
                    del c.stamps[n:], c.logs[n:]
                warnings.append({"code": "sync_trimmed", "message": f"frame berlebih dipangkas hingga {n} frame"})
            elif pol == "warn":
                warnings.append({"code": "out_of_sync", "message": f"channel tidak sinkron ({reason})"})
            else:
                raise ConvertError("out_of_sync", f"channel video tidak sinkron ({reason}): "
                                   f"{sync_res['counts']}", sync=sync_res)

    # ---- tutup moov
    session_fps = read_session_fps(src.parent)
    src_mtime = src.stat().st_mtime
    stats = {}
    for c in good:
        times, frac, tsrc = choose_timing(c, s, session_fps)
        if s.get("fps_mode", "timestamp") == "timestamp" and tsrc != "payload_timestamp":
            warnings.append({"code": "time_fallback", "topic": c.vc.topic,
                             "message": f"timestamp di pesan tidak dapat dipakai; durasi frame dari: {tsrc}"})
        if times is not None:
            c.writer.times = array("q", times)
        ct, csrc = creation_time(c, src_mtime)
        c.writer.creation_unix = ct
        c.stats = c.writer.finish(c.track.info, c.track.params, frac)
        c.stats.update(time_source=tsrc, creation_unix=ct, creation_source=csrc)
        c.writer = None                                     # sudah ditutup; hindari abort ganda

    # ---- verifikasi
    vmode = s.get("verify", "quick")
    vinfo = {}
    exe = metadata.find_exiftool(s.get("exiftool_path")) if vmode != "off" else None
    if vmode != "off":
        for c in good:
            q = verify.quick(lp(c.partial), expected_frames=c.stats["frames"])
            vinfo[c.vc.topic] = {"quick": q["ok"], "errors": q["errors"]}
            if not q["ok"]:
                raise ConvertError("verify_failed", f"{c.name}: " + "; ".join(q["errors"]))
            if exe is not None:
                try:
                    tags = metadata.exiftool_read(lp(c.partial), exe)
                    bad = [k for k in tags if k.endswith(":Error")]
                    vinfo[c.vc.topic]["exiftool"] = "error" if bad else "ok"
                    if bad:
                        warnings.append({"code": "exiftool_error", "topic": c.vc.topic, "message": str(tags[bad[0]])})
                except Exception as exc:                    # pemeriksaan kedua tidak menggagalkan
                    vinfo[c.vc.topic]["exiftool"] = "unavailable"
                    warnings.append({"code": "exiftool_unavailable", "topic": c.vc.topic, "message": str(exc)})
    if vmode == "full":
        back = {}
        for c in good:
            h, n = verify.payload_sha256(lp(c.partial))
            back[c.vc.topic] = h
            if h != c.stats["sample_sha256"] or n != c.stats["frames"]:
                raise ConvertError("verify_failed", f"{c.name}: hash payload hasil != hash saat tulis")
        srch = _source_hashes(src, good, {c.vc.topic: c.stats["frames"] for c in good})
        for c in good:
            ok = srch[c.vc.topic] == back[c.vc.topic]
            vinfo[c.vc.topic]["payload_matches_source"] = ok
            if not ok:
                raise ConvertError("verify_failed", f"{c.name}: payload tidak identik dengan sumber")
    res["verify"] = vinfo

    # ---- publikasi atomik
    _publish([(c.partial, paths[c.vc.topic]) for c in good])
    outs = []
    for c in good:
        final = paths[c.vc.topic]
        outs.append({"topic": c.vc.topic, "path": str(final), "bytes": final.stat().st_size,
                     "frames": c.stats["frames"], "keyframes": c.stats["keyframes"],
                     "duration_s": round(c.stats["duration_s"], 3), "fps": c.stats["timing"].get("fps"),
                     "time_source": c.stats["time_source"], "co64": c.stats["co64"],
                     "mdat_largesize": c.stats["mdat_largesize"],
                     "creation_unix": c.stats["creation_unix"], "creation_source": c.stats["creation_source"],
                     "codec": "h265", "width": c.track.info.width, "height": c.track.info.height,
                     "dropped_leading_frames": c.track.dropped_leading,
                     "sha256": c.stats["sample_sha256"]})
    partials.clear()                                        # semua sudah dipublikasikan/dipindah

    # ---- sidecar (FR-40/41)
    if s.get("metadata", {}).get("sidecar_json", True):
        try:
            sc_dir = Path(task.get("sidecar_dir") or out_dir)
            sc_dir.mkdir(parents=True, exist_ok=True)
            sc_path = _free(sc_dir / f"{task.get('stem') or src.stem}.metadata.json")
            layer_c = {}
            if exe is not None:
                for c in good:
                    try:
                        layer_c[c.vc.topic] = metadata.exiftool_read(lp(paths[c.vc.topic]), exe)
                    except Exception:
                        layer_c[c.vc.topic] = None
            metadata.write_sidecar(sc_path, {
                "source": rel_name(src), "settings": s,
                "mcap": metadata.mcap_layer(summary),
                "video": [{"topic": c.vc.topic, "adapter": c.vc.adapter, "schema": c.vc.schema,
                           "output": paths[c.vc.topic].name, "bitstream": c.track.stats(), "mp4": c.stats}
                          for c in good],
                "exiftool": layer_c or None, "sync": sync_res, "warnings": warnings})
            res["sidecar"] = str(sc_path)
        except Exception as exc:
            warnings.append({"code": "sidecar_failed", "message": str(exc)})
    first = good[0]
    return {"status": "success", "outputs": outs, "frames": {c.vc.topic: c.stats["frames"] for c in good},
            "fps_used": first.stats["timing"].get("fps"), "time_source": first.stats["time_source"]}


def rel_name(p: Path) -> str:
    return p.name
