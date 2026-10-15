"""Pembuat aliran HEVC asli (ffmpeg/libx265) dan pembungkus pesan Foxglove/ROS2 untuk tes."""
import struct, subprocess, tempfile, shutil
from pathlib import Path

_cache = {}


def hevc_access_units(frames=90, rate="30000/1001", keyint=30, size="320x240"):
    key = (frames, rate, keyint, size)
    if key in _cache:
        return _cache[key]
    d = Path(tempfile.mkdtemp())
    out = d / "s.hevc"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate={rate}", "-frames:v", str(frames), "-pix_fmt", "yuv420p",
                    "-c:v", "libx265", "-x265-params", f"bframes=0:keyint={keyint}:repeat-headers=1:log-level=none",
                    "-f", "hevc", str(out)], check=True)
    data = out.read_bytes()
    shutil.rmtree(d)
    nals, i = [], 0
    pos = [p for p in range(len(data) - 3) if data[p:p + 3] == b"\0\0\1"]
    for a, b in zip(pos, pos[1:] + [len(data)]):
        nals.append(data[a + 3:b].rstrip(b"\0"))
    aus, cur, has_vcl = [], [], False
    for n in nals:
        t = (n[0] >> 1) & 0x3F
        if t < 32:
            if (n[2] >> 7) and has_vcl:
                aus.append(cur); cur, has_vcl = [], False
            cur.append(n); has_vcl = True
        else:
            if has_vcl:
                aus.append(cur); cur, has_vcl = [], False
            cur.append(n)
    if cur:
        aus.append(cur)
    res = [b"".join(b"\0\0\0\1" + n for n in au) for au in aus]
    assert len(res) == frames, (len(res), frames)
    _cache[key] = res
    return res


def _varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F; v >>= 7
        if v: out.append(b | 0x80)
        else: out.append(b); return bytes(out)


def _ld(field, payload): return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def foxglove_msg(payload, stamp_ns, fmt="h265"):
    ts = _varint(1 << 3 | 0) + _varint(stamp_ns // 10**9) + _varint(2 << 3 | 0) + _varint(stamp_ns % 10**9)
    return _ld(1, ts) + _ld(2, b"cam") + _ld(3, payload) + _ld(4, fmt.encode())


def ros2_msg(payload, stamp_ns, fmt="h265", frame_id="cam"):
    out = bytearray(b"\x00\x01\x00\x00")
    out += struct.pack("<iI", stamp_ns // 10**9, stamp_ns % 10**9)
    fid = frame_id.encode() + b"\0"
    out += struct.pack("<I", len(fid)) + fid
    while len(out) % 4: out.append(0)
    f = fmt.encode() + b"\0"
    out += struct.pack("<I", len(f)) + f
    while len(out) % 4: out.append(0)
    out += struct.pack("<I", len(payload)) + payload
    return bytes(out)


def ffprobe(path):
    import json
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,width,height,nb_read_frames:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0 or r.stderr.strip():
        raise AssertionError("ffprobe: " + r.stderr)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    return {"codec": s["codec_name"], "w": s["width"], "h": s["height"], "frames": int(s["nb_read_frames"]),
            "duration": float(d["format"]["duration"])}
