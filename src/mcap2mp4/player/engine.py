"""Pembungkus libVLC 3.0.x (PRD 0.3 FR-120, FR-123, FR-130, FR-132).

Satu Engine = satu pemutar libVLC (satu kamera). Modul ini tidak mengimpor PySide6; python-vlc dimuat malas sehingga
aplikasi tetap berjalan bila libVLC tidak ada (tab Pemutar menampilkan pesan, konversi tidak terpengaruh).

Fungsi murni (diuji tanpa libVLC): clamp_rate, next_speed, format_time, build_args, vout_candidates.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)       # FR-123
MIN_RATE, MAX_RATE = 0.25, 8.0
WIN_VOUTS = ("direct3d11", "directdraw", "wingdi")               # urutan fallback FR-120 (nama modul VLC 3.x; verifikasi di Windows)


class PlayerUnavailable(RuntimeError):
    """libVLC/python-vlc tidak dapat dimuat."""


def clamp_rate(r: float) -> float:
    return round(min(MAX_RATE, max(MIN_RATE, float(r))), 4)


def next_speed(cur: float, direction: int) -> float:
    """Langkah kecepatan `[`/`]` (FR-131). Dari nilai kustom, melompat ke nilai baku terdekat searah."""
    cur = float(cur)
    if direction > 0:
        for s in SPEEDS:
            if s > cur + 1e-9:
                return s
        return SPEEDS[-1]
    for s in reversed(SPEEDS):
        if s < cur - 1e-9:
            return s
    return SPEEDS[0]


def format_time(ms: float | None, millis: bool = False) -> str:
    if ms is None or ms < 0:
        ms = 0
    total = int(ms // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return f"{base}.{int(ms % 1000):03d}" if millis else base


def vout_candidates(platform: str | None = None, forced: str | None = None) -> list[str | None]:
    """Urutan keluaran video yang dicoba. None = pilihan otomatis libVLC."""
    if forced:
        return [forced]
    plat = platform or sys.platform
    return list(WIN_VOUTS) + [None] if plat.startswith("win") else [None]


def build_args(hw_decode="auto", threads=None, vout=None, headless=False) -> list[str]:
    """Argumen instance libVLC. headless=True untuk uji tanpa layar/audio."""
    args = ["--no-video-title-show", "--no-osd", "--quiet", "--no-stats", "--no-snapshot-preview",
            f"--avcodec-threads={int(threads or os.cpu_count() or 2)}",
            "--avcodec-hw=none" if hw_decode == "off" else "--avcodec-hw=any"]
    if vout:
        args.append(f"--vout={vout}")
    if headless:
        args += ["--vout=dummy", "--aout=dummy"]
    return args


def bundled_vlc_dir() -> Path | None:
    root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    d = root / "third_party" / "vlc"
    return d if d.is_dir() else None


def prepare_environment():
    """Arahkan python-vlc ke libVLC yang ikut paket (bila ada) sebelum `import vlc`."""
    d = bundled_vlc_dir()
    if d is None:
        return
    dll = d / ("libvlc.dll" if sys.platform.startswith("win") else "libvlc.so")
    if dll.exists():
        os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(dll))
    if (d / "plugins").is_dir():
        os.environ.setdefault("VLC_PLUGIN_PATH", str(d / "plugins"))
    if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(d))


def load_vlc():
    prepare_environment()
    try:
        import vlc
        vlc.libvlc_get_version()
    except Exception as exc:                       # ImportError, OSError, AttributeError (libVLC tak ditemukan)
        raise PlayerUnavailable(str(exc)) from exc
    return vlc


class VmemSink:
    """Penerima frame libVLC (video callbacks / 'vmem'): VLC mengonversi ke BGRA (RV32) langsung ke buffer kita.

    Dipilih sebagai bawaan karena tidak bergantung pada plugin keluaran video (Direct3D/GDI) maupun GPU: berjalan di RDP/VPS
    dan membuat penggambaran kiri-kanan serta tangkapan PNG sepenuhnya di tangan aplikasi. Dua buffer bergantian;
    `latest()` mengembalikan buffer depan. Tanpa Qt."""

    def __init__(self):
        self.w = self.h = self.pitch = 0
        self.serial = 0
        self._bufs = [None, None]
        self._back = 0
        self._lock = threading.Lock()
        self._refs = []

    def configure(self, player, vlc, w, h):
        """Panggil sebelum play(): tetapkan format RV32 w x h (VLC menskalakan keluaran ke ukuran ini)."""
        self.w, self.h = int(w), int(h)
        self.pitch = ((self.w * 4 + 31) // 32) * 32
        size = self.pitch * self.h
        self._bufs = [ctypes.create_string_buffer(size), ctypes.create_string_buffer(size)]
        self._back, self.serial = 0, 0

        @vlc.CallbackDecorators.VideoLockCb
        def lock(opaque, planes):
            planes[0] = ctypes.cast(self._bufs[self._back], ctypes.c_void_p)
            return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def unlock(opaque, picture, planes):
            return None

        @vlc.CallbackDecorators.VideoDisplayCb
        def display(opaque, picture):
            with self._lock:
                self._back ^= 1                  # buffer yang baru ditulis menjadi 'depan'
                self.serial += 1
            return None

        self._refs = [lock, unlock, display]      # cegah GC (callback dipanggil dari thread VLC)
        player.video_set_callbacks(lock, unlock, display, None)
        player.video_set_format("RV32", self.w, self.h, self.pitch)

    def latest(self):
        """(buffer depan, w, h, pitch, serial) atau None bila belum ada frame."""
        with self._lock:
            if not self.serial or not self._bufs[0]:
                return None
            return self._bufs[self._back ^ 1], self.w, self.h, self.pitch, self.serial

    def save_png(self, path) -> bool:
        fr = self.latest()
        if fr is None:
            return False
        buf, w, h, pitch, _ = fr
        raw = bytes(buf)
        rows = []
        for y in range(h):
            row = raw[y * pitch: y * pitch + w * 4]
            rgb = bytearray(w * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]      # BGRA -> RGB
            rows.append(b"\0" + bytes(rgb))
        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"".join(rows), 1)) + chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)
        return True


def mp4_video_dims(path):
    """(lebar, tinggi) trak video pertama dari tkhd MP4; None bila tidak terbaca (python-vlc tidak andal membaca ini)."""
    from .. import verify
    try:
        path = os.fspath(path)
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            tops = verify._top_level(f, size)
            moov = next((t for t in tops if t[0] == b"moov"), None)
            if moov is None:
                return None
            f.seek(moov[1])
            buf = f.read(moov[2] - moov[1])

        def find(typ, start, end):
            return [(s_, e_) for t_, s_, e_ in verify._boxes(buf, start, end) if t_ == typ]

        for ts, te in find(b"trak", 0, len(buf)):
            for ks, ke in find(b"tkhd", ts, te):
                ver = buf[ks]
                off = ks + (88 if ver == 1 else 76)
                w, h = struct.unpack_from(">II", buf, off)
                w, h = w >> 16, h >> 16
                if w and h:
                    return w, h
    except Exception:
        return None
    return None


def fit_size(w, h, max_w):
    """Ukuran keluaran vmem: pertahankan rasio, batasi lebar (0 = asli), genap agar konversi YUV mulus."""
    if max_w and w > max_w:
        h = int(round(h * max_w / w))
        w = max_w
    return max(2, w & ~1), max(2, h & ~1)


@dataclass
class Stats:
    decoded: int = 0
    displayed: int = 0
    lost: int = 0
    lost_audio: int = 0


@dataclass
class EngineOptions:
    hw_decode: str = "off"             # off (bawaan: vmem butuh frame CPU) | auto  (setting player_hw_decode)
    threads: int | None = None
    vout: str | None = None
    headless: bool = False
    render: str = "vmem"               # vmem (bawaan; frame digambar aplikasi) | native (jendela libVLC)
    max_width: int = 1280              # batas lebar buffer vmem (0 = ukuran asli)
    extra: list = field(default_factory=list)


class Engine:
    """Satu kamera. Semua waktu dalam milidetik."""

    def __init__(self, opts: EngineOptions | None = None, vlc_module=None):
        self.opts = opts or EngineOptions()
        self.vlc = vlc_module or load_vlc()
        self.instance = self.vlc.Instance(build_args(self.opts.hw_decode, self.opts.threads, self.opts.vout,
                                                     self.opts.headless) + list(self.opts.extra))
        if self.instance is None:
            raise PlayerUnavailable("libVLC gagal dibuat (argumen/plugin tidak valid)")
        self.player = self.instance.media_player_new()
        self.media = None
        self.path = None
        self.on_end = None             # callback tanpa argumen saat media selesai
        self.sink = VmemSink() if self.opts.render == "vmem" else None
        self._anchor = None            # (waktu monotonic, ms) saat nilai get_time() TERAKHIR berubah
        self._poll_stop = threading.Event()
        self._poller = None
        self._rate = 1.0
        ev = self.player.event_manager()
        ev.event_attach(self.vlc.EventType.MediaPlayerEndReached, lambda e: self.on_end and self.on_end())

    # -- jendela
    def attach(self, win_id: int):
        if self.opts.headless:
            return
        if sys.platform.startswith("win"):
            self.player.set_hwnd(int(win_id))
        elif sys.platform == "darwin":
            self.player.set_nsobject(int(win_id))
        else:
            self.player.set_xwindow(int(win_id))

    def _video_dims(self):
        return mp4_video_dims(self.path) or (1280, 720)

    # -- media
    def load(self, path):
        self.path = os.path.normpath(str(path))      # libVLC Windows menolak path bergaris miring (/)
        self.media = self.instance.media_new_path(self.path)
        self.player.set_media(self.media)
        self.media.parse()                          # sinkron: durasi/trek tersedia segera (berkas lokal kecil metadatanya)
        if self.sink is not None:
            w, h = self._video_dims()
            self.sink.configure(self.player, self.vlc, *fit_size(w, h, self.opts.max_width))

    def play(self):
        self._start_poller()
        return self.player.play()

    # libVLC memperbarui get_time() secara kasar (lompatan ratusan ms). Untuk membandingkan dua kamera, nilai itu dicatat tepat
    # saat BERUBAH (polling 5 ms) lalu diinterpolasi: galat ~ratusan ms turun menjadi ~10 ms (terukur: selisih nyata antar-kamera <=10 ms).
    def _start_poller(self):
        if self._poller is not None and self._poller.is_alive():
            return
        self._poll_stop.clear()

        def run():
            last = -1
            while not self._poll_stop.wait(0.005):
                try:
                    t = int(self.player.get_time())
                except Exception:
                    return
                if t >= 0 and t != last:
                    last = t
                    self._anchor = (time.monotonic(), t)

        self._poller = threading.Thread(target=run, name="vlc-time", daemon=True)
        self._poller.start()

    @property
    def time_est_ms(self) -> float:
        """Posisi media (ms) terbaik: interpolasi dari anchor terakhir bila sedang memutar; get_time() biasa saat jeda."""
        a = self._anchor
        if a is None or self.state != "Playing":
            return float(self.time_ms)
        dt = min(0.6, max(0.0, time.monotonic() - a[0]))
        return a[1] + dt * 1000.0 * self.rate

    def set_paused(self, paused: bool):
        self.player.set_pause(1 if paused else 0)

    def stop(self):
        self._anchor = None
        self.player.stop()

    @property
    def state(self) -> str:
        return str(self.player.get_state()).split(".")[-1]

    @property
    def is_playing(self) -> bool:
        return bool(self.player.is_playing())

    # -- posisi
    @property
    def length_ms(self) -> int:
        return max(0, int(self.player.get_length() or (self.media.get_duration() if self.media else 0) or 0))

    @property
    def time_ms(self) -> int:
        return max(0, int(self.player.get_time()))

    def seek_ms(self, ms: int):
        self._anchor = None
        self.player.set_time(int(max(0, ms)))

    def next_frame(self):
        self.player.next_frame()

    # -- kecepatan / suara
    @property
    def rate(self) -> float:
        return float(self.player.get_rate() or self._rate)

    def set_rate(self, r: float):
        self._rate = clamp_rate(r)
        self.player.set_rate(self._rate)

    @property
    def volume(self) -> int:
        return int(self.player.audio_get_volume())

    def set_volume(self, v: int):
        self.player.audio_set_volume(int(min(100, max(0, v))))

    @property
    def muted(self) -> bool:
        return bool(self.player.audio_get_mute())

    def set_muted(self, m: bool):
        self.player.audio_set_mute(bool(m))

    # -- info
    @property
    def audio_tracks(self) -> int:
        n = self.player.audio_get_track_count()
        return max(0, n - 1) if n and n > 0 else 0        # VLC menghitung trek "Disable"

    @property
    def video_size(self):
        return self.player.video_get_size(0)

    def stats(self) -> Stats:
        st = self.vlc.MediaStats()
        if self.media is not None and self.media.get_stats(st):
            return Stats(st.decoded_video, st.displayed_pictures, st.lost_pictures, st.lost_abuffers)
        return Stats()

    def snapshot(self, path) -> bool:
        """Simpan PNG frame saat ini (FR-130). Perlu keluaran video nyata (bukan dummy)."""
        if self.sink is not None:
            return self.sink.save_png(path)
        return self.player.video_take_snapshot(0, str(path), 0, 0) == 0

    def release(self):
        self._poll_stop.set()
        try:
            self.player.stop()
        finally:
            self.player.release()
            self.instance.release()
