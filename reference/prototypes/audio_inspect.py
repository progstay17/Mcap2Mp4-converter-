"""Periksa topik foxglove.RawAudio pada file MCAP: format, kontinuitas, selisih terhadap video, sambungan antar-file.
Pakai:  PYTHONPATH=src python reference/prototypes/audio_inspect.py ego_0014.mcap ego_0015.mcap
Hasil pada rekaman asli ada di docs/DATA_NYATA_ego.md (pcm-s16, 16 kHz, 2 kanal, 256 sampel/16,000 ms, tanpa celah)."""
import statistics, struct, sys
from mcap2mp4 import mcap_reader as R, source_adapters as sa


def raw_audio(msg):
    d = {}
    for f, wt, v in sa._fields(bytes(msg)):
        if f == 1 and wt == 2:
            sec = nan = 0
            for f2, w2, v2 in sa._fields(v):
                sec, nan = (v2, nan) if f2 == 1 else (sec, v2)
            d["stamp"] = sec * 10**9 + nan
        elif f == 2 and wt == 2: d["data"] = v
        elif f == 3 and wt == 2: d["format"] = v.decode()
        elif f == 4: d["rate"] = v
        elif f == 5: d["ch"] = v
    return d


def main(paths, audio_topic="/ego/audio/0", video_topic="/ego/camera/0"):
    prev_end = None
    for p in paths:
        ms, v0 = [], None
        for ch, msg in R.iter_messages(p, topics={audio_topic, video_topic}):
            if ch.topic == audio_topic:
                a = raw_audio(msg.data); ms.append(a)
            elif v0 is None:
                v0 = msg.log_time
        a0 = ms[0]
        smp = [len(m["data"]) // (2 * a0["ch"]) for m in ms]
        st = [m["stamp"] for m in ms]
        gaps = [(st[i] + smp[i] / a0["rate"] * 1e9) - st[i + 1] for i in range(len(ms) - 1)]
        print(f"{p}: {len(ms)} pesan {a0['format']} {a0['rate']} Hz {a0['ch']} kanal | sampel={sum(smp)} ({sum(smp)/a0['rate']:.3f} dtk)"
              f" | celah maks={max(abs(g) for g in gaps)/1e6:.3f} ms | audio-awal minus video-awal={(st[0]-v0)/1e6:.3f} ms")
        if prev_end is not None:
            print(f"   sambungan dari file sebelumnya: {(st[0]-prev_end)/1e6:.3f} ms (0 = kontinu)")
        prev_end = st[-1] + smp[-1] / a0["rate"] * 1e9


if __name__ == "__main__":
    main(sys.argv[1:])
