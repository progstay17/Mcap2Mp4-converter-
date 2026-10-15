"""Benchmark awal pemutar (PRD 0.3 §9 beban dekode, M7/M9): beban CPU libVLC per stream 1080p30 H.265.

    python build/bench_player.py [--video clip.mp4] [--seconds 10] [--rates 1,2,4] [--streams 1,2] [--hw off]

Tanpa --video, dibuat klip uji 1080p30 H.265 dengan ffmpeg/libx265 (testsrc2 + derau, bukan rekaman nyata: gunakan MP4 hasil
converter dari rekaman asli untuk angka yang representatif). Keluaran dummy (tanpa render) => mengukur DEKODE, bukan tampilan.
Hasil: cores ~ detik-CPU / detik-dinding per stream; 'lost' = frame terjatuh menurut libVLC. WAJIB diulang di mesin referensi.
"""
import argparse, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcap2mp4.player.engine import Engine, EngineOptions, PlayerUnavailable


def make_clip(path, seconds):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={seconds}",
           "-vf", "noise=alls=20:allf=t", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "keyint=30:log-level=error",
           "-tag:v", "hvc1", str(path)]
    subprocess.run(cmd, check=True)


def run(video, rate, streams, hw, seconds):
    engines = [Engine(EngineOptions(hw_decode=hw, headless=True)) for _ in range(streams)]
    for e in engines:
        e.load(video)
        e.set_rate(rate)
    c0, w0 = time.process_time(), time.time()
    for e in engines:
        e.play()
    time.sleep(0.5)
    limit = w0 + seconds / rate + 6
    while time.time() < limit and any(e.state not in ("Ended", "Stopped", "Error") for e in engines):
        time.sleep(0.1)
    wall, cpu = time.time() - w0, time.process_time() - c0
    lost = sum(e.stats().lost for e in engines)
    played = max(e.time_ms for e in engines) / 1000
    for e in engines:
        e.release()
    return {"rate": rate, "streams": streams, "wall_s": round(wall, 2), "cpu_s": round(cpu, 2),
            "cores_total": round(cpu / wall, 2), "cores_per_stream": round(cpu / wall / streams, 2), "lost": lost,
            "media_s_reached": round(played, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--rates", default="1,2,4")
    ap.add_argument("--streams", default="1,2")
    ap.add_argument("--hw", default="off", choices=["auto", "off"])
    a = ap.parse_args()
    try:
        video = a.video
        if not video:
            video = str(Path(tempfile.mkdtemp()) / "bench_1080p30.mp4")
            make_clip(video, a.seconds)
        print(f"cpu logis: {os.cpu_count()} | video: {video} | hw: {a.hw}")
        for n in (int(x) for x in a.streams.split(",")):
            for r in (float(x) for x in a.rates.split(",")):
                print(run(video, r, n, a.hw, a.seconds))
    except PlayerUnavailable as exc:
        sys.exit(f"libVLC tidak tersedia: {exc}")


if __name__ == "__main__":
    main()
