"""Engine palsu (tanpa libVLC) untuk menguji Controller/PlayerTab/sinkron. Waktu dimajukan manual lewat advance()."""
from mcap2mp4.player.engine import Stats, clamp_rate


class FakeEngine:
    def __init__(self, length_ms=10_000, lengths=None):
        self.lengths = lengths or {}
        self.length_ms_ = length_ms
        self.t = 0
        self.playing = False
        self.st = "NothingSpecial"
        self.rate_ = 1.0
        self.muted_ = False
        self.vol = 100
        self.path = None
        self.calls = []
        self.on_end = None
        self.attached = None
        self.lost = 0
        self.snap_ok = True

    def attach(self, wid): self.attached = wid
    def load(self, p):
        self.path = p; self.st = "Opening"; self.t = 0; self.calls.append(("load", p))
        if p in self.lengths:
            self.length_ms_ = self.lengths[p]
    def play(self): self.playing, self.st = True, "Playing"; self.calls.append(("play",))
    def set_paused(self, p): self.playing = not p; self.st = "Paused" if p else "Playing"; self.calls.append(("pause", p))
    def stop(self): self.playing, self.st, self.t = False, "Stopped", 0; self.calls.append(("stop",))
    state = property(lambda s: s.st)
    is_playing = property(lambda s: s.playing)
    length_ms = property(lambda s: s.length_ms_)
    time_ms = property(lambda s: s.t)
    def seek_ms(self, ms): self.t = int(ms); self.calls.append(("seek", int(ms)))
    def next_frame(self): self.t += 33; self.calls.append(("step",))
    rate = property(lambda s: s.rate_)
    def set_rate(self, r): self.rate_ = clamp_rate(r); self.calls.append(("rate", self.rate_))
    volume = property(lambda s: s.vol)
    def set_volume(self, v): self.vol = v
    muted = property(lambda s: s.muted_)
    def set_muted(self, m): self.muted_ = bool(m)
    audio_tracks = 1
    video_size = (1920, 1080)
    def stats(self): return Stats(decoded=100, displayed=100 - self.lost, lost=self.lost)
    def snapshot(self, path): self.calls.append(("snap", str(path))); return self.snap_ok
    def release(self): self.calls.append(("release",))

    def advance(self, ms):
        if self.playing:
            self.t = min(self.length_ms_, self.t + int(ms * self.rate_))
            if self.t >= self.length_ms_:
                self.playing, self.st = False, "Ended"
                if self.on_end:
                    self.on_end()
