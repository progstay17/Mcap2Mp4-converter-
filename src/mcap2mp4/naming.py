"""Penamaan keluaran mengikuti nama file sumber (PRD FR-16)."""
from __future__ import annotations
import re
from datetime import datetime

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_BASE = 200

def sanitize(text: str, fallback: str = "channel") -> str:
    s = _ILLEGAL.sub("_", text).strip(" .")
    if s.upper() in _RESERVED:
        s = "_" + s
    return s or fallback

def topic_label(topic: str) -> str:
    """'/a/cam/0' -> 'a_cam_0'."""
    return sanitize(topic.strip("/").replace("/", "_"))

class NameCollisionError(ValueError):
    pass


def build_names(stem: str, topics: list[str], pattern: str | None = None,
                date: datetime | None = None, extension: str = ".mp4",
                fail_on_collision: bool = False) -> dict[str, str]:
    """Kembalikan {topik: nama_file}. Satu channel -> {nama}.mp4; banyak -> {nama}_{topik}.mp4."""
    if pattern is None:
        pattern = "{nama}" if len(topics) == 1 else "{nama}_{topik}"
    day = (date or datetime.now()).strftime("%Y%m%d")
    out, used = {}, set()
    for i, topic in enumerate(topics):
        base = pattern.format(nama=sanitize(stem, "rekaman"), topik=topic_label(topic), indeks=i, tanggal=day)
        base = sanitize(base)[:MAX_BASE].rstrip(" .") or "rekaman"
        name, n = base, 2
        while (name + extension).lower() in used:      # tabrakan nama -> nomor otomatis
            if fail_on_collision:
                raise NameCollisionError(f"nama keluaran bentrok: {name}{extension}")
            name, n = f"{base}_{n}", n + 1
        used.add((name + extension).lower())
        out[topic] = name + extension
    return out
