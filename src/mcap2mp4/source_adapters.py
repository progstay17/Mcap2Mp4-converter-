"""Adaptor sumber video (PRD FR-10, FR-40): Foxglove (protobuf) dan ROS2 (CDR).

Channel video ditemukan dari NAMA SKEMA, bukan nama topik (universal). Port dari legacy:
compressed_video_payload (protobuf), parse_compressed_image (CDR; D4: header.stamp kini dibaca).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

FOXGLOVE_VIDEO = "foxglove.CompressedVideo"
ROS2_IMAGE = ("sensor_msgs/msg/CompressedImage", "sensor_msgs/CompressedImage")
ABSOLUTE_AFTER_NS = 946684800 * 10**9          # 2000-01-01 UTC: di bawah ini dianggap waktu relatif


@dataclass
class VideoChannel:
    channel_id: int
    topic: str
    adapter: str              # foxglove | ros2
    schema: str


@dataclass
class Frame:
    fmt: str                  # h265 | h264 | lainnya (huruf kecil)
    payload: bytes
    stamp_ns: int | None      # timestamp di dalam pesan; None bila tidak ada


class AdapterError(ValueError):
    pass


def find_video_channels(summary) -> list[VideoChannel]:
    out = []
    for ch in sorted(summary.channels.values(), key=lambda c: c.id):
        sc = summary.schemas.get(ch.schema_id)
        if sc is None:
            continue
        if sc.name == FOXGLOVE_VIDEO and ch.message_encoding == "protobuf":
            out.append(VideoChannel(ch.id, ch.topic, "foxglove", sc.name))
        elif sc.name in ROS2_IMAGE and ch.message_encoding == "cdr":
            out.append(VideoChannel(ch.id, ch.topic, "ros2", sc.name))
    return out


def normalize_format(fmt: str | None) -> str:
    f = (fmt or "").strip().lower()
    if "265" in f or "hevc" in f:
        return "h265"
    if "264" in f or "avc" in f:
        return "h264"
    return f or "unknown"


# ---------------------------------------------------------------- protobuf
def _varint(b, o):
    v = s = 0
    while True:
        if o >= len(b):
            raise AdapterError("varint terpotong")
        c = b[o]
        o += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, o
        s += 7
        if s > 70:
            raise AdapterError("varint terlalu panjang")


def _fields(msg):
    o, n = 0, len(msg)
    while o < n:
        key, o = _varint(msg, o)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, o = _varint(msg, o)
        elif wt == 1:
            val, o = msg[o:o + 8], o + 8
        elif wt == 2:
            ln, o = _varint(msg, o)
            if o + ln > n:
                raise AdapterError("field protobuf melewati batas")
            val, o = msg[o:o + ln], o + ln
        elif wt == 5:
            val, o = msg[o:o + 4], o + 4
        else:
            raise AdapterError(f"wire type protobuf {wt} tidak didukung")
        yield field, wt, val


def parse_foxglove_video(msg) -> Frame:
    msg = bytes(msg)
    data = fmt = None
    stamp = None
    for field, wt, val in _fields(msg):
        if field == 1 and wt == 2:                      # google.protobuf.Timestamp
            sec = nanos = 0
            for f2, w2, v2 in _fields(val):
                if f2 == 1 and w2 == 0:
                    sec = v2
                elif f2 == 2 and w2 == 0:
                    nanos = v2
            stamp = sec * 10**9 + nanos
        elif field == 3 and wt == 2:
            data = val
        elif field == 4 and wt == 2:
            fmt = val.decode("utf-8", "replace")
    if data is None:
        raise AdapterError("pesan CompressedVideo tanpa data")
    return Frame(normalize_format(fmt), data, stamp)


# ---------------------------------------------------------------- ROS2 CDR
def parse_ros2_compressed_image(msg) -> Frame:
    msg = bytes(msg)
    if len(msg) < 20 or msg[1] != 1:
        raise AdapterError("CDR big-endian atau pesan terlalu pendek tidak didukung")
    sec, nsec = struct.unpack_from("<iI", msg, 4)       # header.stamp (D4)
    o = 12
    n = struct.unpack_from("<I", msg, o)[0]
    o += 4 + n                                          # frame_id
    o = (o + 3) & ~3
    n = struct.unpack_from("<I", msg, o)[0]
    o += 4
    fmt = msg[o:o + n].rstrip(b"\0").decode("utf-8", "replace")
    o += n
    o = (o + 3) & ~3
    n = struct.unpack_from("<I", msg, o)[0]
    o += 4
    if o + n > len(msg):
        raise AdapterError("data CompressedImage melewati batas pesan")
    return Frame(normalize_format(fmt), msg[o:o + n], sec * 10**9 + nsec)


def parse_frame(adapter: str, data) -> Frame:
    if adapter == "foxglove":
        return parse_foxglove_video(data)
    if adapter == "ros2":
        return parse_ros2_compressed_image(data)
    raise AdapterError(f"adaptor tidak dikenal: {adapter}")


def is_absolute(stamp_ns: int | None) -> bool:
    return stamp_ns is not None and stamp_ns >= ABSOLUTE_AFTER_NS
