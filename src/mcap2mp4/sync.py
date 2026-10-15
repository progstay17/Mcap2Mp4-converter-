"""Pencocokan frame antar-channel (PRD FR-33). Dipasangkan lewat log_time MCAP yang sama (keputusan #11)."""
from __future__ import annotations

from statistics import median


def frame_period_ns(times) -> float:
    d = [times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] > times[i]]
    return float(median(d)) if d else 0.0


def compare(channels: dict, tolerance_frames: float = 0.5) -> dict:
    """channels: {topik: daftar log_time ns (frame yang ditulis)}. Acuan = channel pertama.
    Hasil: {ok, reason, counts, diff, first_mismatch, trim_to}. reason: None | frame_count | tolerance | time_base."""
    topics = list(channels)
    counts = {t: len(channels[t]) for t in topics}
    res = {"ok": True, "reason": None, "counts": counts, "diff": 0, "first_mismatch": None, "trim_to": None}
    if len(topics) < 2:
        return res
    ref = topics[0]
    period = frame_period_ns(channels[ref]) or max((frame_period_ns(channels[t]) for t in topics), default=0.0)
    tol = tolerance_frames * period
    res["diff"] = max(counts.values()) - min(counts.values())
    n = min(counts.values())
    first_bad = None
    for i in range(n):
        base = channels[ref][i]
        for t in topics[1:]:
            if abs(channels[t][i] - base) > tol:
                first_bad = {"index": i, "topic": t, "ref_time_ns": base, "time_ns": channels[t][i],
                             "delta_ns": channels[t][i] - base}
                break
        if first_bad:
            break
    if first_bad and period and abs(first_bad["delta_ns"]) > 10 * period and first_bad["index"] == 0:
        res.update(ok=False, reason="time_base", first_mismatch=first_bad)
    elif first_bad:
        res.update(ok=False, reason="tolerance", first_mismatch=first_bad)
    elif res["diff"]:
        res.update(ok=False, reason="frame_count", trim_to=n,
                   first_mismatch={"index": n, "topic": max(counts, key=counts.get), "note": "frame berlebih di ujung"})
    return res
