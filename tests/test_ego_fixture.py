"""Tes fondasi: fixture 'ego' sintetis meniru temuan data nyata, dan konverter saat ini menanganinya."""
import shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import mcap_reader as R, settings, worker, verify
import hevc_helper as H
import ego_synth as E


class EgoFixtureTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.paths, self.truth = E.write_ego_session(self.d / "src", parts=2)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_summary_overlaps_but_video_is_continuous(self):
        a, b = (R.read_summary(p) for p in self.paths)
        self.assertLess(b.message_start_time, a.message_end_time)             # ringkasan: tumpang-tindih (dari IMU)
        ta, tb = self.truth["parts"]
        self.assertEqual(tb["cam0_first"] - ta["cam0_last"], E.PERIOD)       # video: tepat satu periode frame
        self.assertEqual(tb["audio_first_stamp"], ta["audio_first_stamp"] + ta["audio_messages"] * E.AUDIO_STEP)

    def test_channel_ids_are_permuted_between_files(self):
        ids = []
        for p in self.paths:
            s = R.read_summary(p)
            ids.append({c.topic: c.id for c in s.channels.values()})
        self.assertNotEqual(ids[0]["/ego/camera/0"], ids[1]["/ego/camera/0"])
        self.assertEqual(set(ids[0]), set(ids[1]))

    def test_audio_is_contiguous_and_totals_match(self):
        total = sum(p["audio_messages"] for p in self.truth["parts"]) * E.SPM
        self.assertEqual(total, self.truth["audio_total_samples"])

    def test_current_converter_handles_pair_per_file(self):
        out = self.d / "out"
        s = settings.load()
        for p in self.paths:
            r = worker.convert_file({"src": str(p), "rel": p.name, "out_dir": str(out), "settings": s})
            self.assertEqual(r["status"], "success", r)
            self.assertEqual(sorted(o["topic"] for o in r["outputs"]), ["/ego/camera/0", "/ego/camera/1"])
            for o in r["outputs"]:
                self.assertEqual(o["frames"], 90)
                self.assertEqual(o["dropped_leading_frames"], 0)
                self.assertEqual(o["creation_source"], "file_mtime")           # waktu relatif sejak boot bukan tanggal
                self.assertEqual(H.ffprobe(o["path"])["frames"], 90)
                self.assertTrue(verify.quick(o["path"])["ok"])


if __name__ == "__main__":
    unittest.main()
