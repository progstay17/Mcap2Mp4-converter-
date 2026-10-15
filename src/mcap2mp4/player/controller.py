"""Pengendali pemutar: logika perintah yang tidak bergantung pada Qt maupun libVLC (diuji dengan engine palsu).

Satu Controller mengendalikan satu atau lebih Engine (M7: satu kamera; M8 menambah sinkron dua kamera di sync.py).
Aturan: perintah putar/jeda/seek/kecepatan diterapkan ke semua engine; audio hanya dari engine pertama (master).
"""
from __future__ import annotations

import time
from pathlib import Path

from .engine import clamp_rate, next_speed

# FR-131: pintasan -> nama perintah (dipetakan ke QShortcut di widgets.py; ditampilkan di bantuan lewat i18n)
SHORTCUTS = {
    "Space": "toggle_play", "Left": "back_5", "Right": "fwd_5", "Shift+Left": "back_30", "Shift+Right": "fwd_30",
    "[": "slower", "]": "faster", ".": "step_frame", "M": "toggle_mute", "F": "fullscreen", "Ctrl+O": "open",
    "Ctrl+S": "snapshot",
}
AUTO_MUTE_ABOVE = 2.0                  # FR-123: di atas 2x audio dibisukan otomatis (dapat dimatikan)


class Controller:
    def __init__(self, engines, auto_mute=True):
        self.engines = list(engines)
        self.auto_mute = auto_mute
        self.user_muted = False
        self._auto_muted = False
        self.rate = 1.0
        self.volume = 80

    @property
    def master(self): return self.engines[0]

    # -- media
    def open(self, paths, autoplay=True):
        for e, p in zip(self.engines, paths):
            e.load(p)
        self.apply_rate_and_audio()
        if autoplay:
            self.play()

    def play(self):
        for e in self.engines:
            e.play()
        self.apply_rate_and_audio()

    def pause(self):
        for e in self.engines:
            e.set_paused(True)

    def resume(self):
        for e in self.engines:
            e.set_paused(False)

    def toggle_play(self):
        if self.master.state in ("Stopped", "Ended", "NothingSpecial"):
            self.master.stop()
            self.play()
        elif self.master.is_playing:
            self.pause()
        else:
            self.resume()

    def stop(self):
        for e in self.engines:
            e.stop()

    # -- posisi
    @property
    def length_ms(self): return self.master.length_ms

    @property
    def time_ms(self): return self.master.time_ms

    def seek_ms(self, ms):
        ms = int(min(max(0, ms), max(0, self.length_ms - 1)))
        for e in self.engines:
            e.seek_ms(ms)
        return ms

    def seek_rel(self, seconds):
        return self.seek_ms(self.time_ms + int(seconds * 1000))

    def seek_fraction(self, f):
        return self.seek_ms(int(self.length_ms * min(1.0, max(0.0, f))))

    def step_frame(self):
        """Maju satu frame (FR-122): jeda dulu, lalu maju."""
        if self.master.is_playing:
            self.pause()
        for e in self.engines:
            e.next_frame()

    # -- kecepatan & suara
    def set_rate(self, r):
        self.rate = clamp_rate(r)
        for e in self.engines:
            e.set_rate(self.rate)
        self.apply_rate_and_audio()
        return self.rate

    def faster(self): return self.set_rate(next_speed(self.rate, +1))

    def slower(self): return self.set_rate(next_speed(self.rate, -1))

    def apply_rate_and_audio(self):
        """Audio: hanya master; bisu otomatis di atas 2x kecuali pengguna mematikan fitur ini."""
        self._auto_muted = bool(self.auto_mute and self.rate > AUTO_MUTE_ABOVE + 1e-9)
        for i, e in enumerate(self.engines):
            e.set_muted(i > 0 or self.user_muted or self._auto_muted)
        self.master.set_volume(self.volume)

    def toggle_mute(self):
        self.user_muted = not self.user_muted
        self.apply_rate_and_audio()
        return self.user_muted

    @property
    def effectively_muted(self): return self.user_muted or self._auto_muted

    def set_volume(self, v):
        self.volume = int(min(100, max(0, v)))
        self.master.set_volume(self.volume)

    # -- tangkapan & status
    def snapshot(self, folder, stem="frame"):
        """PNG per kamera pada posisi sekarang (FR-130). Kembalikan daftar path yang berhasil."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        ms = self.time_ms
        out = []
        for i, e in enumerate(self.engines):
            p = folder / f"{stem}_cam{i}_{stamp}_{ms}ms.png"
            if e.snapshot(p):
                out.append(p)
        return out

    def status(self) -> dict:
        s = self.master.stats()
        return {"state": self.master.state, "time_ms": self.time_ms, "length_ms": self.length_ms, "rate": self.rate,
                "muted": self.effectively_muted, "volume": self.volume, "lost": s.lost, "decoded": s.decoded,
                "displayed": s.displayed}

    def release(self):
        for e in self.engines:
            e.release()
