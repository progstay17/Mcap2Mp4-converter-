"""ROS2 (CDR) CompressedImage H.265 -> MP4, reusing mcap_to_mp4's MP4 writer."""
from __future__ import annotations

import os
import struct
import tempfile
import time
from pathlib import Path

import mcap_to_mp4 as core

ROS2_TOPIC = "/head/camera/image_compressed"


def parse_compressed_image(msg):
    """sensor_msgs/CompressedImage, CDR little-endian -> (format, data)."""
    if msg[1] != 1:
        raise ValueError("CDR big-endian tidak didukung")
    o = 4 + 8                                   # encapsulation header + stamp
    n = struct.unpack_from("<I", msg, o)[0]; o += 4 + n          # frame_id
    o = (o + 3) & ~3
    n = struct.unpack_from("<I", msg, o)[0]; o += 4
    fmt = msg[o:o + n].rstrip(b"\0").decode("utf-8", "replace").lower(); o += n
    o = (o + 3) & ~3
    n = struct.unpack_from("<I", msg, o)[0]; o += 4
    return fmt, msg[o:o + n]


def detect_layout(path):
    """Stops as soon as the layout is certain; scans on only for unknown files."""
    topics = set()
    for op, rec in core.iter_records(path):
        if op != 0x04:
            continue
        cid, o = core.u16(rec, 0)
        _, o = core.u16(rec, o)
        topic, _ = core.mstr(rec, o)
        topics.add(topic)
        if topic == ROS2_TOPIC:
            return "ros2"
        if all(t in topics for t in core.CAMS):
            return "ego"
    if any(t in topics for t in core.CAMS):
        return "ego"          # let the original extractor report what is missing
    raise ValueError("topik video tidak dikenali; topik yang ada: " + ", ".join(sorted(topics)))


def collect_ros2(path, progress=None):
    want = None
    track = core.Track()
    for op, rec in core.iter_records(path, progress=progress):
        if op == 0x04:
            cid, o = core.u16(rec, 0)
            _, o = core.u16(rec, o)
            topic, _ = core.mstr(rec, o)
            if topic == ROS2_TOPIC:
                want = cid
        elif op == 0x05 and want is not None and core.u16(rec, 0)[0] == want:
            fmt, data = parse_compressed_image(rec[22:])
            core.add_payload(track, data, fmt, ROS2_TOPIC)
    if progress:
        progress(path.stat().st_size, path.stat().st_size)
    return track


def extract_ros2(source, fps=None):
    source = Path(source).resolve()
    result = {"source": str(source), "status": "failed", "outputs": [], "layout": "ros2"}
    output = source.with_name(f"{source.stem}_camera_0.mp4")
    temp = None
    try:
        if output.exists():
            result.update(status="skipped", message="MP4 sudah ada, dilewati.")
            return result
        actual_fps = core.get_fps(source.parent, fps)
        if not 1 <= actual_fps <= 90000:
            raise ValueError("fps harus 1-90000")
        result["fps"] = actual_fps
        track = collect_ros2(source)
        samples = core.finalize(track, ROS2_TOPIC)
        fd, name = tempfile.mkstemp(prefix="." + output.stem + "_", suffix=".partial", dir=source.parent)
        os.close(fd)
        temp = Path(name)
        core.write_mp4(temp, samples, track.info, track.params, actual_fps)
        os.rename(temp, output) if os.name == "nt" else os.link(temp, output)
        result.update(status="success", outputs=[str(output)], frames=len(samples),
                      message=f"Video kamera ROS2 diekstrak, {len(samples)} frame, {actual_fps} FPS.")
    except Exception as exc:
        result["message"] = f"{type(exc).__name__}: {exc}"
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return result
