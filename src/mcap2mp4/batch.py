"""Pemindaian, validasi lokasi output, dan pelaksanaan batch (PRD FR-01..FR-04, FR-06..FR-08, FR-20..FR-22, FR-50..FR-52, FR-61).

Sumber kebenaran resume = manifest.jsonl. File diproses di proses terpisah (spawn), paling banyak `workers`
sekaligus; satu file gagal tidak menghentikan antrean.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, worker
from .manifest import Manifest
from .paths import is_inside, is_network, lp


class BatchError(Exception):
    def __init__(self, code, **kw):
        super().__init__(code)
        self.code, self.kw = code, kw


@dataclass
class Entry:
    path: Path
    rel: str                  # kunci manifest (posix, relatif ke folder sumber)
    size: int


def scan(s: dict) -> list[Entry]:
    root = Path(s["source_dir"])
    it = root.rglob("*") if s.get("recursive", True) else root.glob("*")
    out = []
    for p in sorted(it, key=lambda x: str(x).lower()):
        try:
            if p.is_file() and p.suffix.lower() == ".mcap" and not p.name.startswith("."):
                out.append(Entry(p, p.relative_to(root).as_posix(), p.stat().st_size))
        except OSError:
            continue
    return out


def out_dir_for(s: dict, e: Entry) -> Path:
    if s["output_mode"] == "beside":
        return e.path.parent
    dst = Path(s["output_dir"])
    if s.get("mirror_structure", True):
        return dst / Path(e.rel).parent
    return dst


def reports_base(s: dict) -> Path:
    if s.get("reports_dir"):
        return Path(s["reports_dir"])
    if s["output_mode"] != "beside" and s.get("output_dir"):
        return Path(s["output_dir"])
    return Path(s["source_dir"]) / "_mcap2mp4_reports"


def validate(s: dict) -> list[tuple[str, dict]]:
    """Kembalikan daftar peringatan (kode, parameter); raise BatchError untuk kesalahan fatal (FR-08)."""
    warns = []
    src = Path(s["source_dir"])
    if not src.is_dir():
        raise BatchError("src_missing", path=str(src))
    mode = s["output_mode"]
    if mode in ("folder", "ask") and not s.get("output_dir"):
        raise BatchError("output_required")
    if mode == "folder":
        dst = Path(s["output_dir"])
        if is_inside(dst, src) and s.get("recursive", True):
            raise BatchError("output_inside_source")
        try:
            dst.mkdir(parents=True, exist_ok=True)
            probe = dst / f".w{os.getpid()}.tmp"
            probe.write_bytes(b"x")
            probe.unlink()
        except OSError:
            raise BatchError("output_not_writable", path=str(dst))
        if is_network(dst):
            warns.append(("network_output", {"path": str(dst)}))
    if is_network(src):
        warns.append(("network_source", {"path": str(src)}))
    tmp = s.get("temp_dir")
    if tmp:
        Path(tmp).mkdir(parents=True, exist_ok=True)
        ref = Path(s["output_dir"]) if mode == "folder" else src
        if os.stat(tmp).st_dev != os.stat(ref).st_dev:
            raise BatchError("temp_other_volume")
    return warns


def sweep_partials(dirs, max_age_h=6):
    n, now = 0, time.time()
    for d in dirs:
        try:
            for p in Path(d).glob(".*.partial"):
                if now - p.stat().st_mtime > max_age_h * 3600:
                    p.unlink()
                    n += 1
        except OSError:
            pass
    return n


class RunControl:
    """Kendali Jeda/Batal lintas proses (dipakai GUI). Event dibuat dari konteks 'spawn'."""
    def __init__(self):
        ctx = mp.get_context("spawn")
        self.cancel = ctx.Event()
        self.pause = ctx.Event()


def unique_stems(s: dict, entries) -> dict[str, str]:
    """Mode folder tanpa cermin struktur: dua sumber ber-nama sama (beda subfolder) tidak boleh berbagi nama
    keluaran (dulu file kedua bisa dianggap 'sudah selesai' padahal belum dikonversi)."""
    stems = {e.rel: e.path.stem for e in entries}
    if s["output_mode"] == "beside" or s.get("mirror_structure", True):
        return stems
    count: dict[str, int] = {}
    for v in stems.values():
        count[v.lower()] = count.get(v.lower(), 0) + 1
    used = set()
    for e in entries:
        st = stems[e.rel]
        if count[st.lower()] > 1:
            st = "_".join(Path(e.rel).with_suffix("").parts)
        base, n = st, 2
        while st.lower() in used:
            st, n = f"{base}_{n}", n + 1
        used.add(st.lower())
        stems[e.rel] = st
    return stems


def _task(s, e, stems=None):
    return {"src": str(e.path), "rel": e.rel, "out_dir": str(out_dir_for(s, e)), "stem": (stems or {}).get(e.rel),
            "sidecar_dir": s.get("sidecar_dir"), "temp_dir": s.get("temp_dir"), "settings": s}


def dry_run(s: dict) -> list[dict]:
    """FR-02: pindai dan tentukan status awal tanpa menulis apa pun."""
    validate_dry(s)
    entries = scan(s)
    stems = unique_stems(s, entries)
    man = Manifest(reports_base(s) / "manifest.jsonl") if (reports_base(s) / "manifest.jsonl").exists() else None
    rows = []
    for e in entries:
        row = {"source": e.rel, "source_bytes": e.size, "status": "pending", "outputs": [], "warnings": []}
        try:
            if man and man.is_done(e.rel):
                row["status"] = "skipped"
            else:
                summary, vcs, paths, state = worker.plan(_task(s, e, stems))
                row["topics"] = {c.topic: summary.channel_message_counts.get(c.id) for c in summary.channels.values()}
                row["outputs"] = [{"topic": t, "path": str(p)} for t, p in paths.items()]
                row["status"] = {"pending": "pending", "complete": "skipped", "incomplete": "incomplete"}[state]
        except worker.ConvertError as exc:
            row.update(status="failed", error_code=exc.code, error=exc.message)
        except Exception as exc:
            row.update(status="failed", error_code="mcap_error", error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
    return rows


def validate_dry(s):
    src = Path(s["source_dir"])
    if not src.is_dir():
        raise BatchError("src_missing", path=str(src))
    if s["output_mode"] in ("folder", "ask") and not s.get("output_dir"):
        raise BatchError("output_required")
    if s["output_mode"] == "folder" and is_inside(Path(s["output_dir"]), src) and s.get("recursive", True):
        raise BatchError("output_inside_source")


def _summarize(results, started, s):
    counts = {k: 0 for k in ("success", "skipped", "incomplete", "out_of_sync", "failed")}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = sum(r.get("source_bytes") or 0 for r in results)
    el = time.monotonic() - started
    return {"app_version": __version__, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "counts": counts, "files": len(results), "total_source_bytes": total,
            "elapsed_s": round(el, 2), "throughput_mb_s": round(total / 1e6 / el, 2) if el > 0 else None,
            "settings": s}


def exit_code(summary) -> int:
    c = summary["counts"]
    if c.get("failed"):
        return 1
    if c.get("incomplete") or c.get("out_of_sync"):
        return 2
    return 0


def run(s: dict, on_event=None, control: RunControl | None = None) -> tuple[list[dict], dict]:
    """Jalankan batch. on_event(kind, data): 'warning' | 'start'(i,n,rel) | 'progress'((rel,done,total)) | 'done'(i,n,result).
    control: RunControl untuk Jeda/Lanjut/Batal. Berkas yang belum dimulai saat Batal tidak muncul di hasil."""
    emit = on_event or (lambda *a: None)
    warns = validate(s)
    for w in warns:
        emit("warning", w)
    entries = scan(s)
    stems = unique_stems(s, entries)
    started = time.monotonic()
    base = reports_base(s)
    man = Manifest(base / "manifest.jsonl")
    sweep_partials({out_dir_for(s, e) for e in entries})
    results: list[dict | None] = [None] * len(entries)
    todo = []
    for i, e in enumerate(entries):
        if man.is_done(e.rel):
            rec = man.latest[e.rel]
            results[i] = {**rec, "status": "skipped", "source_bytes": e.size, "note": "manifest"}
            emit("done", (i, len(entries), results[i]))
        else:
            todo.append(i)

    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    stop = threading.Event()
    cancel_ev = control.cancel if control else None
    pause_ev = control.pause if control else None

    def drain():
        while not stop.is_set():
            try:
                ev = q.get(timeout=0.2)
            except Exception:
                continue
            if ev and ev[0] == "progress":
                emit("progress", ev[1:])
    th = threading.Thread(target=drain, daemon=True)
    th.start()

    n_workers = max(1, int(s.get("workers", 2)))

    def new_pool():
        return ProcessPoolExecutor(n_workers, mp_context=ctx, initializer=worker.init_worker,
                                   initargs=(q, cancel_ev, pause_ev))
    pool = new_pool()
    pending: dict = {}
    queue_ = list(todo)
    retry: list[int] = []                    # file yang sedang diisolasi setelah pool rusak: dijalankan satu per satu
    attempts: dict[int, int] = {}
    cancelled = lambda: cancel_ev is not None and cancel_ev.is_set()      # noqa: E731
    paused = lambda: pause_ev is not None and pause_ev.is_set()           # noqa: E731

    def submit_next():
        limit = 1 if retry else n_workers
        while len(pending) < limit and (retry or queue_):
            i = retry.pop(0) if retry else queue_.pop(0)
            e = entries[i]
            man.append({"source": e.rel, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat()})
            emit("start", (i, len(entries), e.rel))
            pending[pool.submit(worker.convert_file, _task(s, e, stems))] = i

    def finish(i, r):
        results[i] = r
        man.append({k: v for k, v in r.items() if k != "settings"})   # status final ditulis paling akhir
        emit("done", (i, len(entries), r))

    try:
        if not cancelled() and not paused():
            submit_next()
        while pending or ((queue_ or retry) and not cancelled()):
            if pending:
                done, _ = wait(list(pending), timeout=0.3, return_when=FIRST_COMPLETED)
            else:
                time.sleep(0.2)
                done = set()
            broken = False
            for fut in done:
                i = pending.pop(fut)
                e = entries[i]
                try:
                    finish(i, fut.result())
                except BrokenProcessPool:
                    broken = True
                    attempts[i] = attempts.get(i, 0) + 1
                    retry.append(i)                       # tinjau di bawah
                except Exception as exc:
                    finish(i, {"source": e.rel, "status": "failed", "error_code": "internal_error",
                               "error": f"{type(exc).__name__}: {exc}", "outputs": [], "warnings": [],
                               "source_bytes": e.size})
            if broken:
                for fut, i in list(pending.items()):      # sisa tugas di pool lama ikut terdampak
                    pending.pop(fut)
                    attempts[i] = attempts.get(i, 0) + 1
                    retry.append(i)
                pool.shutdown(wait=False, cancel_futures=True)
                pool = new_pool()
                keep = []
                for i in retry:
                    if attempts[i] >= 2:                  # sudah dijalankan sendirian dan tetap mati -> pelakunya
                        e = entries[i]
                        finish(i, {"source": e.rel, "status": "failed", "error_code": "worker_crashed",
                                   "error": "proses worker berhenti tiba-tiba", "outputs": [], "warnings": [],
                                   "source_bytes": e.size})
                    else:
                        keep.append(i)
                retry[:] = keep
            if not cancelled() and not paused():
                submit_next()
    except KeyboardInterrupt:
        if cancel_ev is not None:
            cancel_ev.set()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        stop.set()
        th.join(timeout=1)
        pool.shutdown(wait=True, cancel_futures=True)
    final = [r for r in results if r is not None]
    summary = _summarize(final, started, {k: v for k, v in s.items()})
    summary["cancelled"] = cancelled()
    return final, summary
