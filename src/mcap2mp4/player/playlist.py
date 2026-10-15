"""Playlist (FR-126, Lampiran E `.m2playlist`). Entri = Recording, bukan file. Tanpa Qt/libVLC."""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

from .model import Recording

MODES = ("sequential", "repeat", "shuffle")


class Playlist:
    def __init__(self, rng=None):
        self.items: list[Recording] = []
        self.index = -1
        self.play_mode = "sequential"
        self.state = {"index": 0, "position_s": 0.0, "speed": 1.0}
        self.rng = rng or random.Random()
        self._history: list[int] = []

    # -- isi
    def add(self, rec: Recording, at: int | None = None) -> int:
        at = len(self.items) if at is None else max(0, min(at, len(self.items)))
        self.items.insert(at, rec)
        if self.index >= at:
            self.index += 1
        return at

    def remove(self, i: int):
        if not 0 <= i < len(self.items):
            return
        del self.items[i]
        self._history = [h - (h > i) for h in self._history if h != i]
        if self.index == i:
            self.index = -1                          # entri yang diputar dihapus: tidak ada yang aktif
        elif self.index > i:
            self.index -= 1

    def move(self, src: int, dst: int):
        if not (0 <= src < len(self.items)) or not (0 <= dst < len(self.items)) or src == dst:
            return
        cur = self.items[self.index] if 0 <= self.index < len(self.items) else None
        self.items.insert(dst, self.items.pop(src))
        if cur is not None:
            self.index = next(i for i, r in enumerate(self.items) if r is cur)

    def clear(self):
        self.items.clear()
        self.index = -1
        self._history.clear()

    @property
    def current(self): return self.items[self.index] if 0 <= self.index < len(self.items) else None

    # -- navigasi
    def select(self, i: int):
        if 0 <= i < len(self.items):
            if self.index != i and self.index >= 0:
                self._history.append(self.index)
            self.index = i
        return self.current

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("mode harus " + "|".join(MODES))
        self.play_mode = mode

    def next(self):
        """Entri berikutnya menurut mode; None bila selesai (mode berurutan di akhir daftar)."""
        n = len(self.items)
        if not n:
            return None
        if self.play_mode == "shuffle":
            cand = [i for i in range(n) if i != self.index] or [0]
            return self.select(self.rng.choice(cand))
        nxt = self.index + 1
        if nxt >= n:
            if self.play_mode == "repeat":
                nxt = 0
            else:
                return None
        return self.select(nxt)

    def prev(self):
        if self.play_mode == "shuffle" and self._history:
            self.index = self._history.pop()
            return self.current
        if self.index > 0:
            return self.select(self.index - 1)
        return self.current

    # -- simpan / muat (Lampiran E)
    def to_dict(self):
        return {"version": 1, "play_mode": self.play_mode, "items": [r.to_dict() for r in self.items],
                "state": dict(self.state, index=max(0, self.index))}

    def save(self, path, position_s=None, speed=None):
        if position_s is not None:
            self.state["position_s"] = float(position_s)
        if speed is not None:
            self.state["speed"] = float(speed)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path, rng=None) -> "Playlist":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        pl = cls(rng)
        pl.play_mode = d.get("play_mode", "sequential") if d.get("play_mode") in MODES else "sequential"
        pl.items = [Recording.from_dict(x) for x in d.get("items", [])]
        pl.state = {"index": 0, "position_s": 0.0, "speed": 1.0, **(d.get("state") or {})}
        pl.index = pl.state["index"] if 0 <= pl.state["index"] < len(pl.items) else -1
        return pl
