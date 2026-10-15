"""Tes sesi M6 (FR-100..FR-111, AC-20..22, AC-32). Memakai tests/ego_synth.py (meniru rekaman 'ego' asli)."""
import hashlib, json, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import batch, cli, mcap_reader as R, session, settings, verify, worker
import ego_synth as E
import hevc_helper as H
from mcap_synth import write_mcap


def convert_task(paths, out, **over):
    s = settings.load()
    s.update(over)
    ses = session.detect([session.probe(p) for p in paths])
    assert len(ses) == 1, [x.files for x in ses]
    return session.merge(ses[0], s, out)


def payload_stream(mp4):
    m = verify.parse_mp4(mp4)
    h = hashlib.sha256()
    with open(mp4, "rb") as f:
        for off, sz in zip(m["offsets"], m["sizes"]):
            f.seek(off)
            h.update(f.read(sz))
    return h


def write_cam_part(path, aus, start_ns, topic="/cam/0"):
    msgs = [(1, start_ns + k * E.PERIOD, H.foxglove_msg(au, start_ns + k * E.PERIOD)) for k, au in enumerate(aus)]
    return write_mcap(path, [(1, topic, E.FOX_VIDEO, "protobuf", "protobuf")], msgs, compression="", chunk_size=40, library="t")


class SessionDetectionTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def sessions(self, folder):
        return session.detect_folder(folder)

    def test_ac20_consecutive_files_form_one_session(self):
        paths, truth = E.write_ego_session(self.d / "s", parts=2)
        ss = self.sessions(self.d / "s")
        self.assertEqual(len(ss), 1)
        se = ss[0]
        self.assertEqual((se.count, se.id), (2, "ego_0014~ego_0015"))
        self.assertEqual(se.joins[0]["reason"], "continuous")
        self.assertAlmostEqual(se.joins[0]["gap_frames"], 1.0, places=2)
        # waktu per kamera dari indeks chunk, bukan ringkasan keseluruhan (yang tumpang-tindih karena IMU)
        a, b = (R.read_summary(p) for p in paths)
        self.assertLess(b.message_start_time, a.message_end_time)
        ta, tb = se.traces
        self.assertEqual(ta.video["/ego/camera/0"].first_ns, truth["parts"][0]["cam0_first"])
        self.assertEqual(tb.video["/ego/camera/1"].last_ns, truth["parts"][1]["cam1_last"])
        self.assertAlmostEqual(se.duration_s(), 6.0, places=2)
        # nomor channel tertukar antar-file tidak mengganggu (pencocokan lewat nama topik)
        self.assertNotEqual({c.topic: c.id for c in R.read_summary(paths[0]).channels.values()}["/ego/camera/0"],
                            {c.topic: c.id for c in R.read_summary(paths[1]).channels.values()}["/ego/camera/0"])

    def test_ac21_non_matching_files_stay_separate(self):
        cases = {"gap_too_large": dict(gap_frames=900.0), "overlap": dict(gap_frames=0.2), "time_backwards": dict(gap_frames=-5.0)}
        for reason, kw in cases.items():
            f = self.d / reason
            E.write_ego_session(f, parts=2, **kw)
            ss = self.sessions(f)
            self.assertEqual([s.count for s in ss], [1, 1], reason)
            self.assertEqual(ss[0].split_reason["reason"], reason)
        # boot ulang: waktu mulai kembali kecil -> terpisah
        f = self.d / "reboot"
        E.write_ego_session(f, parts=1, first_index=14)
        E.write_ego_session(f, parts=1, first_index=15, t0=5_000_000)
        self.assertEqual([s.count for s in self.sessions(f)], [1, 1])
        # topik berbeda
        f = self.d / "topics"
        aus = H.hevc_access_units(30)
        write_cam_part(f / "a.mcap", aus, E.T0, "/cam/0") if (f.mkdir() or True) else None
        write_cam_part(f / "b.mcap", aus, E.T0 + 30 * E.PERIOD, "/cam/9")
        ss = self.sessions(f)
        self.assertEqual([s.count for s in ss], [1, 1])
        self.assertEqual(ss[0].split_reason["reason"], "topics_differ")
        # resolusi berbeda
        f = self.d / "res"
        f.mkdir()
        write_cam_part(f / "a.mcap", H.hevc_access_units(30), E.T0)
        write_cam_part(f / "b.mcap", H.hevc_access_units(30, size="160x120"), E.T0 + 30 * E.PERIOD)
        ss = self.sessions(f)
        self.assertEqual(ss[0].split_reason["reason"], "codec_differs")
        # folder berbeda: sesi tidak melintasi folder
        a, b = self.d / "f1", self.d / "f2"
        E.write_ego_session(a, parts=1, first_index=14)
        pb, _ = E.write_ego_session(b, parts=1, first_index=15, t0=E.T0 + 90 * E.PERIOD)
        t = [session.probe(p) for p in list((a).glob("*.mcap")) + list(b.glob("*.mcap"))]
        self.assertEqual(sorted(s.count for s in session.detect(t)), [1, 1])
        self.assertEqual(session.evaluate_join(t[0], t[1])["reason"], "different_folder")

    def test_ac20b_probe_is_cheap(self):
        paths, _ = E.write_ego_session(self.d / "one", parts=1, frames=90)
        calls = []
        orig = R._read_chunk_messages
        R._read_chunk_messages = lambda f, off: (calls.append(off), orig(f, off))[1]
        try:
            session.probe(paths[0])
        finally:
            R._read_chunk_messages = orig
        self.assertLessEqual(len(calls), 4)                                  # indeks 47 chunk, hanya ujung yang dibuka
        E.write_ego_session(self.d / "many", parts=100, frames=4)
        t0 = time.time()
        ss = session.detect_folder(self.d / "many")
        self.assertLess(time.time() - t0, 3.0)                                # target PRD <1 dtk pada SSD; longgar untuk sandbox
        self.assertEqual(len(ss), 1)
        self.assertEqual(ss[0].count, 100)

    def test_ac22_merge_two_parts_per_camera(self):
        paths, truth = E.write_ego_session(self.d / "s", parts=2)
        out = self.d / "out"
        r = convert_task(paths, out, verify="full")
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(sorted(o["topic"] for o in r["outputs"]), ["/ego/camera/0", "/ego/camera/1"])
        solo = settings.load()
        parts = [worker.convert_file({"src": str(p), "rel": p.name, "out_dir": str(self.d / "solo"), "settings": solo}) for p in paths]
        for o in r["outputs"]:
            self.assertEqual(Path(o["path"]).name.split("_")[0], "ego")
            self.assertTrue(Path(o["path"]).name.startswith("ego_0014~ego_0015"))
            self.assertEqual(o["frames"], 180)
            self.assertAlmostEqual(o["duration_s"], 6.0, delta=0.034)
            self.assertEqual(o["keyframes"], 6)
            h = hashlib.sha256()
            for pr in parts:                                               # hash payload = sambungan hash sumber
                p = next(x for x in pr["outputs"] if x["topic"] == o["topic"])["path"]
                h.update(b"".join(self._samples(p)))
            self.assertEqual(o["sha256"], h.hexdigest())
            dec = subprocess.run(["ffmpeg", "-v", "error", "-i", o["path"], "-map", "0:v:0", "-f", "null", "-"], capture_output=True, text=True)
            self.assertEqual((dec.returncode, dec.stderr.strip()), (0, ""))
            self.assertEqual(H.ffprobe(o["path"])["frames"], 180)
            self.assertTrue(r["verify"][o["topic"]]["quick"])
            self.assertTrue(r["verify"][o["topic"]]["payload_matches_source"])
        self.assertTrue(r["sync"]["ok"])
        # audio kontinu melewati sambungan: satu offset awal, tanpa celah (AC-23/24 versi sesi)
        a = r["outputs"][0]["audio"][0]
        self.assertEqual(a["samples"], truth["audio_total_samples"])
        self.assertEqual(a["gaps"], 0)
        self.assertAlmostEqual(a["offset_to_video_ms"], 32.0, delta=0.01)
        self.assertFalse([w for w in r["warnings"] if w["code"].startswith("audio_") or w["code"] == "session_leading_dropped"])
        self.assertTrue(r["outputs"][0]["audio"][0]["sha256"])
        side = json.load(open(r["sidecar"], encoding="utf-8"))
        self.assertEqual(side["session"]["count"], 2)
        self.assertEqual(side["source"], ["ego_0014.mcap", "ego_0015.mcap"])
        self.assertEqual(side["video"][0]["trace"]["frames"], 180)

    @staticmethod
    def _samples(mp4):
        m = verify.parse_mp4(mp4)
        with open(mp4, "rb") as f:
            for off, sz in zip(m["offsets"], m["sizes"]):
                f.seek(off)
                yield f.read(sz)

    def test_manual_override_persists_in_sessions_json(self):
        f = self.d / "s"
        E.write_ego_session(f, parts=2, gap_frames=900.0)
        self.assertEqual([s.count for s in self.sessions(f)], [1, 1])
        session.set_override(f, "ego_0014.mcap", "ego_0015.mcap", "join")
        ss = self.sessions(f)                                               # "restart": dibaca ulang dari disk
        self.assertEqual((len(ss), ss[0].count, ss[0].joins[0]["reason"], ss[0].origin), (1, 2, "manual_join", "manual"))
        data = json.loads((f / "sessions.json").read_text(encoding="utf-8"))
        self.assertEqual(data["overrides"]["join"], [["ego_0014.mcap", "ego_0015.mcap"]])
        session.set_override(f, "ego_0014.mcap", "ego_0015.mcap", "split")
        self.assertEqual([s.count for s in self.sessions(f)], [1, 1])
        self.assertEqual(session.load_overrides(f), {"join": [], "split": [["ego_0014.mcap", "ego_0015.mcap"]]})
        session.set_override(f, "ego_0014.mcap", "ego_0015.mcap", "clear")
        self.assertEqual(session.load_overrides(f), {"join": [], "split": []})
        # gabung paksa juga berlaku saat konversi
        session.set_override(f, "ego_0014.mcap", "ego_0015.mcap", "join")
        s = settings.load(); s.update(source_dir=str(f), output_dir=str(self.d / "o"), session_mode="merged")
        ents = batch.group_sessions(s, batch.scan(s))
        self.assertEqual([(len(e.parts), e.stem_override) for e in ents], [(2, "ego_0014~ego_0015")])

    def test_leading_non_keyframes_in_later_part_are_dropped_and_reported(self):
        f = self.d / "s"
        f.mkdir()
        aus = H.hevc_access_units(150)
        write_cam_part(f / "a.mcap", aus[:60], E.T0)
        write_cam_part(f / "b.mcap", aus[65:], E.T0 + 60 * E.PERIOD)      # bagian B mulai di tengah GOP (AU 65, keyframe di 90)
        paths = [f / "a.mcap", f / "b.mcap"]
        r = convert_task(paths, self.d / "out", verify="full")
        self.assertEqual(r["status"], "success", r)
        o = r["outputs"][0]
        self.assertEqual(o["frames"], 60 + (85 - 25))
        self.assertEqual(o["dropped_seam_frames"], 25)
        w = [x for x in r["warnings"] if x["code"] == "session_leading_dropped"]
        self.assertEqual((len(w), w[0]["frames"], w[0]["part"]), (1, 25, 2))
        dec = subprocess.run(["ffmpeg", "-v", "error", "-i", o["path"], "-f", "null", "-"], capture_output=True, text=True)
        self.assertEqual((dec.returncode, dec.stderr.strip()), (0, ""))
        self.assertTrue(r["verify"]["/cam/0"]["payload_matches_source"])

    def test_part_failure_fails_whole_session_and_names_part(self):
        paths, _ = E.write_ego_session(self.d / "s", parts=2)
        data = bytearray(paths[1].read_bytes())
        paths[1].write_bytes(bytes(data[: len(data) // 2]))                # potong bagian ke-2
        s = settings.load()
        ses = session.Session("x", "k", str(paths[0].parent), [session.probe(paths[0])] + [type("T", (), {"path": str(paths[1]), "name": paths[1].name})()])
        r = session.merge(ses, s, self.d / "out")
        self.assertNotEqual(r["status"], "success")
        self.assertFalse(list((self.d / "out").glob("*.mp4")) if (self.d / "out").exists() else [])   # tanpa hasil setengah jadi

    def test_batch_merged_end_to_end_via_cli(self):
        E.write_ego_session(self.d / "s", parts=3)
        rc = cli.main(["--src", str(self.d / "s"), "--dst", str(self.d / "o"), "--session-mode", "merged", "--workers", "1",
                       "--reports", "json", "--verify", "quick"])
        self.assertEqual(rc, 0)
        outs = sorted(p.name for p in (self.d / "o").rglob("*.mp4"))
        self.assertEqual(outs, ["ego_0014~ego_0016_ego_camera_0.mp4", "ego_0014~ego_0016_ego_camera_1.mp4"])
        self.assertEqual(H.ffprobe(self.d / "o" / outs[0])["frames"], 270)
        # mode per_file (bawaan) tetap menghasilkan satu MP4 per file/kamera
        rc = cli.main(["--src", str(self.d / "s"), "--dst", str(self.d / "o2"), "--workers", "1", "--reports", "json"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(list((self.d / "o2").rglob("*.mp4"))), 6)


if __name__ == "__main__":
    unittest.main()
