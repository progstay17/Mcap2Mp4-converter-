import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import mcap_reader as R
from mcap_synth import write_mcap

CH = [(1, "/a/cam/0", "foxglove.CompressedVideo", "protobuf", "protobuf"),
      (2, "/imu", "sensor_msgs/msg/Imu", "ros2msg", "cdr")]
MSG = [(1 if i % 2 == 0 else 2, 1000 + i * 10, bytes([i]) * (i + 1)) for i in range(10)]


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def _roundtrip(self, **kw):
        p = write_mcap(self.d / "x.mcap", CH, MSG, **kw)
        got = [(c.topic, m.log_time, m.data) for c, m in R.iter_messages(p)]
        self.assertEqual(got, [({1: "/a/cam/0", 2: "/imu"}[c], t, d) for c, t, d in MSG])
        return p

    def test_none_zstd_lz4_unchunked(self):
        for comp in ("", "zstd", "lz4"):
            self._roundtrip(compression=comp)
        self._roundtrip(chunked=False)

    def test_topic_filter(self):
        p = write_mcap(self.d / "x.mcap", CH, MSG)
        self.assertEqual({c.topic for c, _ in R.iter_messages(p, topics={"/imu"})}, {"/imu"})

    def test_summary_index(self):
        p = write_mcap(self.d / "x.mcap", CH, MSG, compression="zstd", profile="ros2", library="L")
        s = R.read_summary(p)
        self.assertTrue(s.from_index)
        self.assertEqual((s.profile, s.library), ("ros2", "L"))
        self.assertEqual(s.message_count, 10)
        self.assertEqual((s.message_start_time, s.message_end_time), (1000, 1090))
        self.assertEqual(s.channel_message_counts, {1: 5, 2: 5})
        self.assertEqual(s.channels[1].topic, "/a/cam/0")
        self.assertEqual(s.schemas[1].name, "foxglove.CompressedVideo")
        self.assertEqual(s.compression, {"zstd"})

    def test_summary_fallback_scan(self):
        p = write_mcap(self.d / "x.mcap", CH, MSG, summary=False)
        s = R.read_summary(p)
        self.assertFalse(s.from_index)
        self.assertEqual(s.message_count, 10)
        self.assertEqual(s.channel_message_counts, {1: 5, 2: 5})
        with self.assertRaises(R.McapError):
            R.read_summary(p, allow_scan=False)

    def test_bad_magic(self):
        p = self.d / "bad.mcap"; p.write_bytes(b"not an mcap file at all")
        with self.assertRaises(R.McapError):
            list(R.iter_records(p))

    def test_truncated_is_error_not_silent(self):
        p = write_mcap(self.d / "x.mcap", CH, MSG, summary=False)
        p.write_bytes(p.read_bytes()[:-40])
        with self.assertRaises(R.McapError):
            list(R.iter_messages(p))

    def test_unsupported_compression(self):
        with self.assertRaises(R.McapError):
            R._decompress("brotli", b"", 0)

    def test_progress_reaches_total(self):
        p = write_mcap(self.d / "x.mcap", CH, MSG)
        seen = []
        list(R.iter_records(p, progress=lambda d, t: seen.append((d, t))))
        self.assertEqual(seen[-1][0], seen[-1][1])


if __name__ == "__main__":
    unittest.main()
