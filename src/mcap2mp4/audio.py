"""Audio ke MP4 (PRD 0.3, FR-90..FR-98).

Fakta data nyata (docs/DATA_NYATA_ego.md): foxglove.RawAudio (protobuf: 1 timestamp, 2 data, 3 format, 4 sample_rate,
5 number_of_channels), pcm-s16 little-endian interleaved, 16 kHz, 2 kanal, 256 sampel/pesan tiap 16,000 ms, tanpa celah,
stamp == log_time, mulai +24..32 ms setelah frame video pertama.

Isi modul:
    parse_raw_audio(msg)      -> AudioChunk                      (adaptor, FR-90/91)
    find_audio_channels(sum)  -> [AudioChannel]                  (deteksi dari SKEMA, FR-90)
    AudioTrackState.feed(...) -> [bytes]                         (kontinuitas: celah diisi hening / tumpang-tindih dipotong, FR-95)

PCM disalin apa adanya (tanpa resample/encode, FR-92). Penulisan track `soun` ada di mp4_writer.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import source_adapters as sa

FOXGLOVE_AUDIO = "foxglove.RawAudio"
SUPPORTED_FORMATS = ("pcm-s16",)
BYTES_PER_SAMPLE = 2


class AudioError(ValueError):
    pass


@dataclass
class AudioChannel:
    channel_id: int
    topic: str
    schema: str


@dataclass
class AudioChunk:
    stamp_ns: int | None        # timestamp di dalam pesan (None bila tidak ada)
    data: bytes
    fmt: str
    rate: int
    channels: int


def find_audio_channels(summary) -> list[AudioChannel]:
    """Channel audio dikenali dari nama skema (bukan nama topik)."""
    out = []
    for ch in sorted(summary.channels.values(), key=lambda c: c.id):
        sc = summary.schemas.get(ch.schema_id)
        if sc is not None and sc.name == FOXGLOVE_AUDIO and ch.message_encoding == "protobuf":
            out.append(AudioChannel(ch.id, ch.topic, sc.name))
    return out


def parse_raw_audio(msg) -> AudioChunk:
    """Urai satu pesan foxglove.RawAudio."""
    stamp = None
    data = b""
    fmt = ""
    rate = ch = 0
    try:
        for field, wt, val in sa._fields(bytes(msg)):
            if field == 1 and wt == 2:
                sec = nanos = 0
                for f2, w2, v2 in sa._fields(val):
                    if f2 == 1 and w2 == 0:
                        sec = v2
                    elif f2 == 2 and w2 == 0:
                        nanos = v2
                stamp = sec * 10**9 + nanos
            elif field == 2 and wt == 2:
                data = val
            elif field == 3 and wt == 2:
                fmt = val.decode("utf-8", "replace").strip().lower()
            elif field == 4 and wt == 0:
                rate = val
            elif field == 5 and wt == 0:
                ch = val
    except sa.AdapterError as exc:
        raise AudioError(str(exc)) from exc
    if rate <= 0 or ch <= 0:
        raise AudioError("RawAudio tanpa sample_rate/number_of_channels")
    return AudioChunk(stamp, data, fmt, rate, ch)


class AudioTrackState:
    """Satu channel audio: hitung sampel, jaga kontinuitas, kumpulkan statistik.

    feed() mengembalikan daftar blok byte yang harus ditulis berurutan (hening isian lalu data, atau data terpotong).
    Format tidak didukung -> `error` terisi dan feed() tidak mengembalikan apa pun (video tetap jalan, FR-91).
    """

    def __init__(self, topic, gap_policy="fill", gap_tolerance_ms=1.0):
        self.topic = topic
        self.gap_policy = gap_policy
        self.tol_ns = int(round(float(gap_tolerance_ms) * 1e6))
        self.rate = self.channels = None
        self.fmt = None
        self.error = None                       # (kode, pesan) -> channel dilewati
        self.first_stamp_ns = None
        self._next_ns = None                    # stamp yang diharapkan untuk blok berikut
        self.frames = 0                         # total frame PCM yang ditulis (termasuk hening isian)
        self.source_frames = 0                  # frame PCM dari sumber yang ditulis
        self.silence_frames = 0
        self.trimmed_frames = 0
        self.gaps = []                          # {position_s, duration_ms, filled}
        self.overlaps = []                      # {position_s, duration_ms}
        self.messages = 0
        self.sha = hashlib.sha256()             # hash PCM yang ditulis
        self._bytes_per_frame = None

    def _ns(self, frames):
        return frames * 10**9 / self.rate

    def _frames_for(self, ns):
        return int(round(ns * self.rate / 1e9))

    def feed(self, chunk: AudioChunk, log_time_ns: int) -> list[bytes]:
        if self.error:
            return []
        self.messages += 1
        if self.fmt is None:
            if chunk.fmt not in SUPPORTED_FORMATS:
                self.error = ("unsupported_audio_format",
                              f"format audio '{chunk.fmt or '?'}' belum didukung (hanya pcm-s16)")
                return []
            self.fmt, self.rate, self.channels = chunk.fmt, chunk.rate, chunk.channels
            self._bytes_per_frame = BYTES_PER_SAMPLE * self.channels
        elif (chunk.fmt, chunk.rate, chunk.channels) != (self.fmt, self.rate, self.channels):
            self.error = ("audio_format_changed", "format/laju/kanal audio berubah di tengah rekaman")
            return []
        bpf = self._bytes_per_frame
        data = chunk.data
        if len(data) % bpf:
            data = data[: len(data) - len(data) % bpf]        # buang sisa byte tidak utuh
        if not data:
            return []
        stamp = chunk.stamp_ns if chunk.stamp_ns else log_time_ns
        n = len(data) // bpf
        out = []
        if self.first_stamp_ns is None:
            self.first_stamp_ns = stamp
        elif self._next_ns is not None:
            delta = stamp - self._next_ns               # >0 celah, <0 tumpang-tindih
            pos_s = self.frames / self.rate
            if delta > self.tol_ns:
                fill = self._frames_for(delta)
                filled = self.gap_policy == "fill" and fill > 0
                self.gaps.append({"position_s": round(pos_s, 6), "duration_ms": round(delta / 1e6, 3), "filled": filled})
                if filled:
                    sil = bytes(fill * bpf)
                    out.append(sil)
                    self.frames += fill
                    self.silence_frames += fill
                    self.sha.update(sil)
            elif delta < -self.tol_ns:
                cut = min(n, self._frames_for(-delta))
                self.overlaps.append({"position_s": round(pos_s, 6), "duration_ms": round(-delta / 1e6, 3)})
                data = data[cut * bpf:]
                self.trimmed_frames += cut
                n -= cut
        self._next_ns = stamp + int(round(self._ns(len(chunk.data) // bpf)))
        if n > 0:
            out.append(data)
            self.frames += n
            self.source_frames += n
            self.sha.update(data)
        return out

    @property
    def end_stamp_ns(self):
        if self.first_stamp_ns is None:
            return None
        return self.first_stamp_ns + int(round(self._ns(self.frames)))

    @property
    def source_exact(self):
        """True bila isi MP4 identik dengan penggabungan PCM sumber (tanpa isian/pemotongan)."""
        return not self.silence_frames and not self.trimmed_frames

    def summary(self) -> dict:
        return {"topic": self.topic, "format": self.fmt, "sample_rate": self.rate, "channels": self.channels,
                "samples": self.frames, "source_samples": self.source_frames, "messages": self.messages,
                "first_time_ns": self.first_stamp_ns, "end_time_ns": self.end_stamp_ns,
                "duration_s": round(self.frames / self.rate, 6) if self.rate else None,
                "gaps": len(self.gaps), "gap_list": self.gaps[:50], "silence_samples": self.silence_frames,
                "overlaps": len(self.overlaps), "overlap_list": self.overlaps[:50],
                "trimmed_samples": self.trimmed_frames, "sha256": self.sha.hexdigest()}


def raw_pcm_sha256(chunks) -> str:
    """Hash PCM sumber apa adanya dari iterasi AudioChunk (pembanding mode verifikasi penuh)."""
    h = hashlib.sha256()
    for c in chunks:
        bpf = BYTES_PER_SAMPLE * c.channels
        h.update(c.data[: len(c.data) - len(c.data) % bpf])
    return h.hexdigest()
