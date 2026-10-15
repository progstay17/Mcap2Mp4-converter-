"""Pembuat sepasang (atau lebih) MCAP sintetis yang MENIRU rekaman 'ego' asli (docs/DATA_NYATA_ego.md):

- 2 kamera Foxglove CompressedVideo H.265 (30 fps, GOP 30, file dimulai dengan keyframe + VPS/SPS/PPS)
- 1 audio foxglove.RawAudio: pcm-s16, 16 kHz, 2 kanal, 256 sampel per pesan tiap 16,000 ms, kontinu lintas file,
  mulai +32 ms setelah video
- IMU 500 Hz yang berjalan 10 ms melewati akhir video sehingga RINGKASAN file bagian B tampak mulai sebelum
  bagian A berakhir (padahal frame video berurutan tepat satu periode)
- waktu relatif sejak boot (bukan tanggal), stamp pesan == log_time
- nomor channel TERTUKAR antar-file (pencocokan harus lewat nama topik)

Dipakai tes sesi/audio/pemutar. Hanya data uji; tidak dipakai aplikasi.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

from mcap_synth import write_mcap
import hevc_helper as H

FOX_VIDEO, FOX_AUDIO = "foxglove.CompressedVideo", "foxglove.RawAudio"
IMU_SCHEMA = "visio_schema.v1.sensor.ImuRaw"
T0 = 2_183_963_902_000            # ns (relatif sejak boot)
PERIOD = 33_333_333               # ns (~30 fps)
AUDIO_STEP = 16_000_000           # ns = 256 sampel @ 16 kHz
AUDIO_OFFSET = 32_000_000         # audio mulai +32 ms setelah frame video pertama
RATE, CHANNELS, SPM = 16000, 2, 256


def _varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pcm_chunk(first_sample: int) -> bytes:
    """256 frame stereo s16le deterministik (sinus 440/660 Hz) mulai dari indeks sampel global."""
    out = bytearray()
    for n in range(first_sample, first_sample + SPM):
        out += struct.pack("<hh", int(12000 * math.sin(2 * math.pi * 440 * n / RATE)),
                           int(12000 * math.sin(2 * math.pi * 660 * n / RATE)))
    return bytes(out)


def raw_audio_msg(stamp_ns: int, pcm: bytes) -> bytes:
    ts = _varint(1 << 3) + _varint(stamp_ns // 10**9) + _varint(2 << 3) + _varint(stamp_ns % 10**9)
    ld = lambda f, p: _varint(f << 3 | 2) + _varint(len(p)) + p          # noqa: E731
    return ld(1, ts) + ld(2, pcm) + ld(3, b"pcm-s16") + _varint(4 << 3) + _varint(RATE) + _varint(5 << 3) + _varint(CHANNELS)


def write_ego_session(folder, parts=2, frames=90, first_index=14, prefix="ego_", gap_frames=1.0, t0=T0):
    """Tulis `parts` file ego_00NN.mcap berurutan. gap_frames = jarak frame pertama bagian berikut dari frame
    terakhir bagian sebelumnya, dalam periode frame (1.0 = kontinu seperti rekaman asli)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    aus = H.hevc_access_units(frames)
    paths, truth = [], {"parts": []}
    audio_j = 0                                  # indeks pesan audio global
    start = t0
    for p in range(parts):
        cam0_t = [start + k * PERIOD for k in range(frames)]
        cam1_t = [t - 7_000 for t in cam0_t]                    # kamera 1 lebih awal 7 µs (seperti aslinya)
        end_video = cam0_t[-1] + PERIOD
        msgs = []
        ids = {"cam0": 1, "audio": 2, "cam1": 3, "imu": 4} if p % 2 == 0 else {"cam1": 1, "cam0": 2, "audio": 3, "imu": 4}
        for k in range(frames):
            msgs.append((ids["cam0"], cam0_t[k], H.foxglove_msg(aus[k], cam0_t[k])))
            msgs.append((ids["cam1"], cam1_t[k], H.foxglove_msg(aus[k], cam1_t[k])))
        a_first, a_n = audio_j, 0
        while True:
            st = t0 + AUDIO_OFFSET + audio_j * AUDIO_STEP
            if st >= end_video:
                break
            msgs.append((ids["audio"], st, raw_audio_msg(st, pcm_chunk(audio_j * SPM))))
            audio_j += 1
            a_n += 1
        t = start
        while t < end_video + 10_000_000:                       # IMU 500 Hz, melewati akhir video 10 ms
            msgs.append((ids["imu"], t, b"\x08\x01\x10\x02"))
            t += 2_000_000
        msgs.sort(key=lambda m: m[1])
        chans = [(ids["cam0"], "/ego/camera/0", FOX_VIDEO, "protobuf", "protobuf"),
                 (ids["cam1"], "/ego/camera/1", FOX_VIDEO, "protobuf", "protobuf"),
                 (ids["audio"], "/ego/audio/0", FOX_AUDIO, "protobuf", "protobuf"),
                 (ids["imu"], "/ego/imu/0/raw", IMU_SCHEMA, "protobuf", "protobuf")]
        chans.sort(key=lambda c: c[0])
        path = write_ego_part(folder / f"{prefix}{first_index + p:04d}.mcap", chans, msgs)
        paths.append(path)
        truth["parts"].append({"path": path, "frames": frames, "cam0_first": cam0_t[0], "cam0_last": cam0_t[-1],
                               "cam1_first": cam1_t[0], "cam1_last": cam1_t[-1],
                               "audio_first_index": a_first, "audio_messages": a_n,
                               "audio_first_stamp": t0 + AUDIO_OFFSET + a_first * AUDIO_STEP,
                               "imu_last": t - 2_000_000, "ids": ids})
        start = int(cam0_t[-1] + gap_frames * PERIOD)
    truth["audio_total_samples"] = audio_j * SPM
    return paths, truth


def write_ego_part(path, chans, msgs):
    return write_mcap(path, chans, msgs, compression="", chunk_size=40, library="libmcap 1.4.1")
