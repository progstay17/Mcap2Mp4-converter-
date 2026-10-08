import os, sys, tempfile, unittest, shutil, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import worker, settings, batch, cli, i18n, source_adapters as sa
from mcap_synth import write_mcap
import hevc_helper as H
from test_pipeline import mk, BASE, STEP, FOX, ROS


class SettingsTests(unittest.TestCase):
    def test_load_does_not_leak_into_defaults(self):
        a = settings.load(); a["sync"]["policy"] = "trim"; a["metadata"]["sidecar_json"] = False
        b = settings.load()
        self.assertEqual(b["sync"]["policy"], "strict")
        self.assertTrue(b["metadata"]["sidecar_json"])


class AdapterEdgeTests(unittest.TestCase):
    def test_cdr_alignment_various_frame_ids(self):
        au = H.hevc_access_units(90)[0]
        for fid in ("", "a", "ab", "abc", "camera", "camera_front_left"):
            f = sa.parse_ros2_compressed_image(H.ros2_msg(au, BASE + 123, "h265", fid))
            self.assertEqual(f.payload, au, fid)
            self.assertEqual((f.fmt, f.stamp_ns), ("h265", BASE + 123))

    def test_foxglove_without_timestamp(self):
        raw = H._ld(3, b"\x00\x00\x00\x01xx") + H._ld(4, b"h265")
        f = sa.parse_foxglove_video(raw)
        self.assertIsNone(f.stamp_ns)

    def test_format_normalization(self):
        for raw, want in (("H265", "h265"), ("hevc", "h265"), ("h.265", "h265"), ("avc1", "h264"), ("jpeg", "jpeg"), ("", "unknown")):
            self.assertEqual(sa.normalize_format(raw), want)


class ConvertEdgeTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.src = self.d / "sumber dengan spasi"; self.src.mkdir()
        self.out = self.d / "hasil ünïcode"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_file(self, p, **over):
        s = settings.load(); s.update(over)
        return worker.convert_file({"src": str(p), "rel": p.name, "out_dir": str(self.out), "settings": s})

    def test_leading_non_keyframes_dropped(self):
        aus = H.hevc_access_units(90)
        ch = [(1, "/c", FOX, "protobuf", "protobuf")]
        msgs = [(1, BASE + i * STEP, H.foxglove_msg(aus[i], BASE + i * STEP)) for i in range(5, 90)]   # mulai di frame non-IDR
        p = write_mcap(self.src / "x.mcap", ch, msgs, compression="lz4", chunk_size=7)
        r = self.run_file(p)
        self.assertEqual(r["status"], "success", r)
        o = r["outputs"][0]
        self.assertEqual(o["dropped_leading_frames"], 25)          # frame 5..29 dibuang sampai IDR di frame 30
        self.assertEqual(o["frames"], 60)
        self.assertEqual(H.ffprobe(self.out / "x.mp4")["frames"], 60)

    def test_missing_and_nonmonotonic_stamps_fall_back(self):
        aus = H.hevc_access_units(90)
        ch = [(1, "/c", FOX, "protobuf", "protobuf")]
        msgs = [(1, BASE + i * STEP, H._ld(3, aus[i]) + H._ld(4, b"h265")) for i in range(40)]     # tanpa timestamp pesan
        p = write_mcap(self.src / "a.mcap", ch, msgs)
        r = self.run_file(p)
        self.assertEqual(r["time_source"], "log_time")
        self.assertIn("time_fallback", [w["code"] for w in r["warnings"]])
        self.assertAlmostEqual(H.ffprobe(self.out / "a.mp4")["duration"], 40 * STEP / 1e9, delta=0.05)
        # stempel sama semua -> tidak monoton -> jatuh ke log_time
        msgs = [(1, BASE + i * STEP, H.foxglove_msg(aus[i], BASE)) for i in range(40)]
        p = write_mcap(self.src / "b.mcap", ch, msgs)
        r = self.run_file(p)
        self.assertEqual(r["time_source"], "log_time")

    def test_session_json_used_when_no_timestamps(self):
        aus = H.hevc_access_units(90)
        ch = [(1, "/c", FOX, "protobuf", "protobuf")]
        msgs = [(1, BASE, H._ld(3, aus[i]) + H._ld(4, b"h265")) for i in range(30)]               # log_time juga sama
        (self.src / "session.json").write_text('{"fps": 25}')
        p = write_mcap(self.src / "c.mcap", ch, msgs)
        r = self.run_file(p)
        self.assertEqual(r["time_source"], "session_json")
        self.assertAlmostEqual(H.ffprobe(self.out / "c.mp4")["duration"], 30 / 25, delta=0.001)

    def test_mixed_codecs_converts_supported_channel(self):
        aus = H.hevc_access_units(90)
        ch = [(1, "/ok", FOX, "protobuf", "protobuf"), (2, "/h264", FOX, "protobuf", "protobuf")]
        msgs = []
        for i in range(30):
            msgs.append((1, BASE + i * STEP, H.foxglove_msg(aus[i], BASE + i * STEP)))
            msgs.append((2, BASE + i * STEP + 1, H.foxglove_msg(aus[i], BASE + i * STEP, "h264")))
        p = write_mcap(self.src / "m.mcap", ch, msgs)
        r = self.run_file(p)
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(len(r["outputs"]), 1)
        self.assertEqual(r["channel_errors"][0]["code"], "unsupported_codec")
        self.assertEqual(list(self.out.glob(".*partial")), [])

    def test_video_channel_without_messages(self):
        ch = [(1, "/c", FOX, "protobuf", "protobuf")]
        p = write_mcap(self.src / "e.mcap", ch, [])
        r = self.run_file(p)
        self.assertEqual((r["status"], r["error_code"]), ("failed", "no_video_samples"))


def _tree(root, spec):
    root.mkdir(parents=True, exist_ok=True)
    for rel, n in spec.items():
        pth = root / rel; pth.parent.mkdir(parents=True, exist_ok=True)
        mk(pth, "ros2", {"/h": n})


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.src = self.d / "src"; self.out = self.d / "out"
        self.s = settings.load(); self.s.update(source_dir=str(self.src), output_dir=str(self.out), workers=2)
        self.s["reports"]["formats"] = []

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_flat_mode_same_stem_not_skipped(self):
        _tree(self.src, {"a/rek.mcap": 30, "b/rek.mcap": 40})
        self.s["mirror_structure"] = False
        res, summ = batch.run(self.s)
        self.assertEqual(summ["counts"]["success"], 2, res)
        names = sorted(p.name for p in self.out.glob("*.mp4"))
        self.assertEqual(names, ["a_rek.mp4", "b_rek.mp4"])
        frames = sorted(H.ffprobe(p)["frames"] for p in self.out.glob("*.mp4"))
        self.assertEqual(frames, [30, 40])
        res2, summ2 = batch.run(self.s)                                      # ulang: keduanya dilewati lewat manifest
        self.assertEqual(summ2["counts"]["skipped"], 2)

    def test_worker_crash_isolated(self):
        _tree(self.src, {"ok1.mcap": 30, "crash.mcap": 30, "ok2.mcap": 30, "ok3.mcap": 30})
        os.environ["MCAP2MP4_TEST_CRASH"] = "crash.mcap"
        try:
            res, summ = batch.run(self.s)
        finally:
            del os.environ["MCAP2MP4_TEST_CRASH"]
        by = {r["source"]: r for r in res}
        self.assertEqual(by["crash.mcap"]["error_code"], "worker_crashed")
        for k in ("ok1.mcap", "ok2.mcap", "ok3.mcap"):
            self.assertEqual(by[k]["status"], "success", by[k])
        self.assertEqual(summ["counts"]["failed"], 1)
        self.assertEqual(list(self.out.glob(".*partial")), [])

    def test_cancel_stops_cleanly(self):
        _tree(self.src, {f"f{i}.mcap": 30 for i in range(6)})
        ctl = batch.RunControl()
        self.s["workers"] = 1
        seen = []

        def ev(kind, data):
            if kind == "done":
                seen.append(data[2]["status"])
                ctl.cancel.set()                         # batal setelah file pertama selesai
        res, summ = batch.run(self.s, ev, ctl)
        self.assertTrue(summ["cancelled"])
        self.assertLess(len(res), 6)
        self.assertEqual(list(self.out.rglob(".*partial")), [])
        ctl2 = batch.RunControl()
        res, summ = batch.run(self.s, None, ctl2)         # lanjut: sisanya diproses, yang selesai dilewati
        self.assertEqual(sum(1 for r in res if r["status"] in ("success", "skipped")), 6)

    def test_pause_blocks_then_resumes(self):
        _tree(self.src, {f"p{i}.mcap": 30 for i in range(3)})
        ctl = batch.RunControl(); ctl.pause.set()
        self.s["workers"] = 1
        out = {}
        th = threading.Thread(target=lambda: out.update(r=batch.run(self.s, None, ctl)))
        th.start(); time.sleep(1.5)
        try:
            self.assertEqual(list(self.out.glob("*.mp4")) if self.out.exists() else [], [])    # jeda sejak awal: belum ada hasil
        finally:
            ctl.pause.clear()
            th.join(60)
        self.assertEqual(out["r"][1]["counts"]["success"], 3)


class CliTextTests(unittest.TestCase):
    def test_no_indonesian_in_english_summary(self):
        i18n.set_language("en")
        txt = cli.describe_result({"status": "success", "outputs": [{"frames": 5}], "elapsed_s": 1.0, "mb_per_s": 2})
        self.assertIn("frames", txt); self.assertNotIn("dtk", txt)
        i18n.set_language("id")

    def test_sync_reason_localized(self):
        i18n.set_language("en")
        txt = cli.describe_result({"status": "out_of_sync", "error_code": "out_of_sync",
                                   "sync": {"reason": "frame_count", "counts": {"a": 2, "b": 1}}})
        self.assertIn("frame counts differ", txt)
        i18n.set_language("id")


if __name__ == "__main__":
    unittest.main()
