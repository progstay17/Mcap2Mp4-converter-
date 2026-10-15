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

from . import __version__, audio as audiomod, metadata, naming, source_adapters as sa, sync as syncmod, verify
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
    if queue is not None:
        queue.cancel_join_thread()       # keluar tanpa menunggu pengiriman progres (kunci tulis bisa dipegang proses yang mati)


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
        self.aidx = {}                     # topik audio -> indeks track audio di writer
        self.pending = []                  # (topik audio, blok PCM) yang tiba sebelum writer video ada
        self.pending_bytes = 0
        self.wants_audio = False
        self.need_key = False              # sesi gabungan: tunggu keyframe di awal bagian berikutnya (FR-108)


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


def _accept_frame(track, need_key, payload):
    """Logika frame bersama jalur tulis dan jalur verifikasi. Kembalikan (r, need_key_baru, dibuang_di_sambungan)."""
    r = track.feed(payload)
    if r is None:
        return None, need_key, False
    data, key = r
    if need_key:
        if not key:                                  # FR-108: awal bagian berikutnya tanpa keyframe dibuang
            track.frames -= 1
            track._since_key -= 1
            return None, need_key, True
        need_key = False
    return r, need_key, False


def _source_hashes(srcs, chans, limits):
    import hashlib
    hs = {c.vc.topic: hashlib.sha256() for c in chans}
    tr = {c.vc.topic: HevcTrack() for c in chans}
    n = {c.vc.topic: 0 for c in chans}
    by_topic = {c.vc.topic: c for c in chans}
    k = 0
    for part_i, src in enumerate(srcs):
        need = {t: part_i > 0 for t in hs}
        for ch, msg in iter_messages(src, topics=set(hs)):
            k += 1
            if k % 256 == 0:
                _checkpoint()
            t = ch.topic
            if n[t] >= limits[t]:
                continue
            fr = sa.parse_frame(by_topic[t].vc.adapter, msg.data)
            r, need[t], _ = _accept_frame(tr[t], need[t], fr.payload)
            if r is not None:
                hs[t].update(r[0])
                n[t] += 1
    return {t: h.hexdigest() for t, h in hs.items()}


_PENDING_CAP = 32 * 2**20


def _deliver_audio(c, atopic, st, block, s):
    """Tulis blok PCM ke MP4 kamera `c`; bila writer video belum ada, tahan sebentar (FR-96)."""
    if c.writer is None:
        if c.pending_bytes + len(block) > _PENDING_CAP:
            return False
        c.pending.append((atopic, block))
        c.pending_bytes += len(block)
        return True
    if atopic not in c.aidx:
        c.aidx[atopic] = c.writer.add_audio_track(atopic, st.rate, st.channels, s.get("audio_codec_tag", "ipcm"))
    c.writer.add_audio_block(c.aidx[atopic], block)
    return True


def _flush_pending(c, aus, s):
    for atopic, block in c.pending:
        _deliver_audio(c, atopic, aus[atopic], block, s)
    c.pending, c.pending_bytes = [], 0


def _source_audio_hashes(srcs, topics):
    import hashlib
    hs = {t: hashlib.sha256() for t in topics}
    k = 0
    for src in srcs:
        for ch, msg in iter_messages(src, topics=set(topics)):
            k += 1
            if k % 256 == 0:
                _checkpoint()
            try:
                c = audiomod.parse_raw_audio(msg.data)
            except audiomod.AudioError:
                continue
            bpf = audiomod.BYTES_PER_SAMPLE * c.channels
            hs[ch.topic].update(c.data[: len(c.data) - len(c.data) % bpf])
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
    if Path(task["src"]).name in os.environ.get("MCAP2MP4_TEST_CRASH", "\0").split(","):      # kait uji: simulasi worker mati mendadak
        os._exit(3)
    t0 = time.monotonic()
    s = task["settings"]
    src = Path(task["src"])
    rel = task["rel"]
    srcs = [Path(x) for x in (task.get("srcs") or [task["src"]])]
    res = {"source": rel, "source_path": str(src), "status": "failed", "outputs": [], "warnings": [],
           "app_version": __version__,
           "source_bytes": sum(p.stat().st_size for p in srcs) if all(p.exists() for p in srcs) else None}
    if len(srcs) > 1:
        res["parts"] = [p.name for p in srcs]
    partials = []
    chans = []
    try:
        res.update(_convert(task, s, src, res, partials, chans, srcs))
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


def _convert(task, s, src, res, partials, chans, srcs=None):
    srcs = srcs or [src]
    summary, vcs, paths, state = plan(task)
    out_dir = Path(task["out_dir"])
    res["mcap_messages"] = summary.message_count
    res["topics"] = {c.topic: summary.channel_message_counts.get(c.id) for c in summary.channels.values()}
    sums = [summary]
    for part in srcs[1:]:                                    # sesi gabungan: cocokkan topik lewat NAMA (FR-102)
        try:
            sk = read_summary(part)
        except McapError as exc:
            raise ConvertError("session_part_failed", f"bagian {part.name}: {exc}")
        vk = {v.topic: v.adapter for v in sa.find_video_channels(sk)}
        miss = [v.topic for v in vcs if vk.get(v.topic) != v.adapter]
        if miss:
            raise ConvertError("session_part_mismatch", f"bagian {part.name} tidak punya channel video {miss[0]} yang sama")
        sums.append(sk)
        res["mcap_messages"] = (res["mcap_messages"] or 0) + (sk.message_count or 0)
        for c in sk.channels.values():
            res["topics"][c.topic] = (res["topics"].get(c.topic) or 0) + (sk.channel_message_counts.get(c.id) or 0)
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
        partial = tmp_dir / f".{final.stem}.{task.get('tag') or os.getpid()}.partial"
        partials.append(partial)
        chans.append(_Ch(v, final.name, partial))
    by_id = {c.vc.channel_id: c for c in chans}
    by_topic = {c.vc.topic: c for c in chans}

    # ---- audio (FR-90..96)
    aus, a_first_log, audio_cut = {}, {}, {}
    audio_in = s.get("audio_in", "all")
    if audio_in != "none":
        for ac in audiomod.find_audio_channels(summary):
            aus[ac.topic] = audiomod.AudioTrackState(ac.topic, s.get("audio_gap_policy", "fill"),
                                                     s.get("audio_gap_tolerance_ms", 1.0))
    targets = []
    if aus:
        order = sorted(chans, key=lambda c: c.vc.topic)
        targets = order if audio_in == "all" else order[:1]
        for c in targets:
            c.wants_audio = True
    topics = {c.vc.topic for c in chans} | set(aus)

    # ---- lintasan streaming tunggal
    last_emit = [0.0]
    total_bytes = res["source_bytes"] or 1
    base_done = [0]

    def progress(done, total):
        now = time.monotonic()
        if now - last_emit[0] > 0.25 or (done >= total and base_done[0] + total >= total_bytes):
            last_emit[0] = now
            _emit(("progress", task["rel"], base_done[0] + done, total_bytes))

    k = 0
    seam_drops = []                                          # (indeks bagian, topik, jumlah) frame non-keyframe dibuang
    for part_i, part in enumerate(srcs):
        part_topics = topics
        if part_i:
            ac_k = {a.topic for a in audiomod.find_audio_channels(sums[part_i])}
            part_topics = {c.vc.topic for c in chans} | (set(aus) & ac_k)
            for c in chans:
                c.need_key = True
        part_drops = {}
        try:
            for ch, msg in iter_messages(part, topics=part_topics, progress=progress):
                k += 1
                if k % 256 == 0:
                    _checkpoint()
                if ch.topic in aus:
                    ast = aus[ch.topic]
                    if ast.error:
                        continue
                    try:
                        chunk = audiomod.parse_raw_audio(msg.data)
                    except audiomod.AudioError as exc:
                        ast.error = ("audio_parse_error", str(exc))
                        continue
                    a_first_log.setdefault(ch.topic, msg.log_time)
                    for block in ast.feed(chunk, msg.log_time):
                        for c in targets:
                            if c.error:
                                continue
                            if not _deliver_audio(c, ch.topic, ast, block, s):
                                audio_cut[c.vc.topic] = True
                    continue
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
                r, st.need_key, dropped = _accept_frame(st.track, st.need_key, fr.payload)
                if dropped:
                    part_drops[ch.topic] = part_drops.get(ch.topic, 0) + 1
                if r is None:
                    continue
                data, key = r
                if st.writer is None:
                    st.writer = Mp4Writer(lp(st.partial), co64=s.get("co64", "auto"))
                    if st.pending:
                        _flush_pending(st, aus, s)
                st.writer.add_sample(data, key, msg.log_time)
                st.stamps.append(fr.stamp_ns if fr.stamp_ns is not None else -1)
                st.logs.append(msg.log_time)
        except (McapError, sa.AdapterError, audiomod.AudioError) as exc:
            if len(srcs) > 1:                                # FR-109: sebutkan bagian yang gagal
                raise type(exc)(f"bagian ke-{part_i + 1}/{len(srcs)} ({part.name}): {exc}") from exc
            raise
        base_done[0] += part.stat().st_size
        for t, n_drop in part_drops.items():
            seam_drops.append((part_i, t, n_drop))

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
    for part_i, t, n_drop in seam_drops:
        warnings.append({"code": "session_leading_dropped", "topic": t, "part": part_i + 1, "frames": n_drop,
                         "message": f"{n_drop} frame non-keyframe di awal bagian {part_i + 1} dibuang (video dapat membeku sesaat di sambungan)"})
    for t, ast in aus.items():                               # FR-91, FR-95: audio tidak menggagalkan video
        if ast.error:
            warnings.append({"code": ast.error[0], "topic": t, "message": ast.error[1]})
        for g in ast.gaps:
            warnings.append({"code": "audio_gap", "topic": t, "position_s": g["position_s"], "duration_ms": g["duration_ms"],
                             "message": f"celah audio {g['duration_ms']} ms pada {g['position_s']} dtk"
                                        + (" (diisi hening)" if g["filled"] else " (dibiarkan)")})
        for g in ast.overlaps:
            warnings.append({"code": "audio_overlap", "topic": t, "position_s": g["position_s"],
                             "duration_ms": g["duration_ms"], "message": f"audio tumpang-tindih {g['duration_ms']} ms (dipotong)"})
    for tp in audio_cut:
        warnings.append({"code": "audio_buffer_overflow", "topic": tp,
                         "message": "audio sebelum frame video pertama terlalu panjang; sebagian awal audio dibuang"})

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
    pair_info = _pairing_payloads(good, paths, task, srcs) if s.get("metadata", {}).get("embed_pairing") else {}
    for c in good:
        times, frac, tsrc = choose_timing(c, s, session_fps)
        if s.get("fps_mode", "timestamp") == "timestamp" and tsrc != "payload_timestamp":
            warnings.append({"code": "time_fallback", "topic": c.vc.topic,
                             "message": f"timestamp di pesan tidak dapat dipakai; durasi frame dari: {tsrc}"})
        if times is not None:
            c.writer.times = array("q", times)
        ct, csrc = creation_time(c, src_mtime)
        c.writer.creation_unix = ct
        v_delay, a_delays, a_offsets = 0.0, [], []
        if c.writer.audio:
            use_payload = tsrc == "payload_timestamp"
            v_t0 = c.stamps[0] if use_payload else c.logs[0]
            for tr in c.writer.audio:
                ast = aus[tr.name]
                a_t0 = ast.first_stamp_ns if use_payload else a_first_log[tr.name]
                off = (a_t0 - v_t0) / 1e6 + float(s.get("audio_offset_ms", 0))
                a_offsets.append(round(off, 3))
                a_delays.append(max(0.0, off))
                v_delay = max(v_delay, -off)
        c.stats = c.writer.finish(c.track.info, c.track.params, frac, video_delay_ms=v_delay, audio_delays_ms=a_delays,
                                  udta=pair_info.get(c.vc.topic, b""))
        for i, a in enumerate(c.stats["audio"]):
            a["offset_to_video_ms"] = a_offsets[i]
            a["gaps"] = len(aus[a["name"]].gaps)
            a["silence_samples"] = aus[a["name"]].silence_frames
        c.stats.update(time_source=tsrc, creation_unix=ct, creation_source=csrc)
        c.writer = None                                     # sudah ditutup; hindari abort ganda

    # ---- verifikasi
    vmode = s.get("verify", "quick")
    vinfo = {}
    exe = metadata.find_exiftool(s.get("exiftool_path")) if vmode != "off" else None
    if vmode != "off":
        for c in good:
            q = verify.quick(lp(c.partial), expected_frames=c.stats["frames"],
                             expected_audio={i: a["samples"] for i, a in enumerate(c.stats["audio"])})
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
        for c in good:                                      # FR-97: hash audio hasil = hash saat tulis
            for i, a in enumerate(c.stats["audio"]):
                h, n = verify.audio_payload_sha256(lp(c.partial), i)
                if h != a["sha256"] or n != a["samples"]:
                    raise ConvertError("verify_failed", f"{c.name}: audio '{a['name']}' hasil != saat tulis")
        exact = [t for t, a in aus.items() if not a.error and a.source_exact and a.frames]
        asrc = _source_audio_hashes(srcs, exact) if exact and any(c.stats["audio"] for c in good) else {}
        for c in good:
            for a in c.stats["audio"]:
                if a["name"] in asrc:
                    ok = asrc[a["name"]] == a["sha256"]
                    vinfo[c.vc.topic].setdefault("audio", {})[a["name"]] = {"payload_matches_source": ok}
                    if not ok:
                        raise ConvertError("verify_failed", f"{c.name}: audio '{a['name']}' tidak identik dengan sumber")
        srch = _source_hashes(srcs, good, {c.vc.topic: c.stats["frames"] for c in good})
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
                     "dropped_seam_frames": sum(n for _, t, n in seam_drops if t == c.vc.topic),
                     "sha256": c.stats["sample_sha256"], "audio": c.stats["audio"]})
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
                "source": ([p.name for p in srcs] if len(srcs) > 1 else rel_name(src)), "settings": s,
                "session": task.get("session"),
                "mcap": metadata.mcap_layer(summary),
                "video": [{"topic": c.vc.topic, "adapter": c.vc.adapter, "schema": c.vc.schema,
                           "output": paths[c.vc.topic].name, "bitstream": c.track.stats(), "mp4": c.stats,
                           "trace": _trace(c)}
                          for c in good],
                "audio": [dict(a.summary(), offset_to_video_ms=next((x.get("offset_to_video_ms") for c in good
                                                                      for x in c.stats["audio"] if x["name"] == t), None),
                               written_in=[c.vc.topic for c in good if any(x["name"] == t for x in c.stats["audio"])])
                          for t, a in aus.items() if not a.error and a.frames],
                "exiftool": layer_c or None, "sync": sync_res, "warnings": warnings})
            res["sidecar"] = str(sc_path)
        except Exception as exc:
            warnings.append({"code": "sidecar_failed", "message": str(exc)})
    first = good[0]
    return {"status": "success", "outputs": outs, "frames": {c.vc.topic: c.stats["frames"] for c in good},
            "fps_used": first.stats["timing"].get("fps"), "time_source": first.stats["time_source"]}


def _pairing_payloads(good, paths, task, srcs) -> dict:
    """Penanda pasangan kamera per topik (opsi metadata.embed_pairing): ID grup yang sama di semua MP4 keluaran satu konversi,
    indeks kamera (topik naik = kiri ke kanan), nama pasangan, dan asal. Pemutar memasangkan lewat ID grup, tahan ganti nama berkas."""
    import hashlib
    import json
    topics = sorted(c.vc.topic for c in good)
    stem = next(iter(paths.values())).name.rsplit(".", 1)[0] if paths else ""
    gid = hashlib.sha1((task.get("stem") or stem or "").encode() + b"\0" + "\0".join(topics).encode()
                       + b"\0" + "\0".join(p.name for p in srcs).encode()).hexdigest()[:16]
    peers = [{"index": i, "topic": t, "file": paths[t].name} for i, t in enumerate(topics)]
    out = {}
    for i, t in enumerate(topics):
        doc = {"v": 1, "group": gid, "index": i, "count": len(topics), "topic": t, "peers": peers,
               "session": task.get("session"), "source": [p.name for p in srcs], "generator": f"mcap2mp4 {__version__}"}
        out[t] = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return out


def _trace(c) -> dict:
    """Jejak waktu per topik video untuk sidecar (FR-111, Lampiran C)."""
    from . import session as sess
    lg = c.logs
    per = (lg[-1] - lg[0]) / (len(lg) - 1) if len(lg) > 1 else None
    return {"first_time_ns": lg[0], "last_time_ns": lg[-1], "frame_period_ns": per, "frames": len(lg),
            "codec_fingerprint": sess._fingerprint(c.track)}


def rel_name(p: Path) -> str:
    return p.name
