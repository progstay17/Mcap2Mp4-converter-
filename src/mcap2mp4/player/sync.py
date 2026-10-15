"""Sinkron dua kamera dan pemutaran rekaman multi-bagian (FR-124, FR-127). Tanpa Qt/libVLC (engine disuntik).

SyncController  = Controller + koreksi selisih: master = engine pertama (audio); engine lain dikoreksi lewat penyesuaian laju
                  kecil bila selisih > ambang, atau seek bila selisih besar. Log selisih untuk ukuran AC-26.
RecordingPlayer = memutar Recording: timeline total, pemetaan seek ke (bagian, offset), bagian berikutnya dimuat di engine
                  cadangan >= `preload_s` sebelum batas lalu ditukar saat bagian aktif selesai (jeda sambungan diukur)."""
from __future__ import annotations

import time

from .controller import Controller
from .engine import clamp_rate

DEFAULT_THRESHOLD_MS = 40          # ~1,2 frame pada 30 fps (player_sync_threshold_ms)
HARD_SEEK_MS = 400                 # selisih sebesar ini: seek langsung, bukan penyesuaian laju
TRIM_MIN, TRIM_MAX = 0.04, 0.25    # penyesuaian laju relatif untuk mengejar/menunggu (sebanding dengan selisih)


def _pos(e) -> float:
    """Posisi untuk perbandingan antar-kamera: estimasi terinterpolasi bila engine menyediakannya (libVLC nyata), jika tidak get_time()."""
    est = getattr(e, "time_est_ms", None)
    return float(est if est is not None else e.time_ms)


class SyncController(Controller):
    def __init__(self, engines, threshold_ms=DEFAULT_THRESHOLD_MS, auto_mute=True, settle_s=0.6, clock=time.monotonic):
        super().__init__(engines, auto_mute)
        self.threshold_ms = threshold_ms
        self.settle_s = settle_s                  # setelah putar/lanjut/seek, transien start tidak dikoreksi
        self._clock = clock
        self._settle_until = 0.0
        self.drift_log: list[int] = []           # selisih terakhir tiap tick (ms, slave - master), satu per slave
        self.corrections = {"rate": 0, "seek": 0}
        self._trimmed = set()

    def _settle(self):
        self._settle_until = self._clock() + self.settle_s

    def play(self):
        super().play(); self._settle()

    def resume(self):
        super().resume(); self._settle()

    def seek_ms(self, ms):
        r = super().seek_ms(ms); self._settle()
        return r

    def tick(self):
        """Satu putaran koreksi. Panggil ~tiap 100 ms saat memutar."""
        if len(self.engines) < 2 or self._clock() < self._settle_until:
            return []
        m = self.master
        playing = m.is_playing
        mt = _pos(m)
        out = []
        for i, e in enumerate(self.engines[1:], start=1):
            d = int(round(_pos(e) - mt))
            self.drift_log.append(d)
            if abs(d) > HARD_SEEK_MS or (not playing and abs(d) > self.threshold_ms):
                e.seek_ms(int(mt))
                self.corrections["seek"] += 1
                self._restore(i, e)
                out.append((i, "seek", d))
            elif playing and abs(d) > self.threshold_ms:
                trim = min(TRIM_MAX, max(TRIM_MIN, abs(d) / 1000))
                e.set_rate(clamp_rate(self.rate * (1 - trim if d > 0 else 1 + trim)))   # d>0: slave di depan -> perlambat
                self._trimmed.add(i)
                self.corrections["rate"] += 1
                out.append((i, "rate", d))
            elif i in self._trimmed and abs(d) <= self.threshold_ms / 2:
                self._restore(i, e)
                out.append((i, "ok", d))
        return out

    def _restore(self, i, e):
        self._trimmed.discard(i)
        e.set_rate(self.rate)

    def drift_stats(self):
        if not self.drift_log:
            return {"max_ms": 0, "mean_ms": 0.0, "samples": 0}
        a = [abs(x) for x in self.drift_log]
        return {"max_ms": max(a), "mean_ms": sum(a) / len(a), "samples": len(a)}


class _Group:
    """Satu bagian: engine per kamera + pengendalinya."""
    def __init__(self, engines, paths, threshold_ms, auto_mute, settle_s=0.6, clock=time.monotonic):
        self.engines, self.paths = engines, paths
        self.ctrl = SyncController(engines, threshold_ms, auto_mute, settle_s, clock)
        self.state = "new"                        # new | loading | ready | active

    def release(self):
        for e in self.engines:
            try:
                e.release()
            except Exception:
                pass


class RecordingPlayer:
    def __init__(self, engine_factory, threshold_ms=DEFAULT_THRESHOLD_MS, preload_s=3.0, auto_mute=True, clock=time.monotonic,
                 settle_s=0.6):
        self.factory = engine_factory
        self.threshold_ms = threshold_ms
        self.preload_s = max(2.0, preload_s)       # FR-127: dimuat >= 2 dtk sebelum batas
        self.auto_mute = auto_mute
        self.clock = clock
        self.settle_s = settle_s
        self.rec = None
        self.part_idx = 0
        self.active: _Group | None = None
        self.standby: _Group | None = None
        self.rate = 1.0
        self.volume = 80
        self.user_muted = False
        self.on_part_change = None
        self.gaps_ms: list[float] = []             # jeda sambungan terukur (FR-127/AC-29)
        self._retire: list[_Group] = []
        self._ended_at = None
        self._pending_gap = None

    # -- membuka
    def _make_group(self, part) -> _Group:
        paths = part.paths
        g = _Group([self.factory() for _ in paths], paths, self.threshold_ms, self.auto_mute, self.settle_s, self.clock)
        g.ctrl.rate, g.ctrl.volume, g.ctrl.user_muted = self.rate, self.volume, self.user_muted
        return g

    def open(self, rec, start_ms=0, autoplay=True):
        self.close()
        self.rec = rec
        idx, off = rec.locate(start_ms)
        self._start_part(idx, off, autoplay)

    def _start_part(self, idx, off_ms=0, play=True):
        self.part_idx = idx
        g = self._make_group(self.rec.parts[idx])
        for e, p in zip(g.engines, g.paths):
            e.load(p)
        g.state = "active"
        self.active = g
        g.ctrl.apply_rate_and_audio()
        for e in g.engines:
            e.set_rate(self.rate)
        if play:
            g.ctrl.play()
        if off_ms:
            g.ctrl.seek_ms(off_ms)
        if self.on_part_change:
            self.on_part_change(idx)

    def close(self):
        for g in [self.active, self.standby] + self._retire:
            if g:
                g.release()
        self.active = self.standby = None
        self._retire = []
        self.rec = None

    # -- tampilan/timeline
    @property
    def engines(self): return self.active.engines if self.active else []

    @property
    def length_ms(self):
        if not self.rec:
            return 0
        known = int(round(self.rec.duration_s * 1000))
        return known or (self.active.ctrl.length_ms if self.active else 0)       # MP4 tanpa sidecar: durasi dari engine

    @property
    def time_ms(self):
        if not self.active:
            return 0
        return self.rec.part_starts_ms()[self.part_idx] + max(0, self.active.ctrl.time_ms)

    @property
    def is_playing(self): return bool(self.active and self.active.ctrl.master.is_playing)

    @property
    def effectively_muted(self): return bool(self.active and self.active.ctrl.effectively_muted)

    @property
    def finished(self):
        """Bagian terakhir sudah selesai diputar (untuk lanjut otomatis di playlist)."""
        return bool(self.active and self.rec and self.part_idx == len(self.rec.parts) - 1
                    and self.active.ctrl.master.state in ("Ended", "Stopped") and not self.active.ctrl.master.is_playing)

    # -- perintah (diteruskan ke bagian aktif)
    def play(self): self.active.ctrl.play()
    def pause(self): self.active.ctrl.pause()
    def resume(self): self.active.ctrl.resume()

    def toggle_play(self):
        if self.active:
            self.active.ctrl.toggle_play()

    def seek_ms(self, total_ms):
        total_ms = int(min(max(0, total_ms), max(0, self.length_ms - 1)))
        if len(self.rec.parts) == 1:
            return self.active.ctrl.seek_ms(total_ms)
        idx, off = self.rec.locate(total_ms)
        was_playing = self.is_playing
        if idx != self.part_idx:
            self._drop_standby()
            old = self.active
            self._start_part(idx, off, play=was_playing)
            if old:
                self._retire.append(old)
        else:
            self.active.ctrl.seek_ms(off)
        return total_ms

    def seek_rel(self, seconds): return self.seek_ms(self.time_ms + int(seconds * 1000))
    def seek_fraction(self, f): return self.seek_ms(int(self.length_ms * min(1.0, max(0.0, f))))
    def step_frame(self): self.active.ctrl.step_frame()

    def set_rate(self, r):
        self.rate = clamp_rate(r)
        for g in (self.active, self.standby):
            if g:
                g.ctrl.set_rate(self.rate)
        return self.rate

    def faster(self):
        from .engine import next_speed
        return self.set_rate(next_speed(self.rate, +1))

    def slower(self):
        from .engine import next_speed
        return self.set_rate(next_speed(self.rate, -1))

    def toggle_mute(self):
        self.user_muted = self.active.ctrl.toggle_mute() if self.active else not self.user_muted
        return self.user_muted

    def set_volume(self, v):
        self.volume = int(min(100, max(0, v)))
        if self.active:
            self.active.ctrl.set_volume(self.volume)

    def snapshot(self, folder, stem="frame"): return self.active.ctrl.snapshot(folder, stem) if self.active else []

    # -- pemuatan bagian berikutnya (FR-127)
    def _drop_standby(self):
        if self.standby:
            self._retire.append(self.standby)
            self.standby = None

    def tick(self):
        """Dipanggil ~100 ms. Koreksi sinkron, siapkan bagian berikutnya, tukar di batas, lepaskan engine lama."""
        if not self.active:
            return
        self.active.ctrl.tick()
        g = self.active
        if self._pending_gap is not None and g.ctrl.master.is_playing and g.ctrl.master.time_ms > 0:
            self.gaps_ms.append((self.clock() - self._pending_gap) * 1000)       # jeda nyata: batas -> bagian baru maju
            self._pending_gap = None
        nxt = self.part_idx + 1
        has_next = nxt < len(self.rec.parts)
        remaining_ms = int(self.rec.parts[self.part_idx].duration_s * 1000) - max(0, g.ctrl.time_ms)
        if has_next and self.standby is None and remaining_ms <= self.preload_s * 1000 / max(self.rate, 0.25):
            sb = self._make_group(self.rec.parts[nxt])
            for e, p in zip(sb.engines, sb.paths):
                e.load(p)
                e.set_muted(True)
                e.set_rate(self.rate)
                e.play()
            sb.state = "loading"
            self.standby = sb
        if self.standby is not None and self.standby.state == "loading":
            e0 = self.standby.engines[0]
            if e0.state == "Playing" and e0.time_ms >= 0:
                for e in self.standby.engines:
                    e.set_paused(True)
                    e.seek_ms(0)
                self.standby.state = "ready"
        ended = g.ctrl.master.state in ("Ended", "Stopped") or (
            remaining_ms <= 30 and not g.ctrl.master.is_playing and g.ctrl.master.state != "Paused")
        if ended and has_next:
            t0 = self._ended_at or self.clock()
            if self.standby is not None and self.standby.state == "ready":
                self._swap(t0)
            elif self.standby is None:
                self._swap_cold(t0)
            else:
                self._ended_at = t0                     # standby masih memuat: tunggu tick berikut
        elif ended:
            self._ended_at = None
        for old in self._retire:
            old.release()
        self._retire.clear()

    def _swap(self, t0):
        old, self.active, self.standby = self.active, self.standby, None
        self.part_idx += 1
        self.active.state = "active"
        c = self.active.ctrl
        c.apply_rate_and_audio()
        c.resume()
        self._retire.append(old)
        self._pending_gap = t0
        self._ended_at = None
        if self.on_part_change:
            self.on_part_change(self.part_idx)

    def _swap_cold(self, t0):
        old = self.active
        self._start_part(self.part_idx + 1, 0, True)
        self._retire.append(old)
        self._pending_gap = t0
        self._ended_at = None

    def status(self) -> dict:
        st = self.active.ctrl.status() if self.active else {}
        st.update(time_ms=self.time_ms, length_ms=self.length_ms, part=self.part_idx, parts=len(self.rec.parts) if self.rec else 0)
        return st

    def drift_stats(self): return self.active.ctrl.drift_stats() if self.active else {}
