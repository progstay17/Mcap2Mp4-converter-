"""Tes M8: sinkron dua kamera, sesi virtual, playlist, penjelajah file (FR-121..129, AC-26..29)."""
import json, os, random, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import settings, worker, session
from mcap2mp4.player import engine as E
from mcap2mp4.player.library import scan_folder
from mcap2mp4.player.model import Part, Recording
from mcap2mp4.player.playlist import Playlist
from mcap2mp4.player.sync import RecordingPlayer, SyncController
from fake_engine import FakeEngine
import ego_synth as ES


class SyncControllerTests(unittest.TestCase):
    def setUp(self):
        self.m, self.s = FakeEngine(60_000), FakeEngine(60_000)
        self.c = SyncController([self.m, self.s], threshold_ms=40, settle_s=0)
        self.c.open(["l.mp4", "r.mp4"])
        self.m.t = 5000

    def test_slave_corrected_when_drift_exceeds_threshold(self):
        self.s.t = 5100                                       # slave 100 ms di depan
        self.assertEqual(self.c.tick()[0][:2], (1, "rate"))
        self.assertLess(self.s.rate, self.c.rate)             # diperlambat
        for _ in range(80):                                   # simulasi 100 ms/tick; slave lebih lambat -> selisih mengecil
            self.m.advance(100); self.s.advance(100); self.c.tick()
            if self.s.rate == self.c.rate:
                break
        self.assertLessEqual(abs(self.s.t - self.m.t), 20)
        self.assertEqual(self.s.rate, self.c.rate)            # laju dipulihkan setelah masuk ambang
        self.s.t = self.m.t - 90                              # slave tertinggal -> dipercepat
        self.c.tick(); self.assertGreater(self.s.rate, self.c.rate)

    def test_large_drift_and_paused_drift_use_seek(self):
        self.s.t = 7000
        self.assertEqual(self.c.tick()[0][1], "seek"); self.assertEqual(self.s.t, 5000)
        self.c.pause(); self.s.t = 5090
        self.assertEqual(self.c.tick()[0][1], "seek"); self.assertEqual(self.s.t, 5000)
        self.assertEqual(self.c.corrections["seek"], 2)

    def test_within_threshold_untouched_and_stats_logged(self):
        self.s.t = 5020
        self.assertEqual(self.c.tick(), [])
        self.s.t = 4990; self.c.tick()
        st = self.c.drift_stats()
        self.assertEqual((st["max_ms"], st["samples"]), (20, 2))

    def test_seek_pause_speed_apply_to_both_cameras(self):
        self.c.set_rate(0.5); self.c.seek_ms(12_000); self.c.pause()
        for e in (self.m, self.s):
            self.assertEqual((e.rate, e.time_ms, e.playing), (0.5, 12_000, False))
        self.c.resume(); self.assertTrue(self.m.playing and self.s.playing)

    def test_audio_only_from_master_and_auto_mute(self):
        self.assertEqual((self.m.muted, self.s.muted), (False, True))
        self.c.set_rate(3.0)
        self.assertEqual((self.m.muted, self.s.muted), (True, True))
        self.c.set_rate(1.0); self.c.toggle_mute()
        self.assertEqual((self.m.muted, self.s.muted), (True, True))


def make_rec(durs=(3, 4), cams=("/c/0", "/c/1")):
    return Recording("sesi", [Part({t: f"p{i}_{t[-1]}.mp4" for t in cams}, d) for i, d in enumerate(durs)], "session")


def make_player(rec, clock=None):
    lengths = {f"p{i}_{c}.mp4": int(p.duration_s * 1000) for i, p in enumerate(rec.parts) for c in "01"}
    made = []
    def factory():
        e = FakeEngine(1000, lengths)
        made.append(e)
        return e
    pl = RecordingPlayer(factory, preload_s=3.0, clock=clock or time.monotonic, settle_s=0)
    pl.made = made
    return pl


def run(pl, ms, step=100):
    for _ in range(ms // step):
        for e in list(pl.active.engines) + (list(pl.standby.engines) if pl.standby else []):
            e.advance(step)
        pl.tick()


class VirtualSessionTests(unittest.TestCase):
    def test_total_seekbar_maps_to_part_and_offset(self):
        rec = make_rec((3, 4, 5))
        self.assertEqual((rec.duration_s, rec.part_starts_ms()), (12.0, [0, 3000, 7000]))
        self.assertEqual([rec.locate(x) for x in (0, 2999, 3000, 6999, 7000, 11999, 99999)],
                         [(0, 0), (0, 2999), (1, 0), (1, 3999), (2, 0), (2, 4999), (2, 4999)])
        pl = make_player(rec); pl.open(rec)
        self.assertEqual(pl.length_ms, 12_000)
        pl.seek_ms(8500)
        self.assertEqual((pl.part_idx, pl.active.engines[0].path, pl.active.engines[0].time_ms, pl.time_ms), (2, "p2_0.mp4", 1500, 8500))
        pl.seek_ms(1000)
        self.assertEqual((pl.part_idx, pl.time_ms), (0, 1000))
        self.assertEqual(pl.seek_fraction(0.5), 6000)
        self.assertEqual(pl.part_idx, 1)

    def test_next_part_preloaded_at_least_2s_before_boundary_then_swapped(self):
        rec = make_rec((10, 4))
        t = [0.0]
        pl = make_player(rec, clock=lambda: t[0]); pl.open(rec)
        events = []
        pl.on_part_change = events.append
        run(pl, 6000)
        self.assertIsNone(pl.standby)                         # sisa 4 dtk: belum perlu
        run(pl, 1100)                                         # sisa <=3 dtk -> dimuat
        self.assertIsNotNone(pl.standby)
        self.assertIn(pl.standby.state, ("loading", "ready"))
        self.assertEqual(pl.standby.engines[0].path, "p1_0.mcap".replace("mcap", "mp4"))
        self.assertGreaterEqual(10_000 - pl.active.engines[0].time_ms, 2000)       # >= 2 dtk sebelum batas
        self.assertEqual(pl.standby.state, "ready")
        self.assertTrue(all(e.muted for e in pl.standby.engines))
        run(pl, 3000)
        self.assertEqual(pl.part_idx, 1)
        self.assertEqual(events, [1])
        self.assertTrue(pl.is_playing)
        self.assertEqual(len(pl.gaps_ms), 1)
        self.assertLessEqual(pl.gaps_ms[0], 500)
        self.assertEqual((pl.active.engines[0].muted, pl.active.engines[1].muted), (False, True))   # audio dari master pulih
        self.assertAlmostEqual(pl.time_ms, 10_000, delta=400)
        run(pl, 4200)
        self.assertEqual(pl.active.engines[0].state, "Ended")
        self.assertEqual(pl.part_idx, 1)                      # bagian terakhir: tidak ada sambungan lagi
        self.assertEqual(pl.engines[0].path, "p1_0.mp4")

    def test_speed_and_pause_carry_over_the_boundary(self):
        rec = make_rec((5, 5)); pl = make_player(rec); pl.open(rec)
        pl.set_rate(2.0); pl.toggle_mute()
        run(pl, 4000)
        self.assertEqual(pl.part_idx, 1)
        self.assertEqual(pl.active.engines[0].rate, 2.0)
        self.assertTrue(pl.active.engines[0].muted)           # bisu pilihan pengguna ikut

    def test_cold_swap_when_no_standby_was_ready(self):
        rec = make_rec((3, 3)); pl = make_player(rec); pl.open(rec)
        pl.active.engines[0].t = 2990
        pl.active.engines[1].t = 2990
        pl.preload_s = 0                                      # pastikan tidak ada preload
        pl.seek_ms(2999)
        run(pl, 300)
        self.assertEqual(pl.part_idx, 1)

    def test_single_part_recording_behaves_like_simple_player(self):
        rec = make_rec((3,)); pl = make_player(rec); pl.open(rec)
        run(pl, 3500)
        self.assertEqual((pl.part_idx, pl.active.engines[0].state), (0, "Ended"))
        self.assertEqual(pl.gaps_ms, [])


class PlaylistTests(unittest.TestCase):
    def recs(self, n=4): return [Recording(f"r{i}", [Part({"/c/0": f"{i}.mp4"}, 10.0 + i)]) for i in range(n)]

    def test_entry_is_recording_not_file(self):
        pl = Playlist()
        rec = make_rec((3, 4)); pl.add(rec)
        it = pl.items[0]
        self.assertEqual((it.part_count, it.camera_count, it.duration_s), (2, 2, 7.0))
        self.assertIs(pl.items[0], rec)

    def test_add_remove_reorder_keep_current(self):
        pl = Playlist(); [pl.add(r) for r in self.recs()]
        pl.select(2); cur = pl.current
        pl.move(0, 3); self.assertIs(pl.current, cur); self.assertEqual([r.title for r in pl.items], ["r1", "r2", "r3", "r0"])
        pl.remove(0); self.assertIs(pl.current, cur); self.assertEqual(pl.index, 0)
        pl.remove(pl.index); self.assertIsNone(pl.current)

    def test_auto_next_modes(self):
        pl = Playlist(random.Random(1)); [pl.add(r) for r in self.recs(3)]
        pl.select(0)
        self.assertEqual([pl.next().title, pl.next().title, pl.next()], ["r1", "r2", None])   # berurutan berhenti di akhir
        pl.set_mode("repeat"); pl.select(2); self.assertEqual(pl.next().title, "r0")
        pl.set_mode("shuffle"); seen = set()
        for _ in range(30):
            prev = pl.index; n = pl.next(); self.assertNotEqual(n.title, f"r{prev}"); seen.add(n.title)
        self.assertEqual(seen, {"r0", "r1", "r2"})
        with self.assertRaises(ValueError):
            pl.set_mode("acak")
        pl.set_mode("sequential"); pl.select(2); self.assertEqual(pl.prev().title, "r1")

    def test_save_and_restore(self):
        d = Path(tempfile.mkdtemp())
        try:
            pl = Playlist(); pl.add(make_rec((3, 4))); [pl.add(r) for r in self.recs(2)]
            pl.select(1); pl.set_mode("repeat")
            f = d / "x.m2playlist"
            pl.save(f, position_s=12.5, speed=1.5)
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual((data["version"], data["play_mode"], data["state"]), (1, "repeat", {"index": 1, "position_s": 12.5, "speed": 1.5}))
            self.assertEqual(data["items"][0]["kind"], "session")
            self.assertEqual(len(data["items"][0]["parts"]), 2)
            pl2 = Playlist.load(f)
            self.assertEqual((pl2.index, pl2.play_mode, pl2.state["speed"], [r.title for r in pl2.items]), (1, "repeat", 1.5, ["sesi", "r0", "r1"]))
            self.assertEqual(pl2.items[0].parts[1].cameras, pl.items[0].parts[1].cameras)
            self.assertEqual(pl2.items[0].duration_s, 7.0)
            self.assertFalse(list(d.glob("*.tmp")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FileManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.paths, cls.truth = ES.write_ego_session(cls.d / "src", parts=2)
        s = settings.load()
        cls.per = cls.d / "per_file"
        for i, p in enumerate(cls.paths):
            worker.convert_file({"src": str(p), "rel": p.name, "out_dir": str(cls.per), "settings": s,
                                 "session": {"id": "ego_0014~ego_0015", "index": i + 1, "count": 2}})
        cls.merged = cls.d / "merged"
        ses = session.detect([session.probe(p) for p in cls.paths])[0]
        session.merge(ses, s, cls.merged)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_converter_mp4_recognized_via_sidecar_and_paired_left_right(self):
        recs = scan_folder(self.merged, detect_mcap=False)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r.kind, r.part_count, r.camera_count, r.has_audio), ("session", 1, 2, True))
        self.assertEqual(r.cameras, ["/ego/camera/0", "/ego/camera/1"])           # kiri = ...0, kanan = ...1
        self.assertTrue(r.parts[0].paths[0].endswith("camera_0.mp4") and r.parts[0].paths[1].endswith("camera_1.mp4"))
        self.assertAlmostEqual(r.duration_s, 6.0, delta=0.05)
        self.assertTrue(r.playable)

    def test_per_file_session_becomes_one_virtual_recording(self):
        recs = scan_folder(self.per, detect_mcap=False)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r.title, r.kind, r.part_count, r.camera_count), ("ego_0014~ego_0015", "session", 2, 2))
        self.assertAlmostEqual(r.duration_s, 6.0, delta=0.05)
        self.assertEqual(r.parts[0].paths[0].split("/")[-1], "ego_0014_ego_camera_0.mp4")
        self.assertEqual(r.parts[1].paths[1].split("/")[-1], "ego_0015_ego_camera_1.mp4")

    def test_plain_mp4_plays_as_single_camera(self):
        d = self.d / "plain"; d.mkdir()
        src = next(self.merged.glob("*camera_0.mp4"))
        shutil.copy(src, d / "lain.mp4")
        recs = scan_folder(d, detect_mcap=False)
        self.assertEqual([(r.title, r.camera_count, r.part_count, r.kind) for r in recs], [("lain", 1, 1, "single")])

    def test_unconverted_mcap_listed_with_badge_and_session_cache(self):
        calls = []
        orig = session.probe
        session.probe = lambda p: (calls.append(p), orig(p))[1]
        cache = {}
        try:
            recs = scan_folder(self.d / "src", cache)
            self.assertEqual(len(recs), 1)
            self.assertEqual((recs[0].converted, recs[0].playable, recs[0].kind, recs[0].title, recs[0].source_files),
                             (False, False, "mcap", "ego_0014~ego_0015", ["ego_0014.mcap", "ego_0015.mcap"]))
            n = len(calls)
            scan_folder(self.d / "src", cache)
            self.assertEqual(len(calls), n)                                       # cache (path,ukuran,mtime)
        finally:
            session.probe = orig

    def test_two_camera_mp4s_without_sidecar_are_paired_by_name(self):
        d = self.d / "nosc"; d.mkdir()
        for f in self.merged.glob("*.mp4"):
            shutil.copy(f, d / f.name)                                              # tanpa .metadata.json
        recs = scan_folder(d, detect_mcap=False)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r.title, r.camera_count, r.part_count, r.has_audio), ("ego_0014~ego_0015_ego_camera", 2, 1, True))
        self.assertAlmostEqual(r.duration_s, 6.0, delta=0.05)                        # durasi dibaca dari moov
        self.assertTrue(r.parts[0].paths[0].endswith("camera_0.mp4") and r.parts[0].paths[1].endswith("camera_1.mp4"))
        from mcap2mp4.player.library import recording_for_path, recording_for_files
        one = recording_for_path(d / "ego_0014~ego_0015_ego_camera_1.mp4")           # membuka SATU berkas tetap memuat kedua kamera
        self.assertEqual(one.camera_count, 2)
        picked = recording_for_files([d / "ego_0014~ego_0015_ego_camera_1.mp4", d / "ego_0014~ego_0015_ego_camera_0.mp4"])
        self.assertEqual([Path(p).name[-12:] for p in picked.parts[0].paths], ["camera_1.mp4", "camera_0.mp4"])        # kiri-kanan = urutan pilihan pengguna

    def test_embedded_pairing_marker_survives_lost_sidecar_and_renames(self):
        from mcap2mp4 import verify
        s = settings.load(); s["metadata"] = dict(s["metadata"], embed_pairing=True)
        out = self.d / "marked"
        ses = session.detect([session.probe(p) for p in self.paths])[0]
        r = session.merge(ses, s, out)
        self.assertEqual(r["status"], "success", r)
        files = sorted(out.glob("*.mp4"))
        mk = [verify.read_pairing(f) for f in files]
        self.assertEqual([m["index"] for m in mk], [0, 1])
        self.assertEqual(mk[0]["group"], mk[1]["group"])
        self.assertEqual([m["topic"] for m in mk], ["/ego/camera/0", "/ego/camera/1"])
        self.assertEqual(mk[0]["session"]["count"], 2)
        for j in out.glob("*.json"):
            j.unlink()                                                              # sidecar hilang
        files[0].rename(out / "zzz_kiri.mp4")                                       # nama tak berpola, urutan nama TERBALIK
        files[1].rename(out / "aaa_kanan.mp4")
        recs = scan_folder(out, detect_mcap=False)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual((rec.camera_count, rec.part_count), (2, 1))
        self.assertEqual([Path(p).name for p in rec.parts[0].paths], ["zzz_kiri.mp4", "aaa_kanan.mp4"])   # urut menurut indeks penanda
        self.assertAlmostEqual(rec.duration_s, 6.0, delta=0.05)
        for f in out.glob("*.mp4"):                                                  # MP4 tetap valid & dapat didekode
            self.assertTrue(verify.quick(f)["ok"])
            dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], capture_output=True, text=True)
            self.assertEqual((dec.returncode, dec.stderr.strip()), (0, ""))

    def test_pairing_marker_is_off_by_default_and_does_not_change_payload(self):
        from mcap2mp4 import verify
        s = settings.load()
        ses = session.detect([session.probe(p) for p in self.paths])[0]
        self.assertFalse(s["metadata"]["embed_pairing"])
        r0 = session.merge(ses, s, self.d / "pair_off")
        s["metadata"] = dict(s["metadata"], embed_pairing=True)
        r1 = session.merge(ses, s, self.d / "pair_on")
        a = Path(r0["outputs"][0]["path"]); b = Path(r1["outputs"][0]["path"])
        self.assertIsNone(verify.read_pairing(a))
        self.assertNotIn(b"m2gp", a.read_bytes())
        self.assertIsNotNone(verify.read_pairing(b))
        self.assertEqual(r0["outputs"][0]["sha256"], r1["outputs"][0]["sha256"])      # payload video identik
        self.assertEqual(verify.audio_payload_sha256(a), verify.audio_payload_sha256(b))

    def test_pairing_rules_do_not_pair_unrelated_files(self):
        d = self.d / "unrel"; d.mkdir()
        src = next(self.merged.glob("*camera_0.mp4"))
        for n in ("ego_0014.mp4", "ego_0015.mp4", "video_1.mp4", "video_2.mp4", "solo_cam_0.mp4"):
            shutil.copy(src, d / n)
        recs = scan_folder(d, detect_mcap=False)
        self.assertEqual(sorted((r.title, r.camera_count) for r in recs),
                         [("ego_0014", 1), ("ego_0015", 1), ("solo_cam_0", 1), ("video_1", 1), ("video_2", 1)])

    def test_converted_sources_are_not_listed_again_as_mcap(self):
        d = self.d / "mix"; shutil.copytree(self.per, d); shutil.copy(self.paths[0], d / "ego_0014.mcap")
        recs = scan_folder(d)
        self.assertEqual([r.converted for r in recs], [True])


def vlc_ok():
    try:
        E.load_vlc(); return True
    except E.PlayerUnavailable:
        return False


@unittest.skipUnless(vlc_ok(), "libVLC tidak tersedia")
class RealTwoCameraTests(unittest.TestCase):
    """libVLC nyata (headless/vmem): dua kamera tersinkron, sesi virtual lintas bagian."""
    @classmethod
    def setUpClass(cls):
        FileManagerTests.setUpClass.__func__(FileManagerTests)
        cls.rec_virtual = scan_folder(FileManagerTests.per, detect_mcap=False)[0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(FileManagerTests.d, ignore_errors=True)

    def factory(self):
        e = E.Engine(E.EngineOptions(headless=True))
        return e

    def pump(self, pl, until, timeout=20):
        end = time.time() + timeout
        while time.time() < end and not until():
            pl.tick(); time.sleep(0.05)
        return until()

    def test_two_cameras_sync_and_commands_apply_to_both(self):
        pl = RecordingPlayer(self.factory)
        try:
            pl.open(Recording("x", [self.rec_virtual.parts[0]], "single"))
            self.assertTrue(self.pump(pl, lambda: all(e.time_ms > 300 for e in pl.engines)))
            self.assertEqual(len(pl.engines), 2)
            pl.set_rate(1.5); pl.pause(); time.sleep(0.3); pl.seek_ms(1000)
            self.pump(pl, lambda: False, timeout=0.8)
            for e in pl.engines:
                self.assertEqual(e.state, "Paused")
                self.assertAlmostEqual(e.rate, 1.5, places=1)
            ts = [e.time_ms for e in pl.engines]
            self.assertLessEqual(abs(ts[0] - ts[1]), 70)                          # ~2 frame (33 ms) + ambang
            pl.resume()
            self.pump(pl, lambda: False, timeout=1.2)
            stats = pl.drift_stats()
            print("\n[AC-26 sandbox] drift antar-kamera:", stats, "koreksi:", pl.active.ctrl.corrections)
            self.assertLess(stats["mean_ms"], 40)
            self.assertLessEqual(pl.active.ctrl.corrections["rate"] + pl.active.ctrl.corrections["seek"], 2)   # bukan mengejar derau
        finally:
            pl.close()

    def test_virtual_session_crosses_boundary_within_500ms(self):
        pl = RecordingPlayer(self.factory, preload_s=2.0)
        try:
            pl.open(self.rec_virtual, autoplay=True)
            pl.set_rate(2.0)
            self.assertTrue(self.pump(pl, lambda: pl.part_idx == 1 and pl.is_playing, timeout=25))
            self.pump(pl, lambda: False, timeout=0.5)
            print("\n[AC-29 sandbox] jeda sambungan (ms):", [round(g) for g in pl.gaps_ms])
            self.assertEqual(len(pl.gaps_ms), 1)
            self.assertLessEqual(pl.gaps_ms[0], 500)
            self.assertGreater(pl.time_ms, 2900)
            pl.seek_ms(500)                                                         # acak mundur -> bagian 1 dimuat tepat
            self.assertEqual(pl.part_idx, 0)
        finally:
            pl.close()


if __name__ == "__main__":
    unittest.main()
