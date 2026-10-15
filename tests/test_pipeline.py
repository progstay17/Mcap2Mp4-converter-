import sys, tempfile, unittest, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import worker, verify, settings, mp4_writer
from mcap_synth import write_mcap
import hevc_helper as H

BASE = 1_700_000_000 * 10**9          # epoch absolut
STEP = 33_366_667                     # 1/29.97 dtk (ns)
FOX = "foxglove.CompressedVideo"
ROS = "sensor_msgs/msg/CompressedImage"


def mk(path, kind, n_by_topic, fmt="h265", stamp0=BASE, frames=90, log_shift=None, extra=None):
    aus = H.hevc_access_units(frames)
    ch, msgs = [], []
    for i, (topic, n) in enumerate(n_by_topic.items(), 1):
        if kind == "foxglove":
            ch.append((i, topic, FOX, "protobuf", "protobuf"))
        else:
            ch.append((i, topic, ROS, "ros2msg", "cdr"))
        for k in range(n):
            ts = stamp0 + k * STEP
            payload = aus[k]
            body = (H.foxglove_msg if kind == "foxglove" else H.ros2_msg)(payload, ts, fmt)
            msgs.append((i, ts + (log_shift or 0) + i, body))
    for cid, topic, sname, senc, menc, n in (extra or []):
        ch.append((cid, topic, sname, senc, menc))
        for k in range(n):
            msgs.append((cid, stamp0 + k * 5_000_000, b"\x00" * 16))
    msgs.sort(key=lambda m: m[1])
    return write_mcap(path, ch, msgs, compression="zstd", chunk_size=20, profile="x")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src, self.out = self.d / "src", self.d / "out"
        self.src.mkdir()
        self.s = settings.load()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_file(self, p, **over):
        s = settings.load(); s.update(over)
        if "sync" in over: s["sync"] = {"policy": over["sync"], "tolerance_frames": 0.5}
        return worker.convert_file({"src": str(p), "rel": p.name, "out_dir": str(self.out), "settings": s})

    def partials(self):
        return [p for p in self.d.rglob("*.partial")]

    def test_ros2_single_channel(self):
        p = mk(self.src / "rek_01.mcap", "ros2", {"/head/cam": 90})
        r = self.run_file(p)
        self.assertEqual(r["status"], "success", r)
        o = self.out / "rek_01.mp4"
        self.assertTrue(o.exists())
        pr = H.ffprobe(o)
        self.assertEqual((pr["codec"], pr["w"], pr["h"], pr["frames"]), ("hevc", 320, 240, 90))
        self.assertAlmostEqual(pr["duration"], 90 * STEP / 1e9, delta=0.04)      # tanpa drift (D3)
        self.assertEqual(r["time_source"], "payload_timestamp")
        self.assertTrue(verify.quick(o)["ok"])
        self.assertEqual(r["outputs"][0]["creation_source"], "payload_timestamp")
        self.assertTrue((self.out / "rek_01.metadata.json").exists())
        self.assertEqual(self.partials(), [])

    def test_foxglove_two_channels_names_and_sync_ok(self):
        p = mk(self.src / "a.mcap", "foxglove", {"/a/cam/0": 60, "/a/cam/1": 60})
        r = self.run_file(p)
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(sorted(x.name for x in self.out.glob("*.mp4")), ["a_a_cam_0.mp4", "a_a_cam_1.mp4"])
        for x in self.out.glob("*.mp4"):
            self.assertEqual(H.ffprobe(x)["frames"], 60)

    def test_sync_strict_blocks_publish(self):
        p = mk(self.src / "b.mcap", "foxglove", {"/c0": 60, "/c1": 59})
        r = self.run_file(p)
        self.assertEqual(r["status"], "out_of_sync")
        self.assertEqual(r["sync"]["counts"], {"/c0": 60, "/c1": 59})
        self.assertEqual(r["sync"]["reason"], "frame_count")
        self.assertEqual(list(self.out.glob("*.mp4")) if self.out.exists() else [], [])
        self.assertEqual(self.partials(), [])

    def test_sync_warn_and_trim(self):
        p = mk(self.src / "b.mcap", "foxglove", {"/c0": 60, "/c1": 59})
        r = self.run_file(p, sync="warn")
        self.assertEqual(r["status"], "success")
        self.assertEqual(sorted(H.ffprobe(x)["frames"] for x in self.out.glob("*.mp4")), [59, 60])
        shutil.rmtree(self.out)
        r = self.run_file(p, sync="trim", verify="full")
        self.assertEqual(r["status"], "success", r)
        self.assertEqual([H.ffprobe(x)["frames"] for x in self.out.glob("*.mp4")], [59, 59])

    def test_sync_tolerance_mismatch(self):
        p = mk(self.src / "c.mcap", "foxglove", {"/c0": 60, "/c1": 60})
        # geser log_time channel 2 sebesar 2 frame -> tidak sinkron
        raw = mk(self.src / "d.mcap", "foxglove", {"/c0": 60, "/c1": 60}, log_shift=0)
        from mcap2mp4 import sync
        t0 = [i * STEP for i in range(60)]
        t1 = [i * STEP + 2 * STEP for i in range(60)]
        r = sync.compare({"a": t0, "b": t1})
        self.assertFalse(r["ok"]); self.assertEqual(r["reason"], "tolerance"); self.assertEqual(r["first_mismatch"]["index"], 0)

    def test_skip_then_incomplete(self):
        p = mk(self.src / "e.mcap", "foxglove", {"/c0": 30, "/c1": 30})
        self.assertEqual(self.run_file(p)["status"], "success")
        self.assertEqual(self.run_file(p)["status"], "skipped")
        victim = next(self.out.glob("*_c1.mp4"))
        victim.unlink()
        keep = next(self.out.glob("*_c0.mp4")); size = keep.stat().st_size
        r = self.run_file(p)
        self.assertEqual(r["status"], "incomplete")
        self.assertEqual(keep.stat().st_size, size)          # tidak ditimpa (FR-21/22)
        self.assertFalse(victim.exists())
        r = self.run_file(p, on_exists="number")              # alternatif: beri nomor
        self.assertEqual(r["status"], "success")
        self.assertTrue((self.out / "e_2_c0.mp4").exists())

    def test_co64_always_and_forced_large(self):
        p = mk(self.src / "f.mcap", "ros2", {"/h": 90})
        r = self.run_file(p, co64="always")
        self.assertTrue(r["outputs"][0]["co64"])
        self.assertEqual(H.ffprobe(self.out / "f.mp4")["frames"], 90)
        self.assertTrue(verify.parse_mp4(self.out / "f.mp4")["co64"])

    def test_co64_auto_crosses_threshold_and_off_fails(self):
        p = mk(self.src / "g.mcap", "ros2", {"/h": 90})
        old = mp4_writer.U32_MAX
        try:
            mp4_writer.U32_MAX = 12_000                      # hook uji offset: seolah-olah batas 4 GB = 12 KB
            r = self.run_file(p, co64="auto")
            self.assertEqual(r["status"], "success", r)
            self.assertTrue(r["outputs"][0]["co64"] and r["outputs"][0]["mdat_largesize"])
            pr = H.ffprobe(self.out / "g.mp4")
            self.assertEqual(pr["frames"], 90)
            self.assertTrue(verify.quick(self.out / "g.mp4")["ok"])
            shutil.rmtree(self.out)
            r = self.run_file(p, co64="off")
            self.assertEqual(r["status"], "failed"); self.assertEqual(r["error_code"], "too_large")
            self.assertEqual(list(self.out.glob("*.mp4")), [])
            self.assertEqual(self.partials(), [])
        finally:
            mp4_writer.U32_MAX = old

    def test_unsupported_codec_per_channel(self):
        p = mk(self.src / "h.mcap", "foxglove", {"/c0": 10}, fmt="h264")
        r = self.run_file(p)
        self.assertEqual(r["status"], "failed"); self.assertEqual(r["error_code"], "unsupported_codec")

    def test_no_video_channel_and_other_topics(self):
        p = mk(self.src / "i.mcap", "ros2", {}, extra=[(9, "/imu", "sensor_msgs/msg/Imu", "ros2msg", "cdr", 20)])
        r = self.run_file(p)
        self.assertEqual(r["error_code"], "no_video_channel")

    def test_video_plus_imu_ok_and_topics_listed(self):
        p = mk(self.src / "j.mcap", "ros2", {"/h": 40}, extra=[(9, "/imu", "sensor_msgs/msg/Imu", "ros2msg", "cdr", 20)])
        r = self.run_file(p)
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["topics"], {"/h": 40, "/imu": 20})

    def test_relative_timestamps_fall_back_for_creation(self):
        p = mk(self.src / "k.mcap", "foxglove", {"/c": 30}, stamp0=5 * 10**9)
        r = self.run_file(p)
        self.assertEqual(r["time_source"], "payload_timestamp")           # selisih tetap dipakai untuk durasi
        self.assertEqual(r["outputs"][0]["creation_source"], "file_mtime")  # tapi bukan sebagai tanggal (FR-46)

    def test_fraction_mode(self):
        p = mk(self.src / "l.mcap", "ros2", {"/h": 90})
        r = self.run_file(p, fps_mode="fraction", fps_value="30000/1001")
        self.assertEqual(r["time_source"], "manual_fraction")
        self.assertAlmostEqual(H.ffprobe(self.out / "l.mp4")["duration"], 90 * 1001 / 30000, delta=0.001)

    def test_corrupt_file_fails_cleanly(self):
        p = self.src / "m.mcap"; p.write_bytes(b"\x89MCAP0\r\n" + b"\x01" + b"\xff" * 20)
        r = self.run_file(p)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(self.partials(), [])

    def test_full_verify(self):
        p = mk(self.src / "n.mcap", "foxglove", {"/c0": 45, "/c1": 45})
        r = self.run_file(p, verify="full")
        self.assertEqual(r["status"], "success", r)
        self.assertTrue(all(v["payload_matches_source"] for v in r["verify"].values()))


if __name__ == "__main__":
    unittest.main()
