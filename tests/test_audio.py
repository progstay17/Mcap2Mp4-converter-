"""Tes audio M5 (FR-90..FR-97, AC-23..25). Pakai tests/ego_synth.py (pcm-s16, 16 kHz, 2 kanal, 256 sampel/16 ms)."""
import hashlib, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import audio, settings, verify, worker
import ego_synth as E
import hevc_helper as H
from mcap_synth import write_mcap


def convert(path, out, **over):
    s = settings.load()
    s.update(over)
    return worker.convert_file({"src": str(path), "rel": Path(path).name, "out_dir": str(out), "settings": s})


def decode_pcm(mp4):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-map", "0:a:0", "-f", "s16le", "-"], capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def source_pcm(truth_part_index, truth):
    first = truth["parts"][truth_part_index]["audio_first_index"]
    n = truth["parts"][truth_part_index]["audio_messages"]
    return b"".join(E.pcm_chunk((first + i) * E.SPM) for i in range(n))


def raw_audio_msg(stamp_ns, pcm, fmt=b"pcm-s16", rate=E.RATE, ch=E.CHANNELS):
    v = E._varint
    ts = v(1 << 3) + v(stamp_ns // 10**9) + v(2 << 3) + v(stamp_ns % 10**9)
    ld = lambda f, p: v(f << 3 | 2) + v(len(p)) + p          # noqa: E731
    return ld(1, ts) + ld(2, pcm) + ld(3, fmt) + v(4 << 3) + v(rate) + v(5 << 3) + v(ch)


def write_custom(path, audio_plan, frames=90, fmt=b"pcm-s16", offset=E.AUDIO_OFFSET):
    """audio_plan: daftar (stamp_ns) pesan audio; tiap pesan 256 sampel."""
    aus = H.hevc_access_units(frames)
    msgs = [(1, E.T0 + k * E.PERIOD, H.foxglove_msg(aus[k], E.T0 + k * E.PERIOD)) for k in range(frames)]
    for j, st in enumerate(audio_plan):
        msgs.append((2, st, raw_audio_msg(st, E.pcm_chunk(j * E.SPM), fmt)))
    msgs.sort(key=lambda m: m[1])
    chans = [(1, "/cam/0", E.FOX_VIDEO, "protobuf", "protobuf"), (2, "/mic/0", E.FOX_AUDIO, "protobuf", "protobuf")]
    return write_mcap(path, chans, msgs, compression="", chunk_size=40, library="t")


class AudioTrackTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.paths, self.truth = E.write_ego_session(self.d / "src", parts=2)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_ac23_audio_track_present_and_payload_identical(self):
        total = 0
        for i, p in enumerate(self.paths):
            r = convert(p, self.d / "out", verify="full")
            self.assertEqual(r["status"], "success", r)
            self.assertEqual(len(r["outputs"]), 2)
            for o in r["outputs"]:
                a = o["audio"][0]
                n_expected = self.truth["parts"][i]["audio_messages"] * E.SPM
                self.assertEqual(a["samples"], n_expected)
                self.assertEqual(a["sample_rate"], 16000)
                self.assertEqual(a["channels"], 2)
                self.assertEqual(a["codec_tag"], "ipcm")
                pcm = decode_pcm(o["path"])                                  # ffmpeg membaca & PCM identik dengan sumber
                self.assertEqual(pcm, source_pcm(i, self.truth))
                h, n = verify.audio_payload_sha256(o["path"])
                self.assertEqual((h, n), (hashlib.sha256(pcm).hexdigest(), n_expected))
            for t, v in r["verify"].items():
                self.assertTrue(v["quick"])
                self.assertTrue(v["audio"]["/ego/audio/0"]["payload_matches_source"])
            total += n_expected
        self.assertEqual(total, self.truth["audio_total_samples"])

    def test_ac24_continuous_audio_has_no_silence_fill(self):
        r = convert(self.paths[0], self.d / "out")
        self.assertFalse([w for w in r["warnings"] if w["code"].startswith("audio_")])
        side = __import__("json").load(open(r["sidecar"], encoding="utf-8"))
        a = side["audio"][0]
        self.assertEqual((a["gaps"], a["silence_samples"], a["overlaps"]), (0, 0, 0))
        self.assertEqual(a["format"], "pcm-s16")

    def test_gap_is_filled_with_silence_and_warned(self):
        st = [E.T0 + E.AUDIO_OFFSET + j * E.AUDIO_STEP for j in range(60)]
        st = st[:20] + [t + 5 * E.AUDIO_STEP for t in st[20:]]               # celah 80 ms sesudah pesan ke-20
        p = write_custom(self.d / "gap.mcap", st)
        r = convert(p, self.d / "out", verify="full")
        self.assertEqual(r["status"], "success", r)
        gaps = [w for w in r["warnings"] if w["code"] == "audio_gap"]
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0]["duration_ms"], 80.0, places=1)
        self.assertAlmostEqual(gaps[0]["position_s"], 20 * 0.016, places=3)
        a = r["outputs"][0]["audio"][0]
        self.assertEqual(a["samples"], 60 * E.SPM + 5 * E.SPM)
        pcm = decode_pcm(r["outputs"][0]["path"])
        mid = pcm[20 * E.SPM * 4: 25 * E.SPM * 4]
        self.assertEqual(mid, bytes(len(mid)))                                  # nol sepanjang celah
        self.assertEqual(pcm[:20 * E.SPM * 4], b"".join(E.pcm_chunk(j * E.SPM) for j in range(20)))

    def test_gap_ignore_policy(self):
        st = [E.T0 + E.AUDIO_OFFSET + j * E.AUDIO_STEP for j in range(30)]
        st = st[:10] + [t + 3 * E.AUDIO_STEP for t in st[10:]]
        p = write_custom(self.d / "gap.mcap", st)
        r = convert(p, self.d / "out", audio_gap_policy="ignore")
        self.assertEqual(r["outputs"][0]["audio"][0]["samples"], 30 * E.SPM)
        self.assertEqual([w["code"] for w in r["warnings"] if w["code"] == "audio_gap"], ["audio_gap"])

    def test_overlap_is_trimmed(self):
        st = [E.T0 + E.AUDIO_OFFSET + j * E.AUDIO_STEP for j in range(30)]
        st = st[:10] + [t - 4_000_000 for t in st[10:]]                         # 4 ms = 64 sampel tumpang-tindih
        p = write_custom(self.d / "ov.mcap", st)
        r = convert(p, self.d / "out")
        self.assertEqual(r["outputs"][0]["audio"][0]["samples"], 30 * E.SPM - 64)
        self.assertEqual(len([w for w in r["warnings"] if w["code"] == "audio_overlap"]), 1)

    def test_av_offset_is_written_as_edit_list(self):
        r = convert(self.paths[0], self.d / "out")
        m = verify.parse_mp4(r["outputs"][0]["path"])
        self.assertEqual(m["audio"][0]["empty_edit_ms"], 32)
        self.assertAlmostEqual(r["outputs"][0]["audio"][0]["offset_to_video_ms"], 32.0, delta=0.01)
        r2 = convert(self.paths[0], self.d / "out2", audio_offset_ms=-10.0)
        m2 = verify.parse_mp4(r2["outputs"][0]["path"])
        self.assertEqual(m2["audio"][0]["empty_edit_ms"], 22)
        r3 = convert(self.paths[0], self.d / "out3", audio_offset_ms=-50.0)    # audio mulai lebih awal -> video yang tertunda
        m3 = verify.parse_mp4(r3["outputs"][0]["path"])
        self.assertEqual(m3["audio"][0]["empty_edit_ms"], 0)
        self.assertEqual(m3["empty_edit_ms"], 18)

    def test_unsupported_audio_format_does_not_fail_video(self):
        st = [E.T0 + E.AUDIO_OFFSET + j * E.AUDIO_STEP for j in range(20)]
        p = write_custom(self.d / "fl.mcap", st, fmt=b"pcm-f32")
        r = convert(p, self.d / "out")
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(r["outputs"][0]["frames"], 90)
        self.assertEqual(r["outputs"][0]["audio"], [])
        self.assertIn("unsupported_audio_format", [w["code"] for w in r["warnings"]])
        self.assertEqual(verify.parse_mp4(r["outputs"][0]["path"])["audio"], [])

    def test_audio_in_all_first_none(self):
        for mode, expect in (("all", [1, 1]), ("first", [1, 0]), ("none", [0, 0])):
            r = convert(self.paths[0], self.d / f"o_{mode}", audio_in=mode)
            self.assertEqual(r["status"], "success", r)
            got = [len(verify.parse_mp4(o["path"])["audio"]) for o in sorted(r["outputs"], key=lambda o: o["topic"])]
            self.assertEqual(got, expect, mode)

    def test_sowt_sample_entry_option(self):
        r = convert(self.paths[0], self.d / "out", audio_codec_tag="sowt", verify="full")
        self.assertEqual(r["status"], "success", r)
        o = r["outputs"][0]
        self.assertEqual(verify.parse_mp4(o["path"])["audio"][0]["entry"], "sowt")
        self.assertEqual(decode_pcm(o["path"]), source_pcm(0, self.truth))

    def test_video_only_files_unchanged(self):
        st = []
        p = write_custom(self.d / "v.mcap", st)
        r = convert(p, self.d / "out")
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(r["outputs"][0]["audio"], [])
        self.assertTrue(verify.quick(r["outputs"][0]["path"])["ok"])


class AudioUnitTests(unittest.TestCase):
    def test_parse_raw_audio_roundtrip(self):
        c = audio.parse_raw_audio(E.raw_audio_msg(1_500_000_123, E.pcm_chunk(0)))
        self.assertEqual((c.stamp_ns, c.fmt, c.rate, c.channels, len(c.data)), (1_500_000_123, "pcm-s16", 16000, 2, 1024))


if __name__ == "__main__":
    unittest.main()
