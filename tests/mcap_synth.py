"""Penulis MCAP sintetis untuk tes (tidak dipakai di aplikasi). Mengikuti spesifikasi MCAP 0.0."""
import struct
from pathlib import Path

MAGIC = b"\x89MCAP0\r\n"


def _s(t): b = t.encode(); return struct.pack("<I", len(b)) + b
def _rec(op, body): return bytes([op]) + struct.pack("<Q", len(body)) + body
def _map(d): body = b"".join(_s(k) + _s(v) for k, v in d.items()); return struct.pack("<I", len(body)) + body


def write_mcap(path, channels, messages, compression="", chunk_size=3, summary=True,
               chunked=True, profile="", library="synth"):
    """channels: [(id, topic, schema_name, schema_encoding, message_encoding)]
    messages: [(channel_id, log_time, data)] -> ditulis berurutan."""
    out = bytearray(MAGIC)
    out += _rec(0x01, _s(profile) + _s(library))
    schemas = {}
    for cid, topic, sname, senc, menc in channels:
        sid = cid                                   # satu skema per channel
        schemas[sid] = _rec(0x03, struct.pack("<H", sid) + _s(sname) + _s(senc) + struct.pack("<I", 0))
        out += schemas[sid]
        out += _rec(0x04, struct.pack("<HH", cid, sid) + _s(topic) + _s(menc) + _map({}))
    chan_recs = [_rec(0x04, struct.pack("<HH", c[0], c[0]) + _s(c[1]) + _s(c[4]) + _map({})) for c in channels]
    chunk_idx, counts, seq = [], {}, {}
    groups = [messages[i:i + chunk_size] for i in range(0, len(messages), chunk_size)] if chunked else [[]]
    if not chunked:
        for cid, lt, data in messages:
            seq[cid] = seq.get(cid, 0) + 1
            out += _rec(0x05, struct.pack("<HIQQ", cid, seq[cid], lt, lt) + data)
            counts[cid] = counts.get(cid, 0) + 1
    else:
        for g in groups:
            inner = bytearray()
            for cid, lt, data in g:
                seq[cid] = seq.get(cid, 0) + 1
                inner += _rec(0x05, struct.pack("<HIQQ", cid, seq[cid], lt, lt) + data)
                counts[cid] = counts.get(cid, 0) + 1
            if compression == "zstd":
                import zstandard; comp = zstandard.ZstdCompressor().compress(bytes(inner))
            elif compression == "lz4":
                import lz4.frame; comp = lz4.frame.compress(bytes(inner))
            else:
                comp = bytes(inner)
            t0, t1 = g[0][1], g[-1][1]
            body = struct.pack("<QQQI", t0, t1, len(inner), 0) + _s(compression) + struct.pack("<Q", len(comp)) + comp
            start = len(out)
            out += _rec(0x06, body)
            chunk_idx.append((t0, t1, start, len(out) - start))
    out += _rec(0x0F, struct.pack("<I", 0))                      # data_end
    summary_start = 0
    if summary:
        summary_start = len(out)
        for r in schemas.values():
            out += r
        for r in chan_recs:
            out += r
        for t0, t1, start, ln in chunk_idx:
            out += _rec(0x08, struct.pack("<QQQQI", t0, t1, start, ln, 0) + struct.pack("<Q", 0) + _s(compression)
                        + struct.pack("<QQ", ln, ln))
        times = [m[1] for m in messages]
        cm = b"".join(struct.pack("<HQ", k, v) for k, v in counts.items())
        out += _rec(0x0B, struct.pack("<QHIIIIQQ", len(messages), len(channels), len(channels), 0, 0,
                                      len(chunk_idx), min(times) if times else 0, max(times) if times else 0)
                    + struct.pack("<I", len(cm)) + cm)
    out += _rec(0x02, struct.pack("<QQI", summary_start, 0, 0))   # footer
    out += MAGIC
    Path(path).write_bytes(bytes(out))
    return path
