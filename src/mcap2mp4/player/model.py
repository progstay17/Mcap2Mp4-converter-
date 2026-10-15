"""Model 'rekaman' untuk pemutar (PRD FR-126/127, Lampiran E). Tanpa Qt/libVLC.

Satu Recording = satu sesi (>=1 bagian) atau satu file. Tiap Part berisi MP4 per kamera (topik -> path). Timeline total =
penjumlahan durasi bagian; locate() memetakan posisi total ke (bagian, offset)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Part:
    cameras: dict                      # topik -> path MP4 (urut topik naik = kiri ke kanan)
    duration_s: float = 0.0
    has_audio: bool = False
    sidecar: str = ""                  # path .metadata.json (untuk penampil metadata)

    @property
    def paths(self): return [self.cameras[t] for t in sorted(self.cameras)]

    def to_dict(self):
        d = {"cameras": dict(self.cameras), "duration_s": self.duration_s, "has_audio": self.has_audio}
        if self.sidecar:
            d["sidecar"] = self.sidecar
        return d

    @classmethod
    def from_dict(cls, d): return cls(dict(d["cameras"]), float(d.get("duration_s", 0)), bool(d.get("has_audio", False)), d.get("sidecar", ""))


@dataclass
class Recording:
    title: str
    parts: list = field(default_factory=list)
    kind: str = "single"               # single | session | mcap (belum dikonversi)
    source_files: list = field(default_factory=list)   # .mcap asal (untuk 'konversi lalu putar')
    converted: bool = True
    folder: str = ""                   # folder asal .mcap (rekaman belum dikonversi)

    @property
    def playable(self): return bool(self.parts) and self.converted

    @property
    def cameras(self): return sorted(self.parts[0].cameras) if self.parts else []

    @property
    def camera_count(self): return len(self.cameras)

    @property
    def duration_s(self): return sum(p.duration_s for p in self.parts)

    @property
    def has_audio(self): return any(p.has_audio for p in self.parts)

    @property
    def part_count(self): return len(self.parts)

    def part_starts_ms(self):
        out, acc = [], 0.0
        for p in self.parts:
            out.append(int(round(acc * 1000)))
            acc += p.duration_s
        return out

    def locate(self, total_ms: int):
        """(indeks bagian, offset ms di bagian itu) untuk posisi total; dijepit ke rentang rekaman."""
        starts = self.part_starts_ms()
        total_ms = max(0, int(total_ms))
        idx = 0
        for i, st in enumerate(starts):
            if total_ms >= st:
                idx = i
        off = total_ms - starts[idx]
        dur = int(round(self.parts[idx].duration_s * 1000))
        return idx, min(off, max(0, dur - 1))

    def to_dict(self):
        return {"kind": self.kind, "title": self.title, "duration_s": round(self.duration_s, 3),
                "parts": [p.to_dict() for p in self.parts], "source_files": list(self.source_files)}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("title", "?"), [Part.from_dict(p) for p in d.get("parts", [])], d.get("kind", "single"),
                   list(d.get("source_files", [])), True)

    def missing_files(self):
        return [p for part in self.parts for p in part.cameras.values() if not Path(p).exists()]
