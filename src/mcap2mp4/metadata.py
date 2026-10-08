"""Metadata tiga lapis + sidecar JSON (PRD FR-40..FR-46).
Lapis A: MCAP (summary). Lapis B: bitstream H.265. Lapis C: MP4 hasil via ExifTool (bila tersedia)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def find_exiftool(custom=None):
    cands = [Path(custom)] if custom else []
    cands += [app_root() / "third_party" / "exiftool" / "exiftool.exe",
              Path(sys.executable).parent / "third_party" / "exiftool" / "exiftool.exe"]
    for c in cands:
        if c.is_file():
            return c
    return None


def exiftool_read(path, exe, raw=False, timeout=120) -> dict:
    """Lapis C: -j -G1 -a -s (FR-42). Satu panggilan per file (cukup untuk M1/M2)."""
    cmd = [str(exe), "-j", "-G1", "-a", "-s"] + (["-n"] if raw else []) + [str(path)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or f"exiftool rc={r.returncode}")
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    return data[0] if data else {}


def iso_utc(ns):
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def mcap_layer(summary) -> dict:
    """Lapis A (FR-40) dari summary."""
    span = None
    if summary.message_start_time is not None and summary.message_end_time is not None:
        span = (summary.message_end_time - summary.message_start_time) / 1e9
    topics = []
    for ch in sorted(summary.channels.values(), key=lambda c: c.id):
        sc = summary.schemas.get(ch.schema_id)
        n = summary.channel_message_counts.get(ch.id)
        topics.append({"id": ch.id, "topic": ch.topic, "schema": sc.name if sc else None,
                       "schema_encoding": sc.encoding if sc else None, "message_encoding": ch.message_encoding,
                       "messages": n, "rate_hz": round(n / span, 3) if n and span else None,
                       "metadata": ch.metadata})
    return {"profile": summary.profile, "library": summary.library, "messages": summary.message_count,
            "start_time_ns": summary.message_start_time, "end_time_ns": summary.message_end_time,
            "start_time_utc": iso_utc(summary.message_start_time), "end_time_utc": iso_utc(summary.message_end_time),
            "duration_s": span, "chunks": summary.chunk_count, "compression": sorted(summary.compression),
            "attachments": summary.attachment_count, "metadata_records": summary.metadata_count,
            "summary_from_index": summary.from_index, "topics": topics}


def write_sidecar(path, payload: dict):
    """Tulis atomik; tidak menimpa (pemanggil memberi path bebas)."""
    path = Path(path)
    tmp = path.with_name("." + path.name + ".partial")
    doc = {"generator": f"mcap2mp4 {__version__}", "generated_utc": datetime.now(timezone.utc).isoformat(), **payload}
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.link(tmp, path)
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        raise
    except OSError:
        os.rename(tmp, path)
    tmp.unlink(missing_ok=True)
