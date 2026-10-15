"""Tes pemutar M7 (FR-120..123, 130..133). Logika diuji dengan engine palsu; libVLC nyata diuji headless bila tersedia."""
import json, os, shutil, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcap2mp4 import i18n, settings, worker
from mcap2mp4.player import controller as C, engine as E
from fake_engine import FakeEngine
from mcap2mp4.player.model import Part, Recording
import ego_synth as ES


class PureLogicTests(unittest.TestCase):
    def test_speed_steps(self):
        self.assertEqual(E.SPEEDS, (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0))
        self.assertEqual(E.next_speed(1.0, +1), 1.25)
        self.assertEqual(E.next_speed(4.0, +1), 4.0)
        self.assertEqual(E.next_speed(0.25, -1), 0.25)
        self.assertEqual(E.next_speed(1.1, +1), 1.25)         # nilai kustom -> baku terdekat searah
        self.assertEqual(E.next_speed(1.1, -1), 1.0)
        self.assertEqual((E.clamp_rate(0.01), E.clamp_rate(99)), (0.25, 8.0))

    def test_format_time(self):
        self.assertEqual(E.format_time(65_000), "01:05")
        self.assertEqual(E.format_time(3_725_400, True), "1:02:05.400")
        self.assertEqual(E.format_time(None), "00:00")

    def test_args_and_vout_fallback_order(self):
        a = E.build_args("off", 6)
        self.assertIn("--avcodec-hw=none", a)
        self.assertIn("--avcodec-threads=6", a)
        self.assertIn("--avcodec-hw=any", E.build_args("auto"))
        self.assertEqual(E.vout_candidates("win32"), ["direct3d11", "directdraw", "wingdi", None])
        self.assertEqual(E.vout_candidates("linux"), [None])
        self.assertEqual(E.vout_candidates("win32", "gdi"), ["gdi"])
        self.assertIn("--vout=dummy", E.build_args(headless=True))

    def test_every_shortcut_has_a_command_and_i18n_help(self):
        self.assertEqual(set(C.SHORTCUTS), {"Space", "Left", "Right", "Shift+Left", "Shift+Right", "[", "]", ".", "M", "F", "Ctrl+O", "Ctrl+S"})
        for lang in ("id", "en", "zh"):
            self.assertIn("player.help", json.load(open(Path(__file__).resolve().parents[1] / "i18n" / f"{lang}.json", encoding="utf-8")))

    def test_player_i18n_keys_complete_in_all_languages(self):
        root = Path(__file__).resolve().parents[1] / "i18n"
        d = {l: json.load(open(root / f"{l}.json", encoding="utf-8")) for l in ("id", "en", "zh")}
        keys = {k for k in d["id"] if k.startswith("player.") or k == "gui.tab.player"}
        self.assertGreater(len(keys), 20)
        for l in ("en", "zh"):
            self.assertEqual(keys - set(d[l]), set(), l)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.e = FakeEngine(60_000)
        self.c = Controller = C.Controller([self.e])
        self.c.open(["a.mp4"])

    def test_open_plays(self):
        self.assertTrue(self.e.playing)
        self.assertEqual(self.e.path, "a.mp4")

    def test_toggle_and_replay_after_end(self):
        self.c.toggle_play(); self.assertFalse(self.e.playing)
        self.c.toggle_play(); self.assertTrue(self.e.playing)
        self.e.advance(70_000); self.assertEqual(self.e.state, "Ended")
        self.c.toggle_play(); self.assertTrue(self.e.playing); self.assertEqual(self.e.time_ms, 0)

    def test_relative_seek_is_clamped(self):
        self.e.t = 3000
        self.assertEqual(self.c.seek_rel(-5), 0)
        self.assertEqual(self.c.seek_rel(30), 30_000)
        self.assertEqual(self.c.seek_rel(100), 59_999)
        self.assertEqual(self.c.seek_fraction(0.5), 30_000)

    def test_speed_applies_and_auto_mutes_above_2x(self):
        self.c.set_rate(1.5); self.assertFalse(self.e.muted)
        self.c.set_rate(3.0); self.assertTrue(self.e.muted); self.assertEqual(self.e.rate, 3.0)
        self.c.set_rate(2.0); self.assertFalse(self.e.muted)
        c2 = C.Controller([FakeEngine()], auto_mute=False)
        c2.set_rate(4.0); self.assertFalse(c2.master.muted)

    def test_user_mute_survives_speed_change(self):
        self.c.toggle_mute(); self.assertTrue(self.e.muted)
        self.c.set_rate(3.0); self.c.set_rate(1.0)
        self.assertTrue(self.e.muted)
        self.c.toggle_mute(); self.assertFalse(self.e.muted)

    def test_faster_slower_keys(self):
        self.assertEqual([self.c.faster(), self.c.faster(), self.c.slower()], [1.25, 1.5, 1.25])

    def test_step_frame_pauses_then_steps(self):
        self.c.step_frame()
        self.assertFalse(self.e.playing)
        self.assertEqual([x[0] for x in self.e.calls][-2:], ["pause", "step"])

    def test_snapshot_naming_and_failure(self):
        d = Path(tempfile.mkdtemp())
        try:
            self.e.t = 1234
            out = self.c.snapshot(d, "rec")
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].name.startswith("rec_cam0_") and out[0].name.endswith("_1234ms.png"))
            self.e.snap_ok = False
            self.assertEqual(self.c.snapshot(d, "rec"), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_two_engines_get_same_commands_but_only_master_has_audio(self):
        a, b = FakeEngine(), FakeEngine()
        c = C.Controller([a, b]); c.open(["l.mp4", "r.mp4"])
        c.set_rate(0.5); c.seek_ms(4000); c.pause()
        for e in (a, b):
            self.assertEqual((e.rate, e.time_ms, e.playing), (0.5, 4000, False))
        self.assertEqual((a.muted, b.muted), (False, True))

    def test_status(self):
        self.e.lost = 3
        st = self.c.status()
        self.assertEqual((st["lost"], st["length_ms"], st["rate"]), (3, 60_000, 1.0))


def _vlc_ok():
    try:
        E.load_vlc()
        return True
    except E.PlayerUnavailable:
        return False


@unittest.skipUnless(_vlc_ok(), "libVLC tidak tersedia")
class RealLibVlcTests(unittest.TestCase):
    """libVLC nyata, headless (vout/aout dummy) memutar MP4 tulisan converter sendiri (risiko R-A1/R-P4)."""
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        paths, _ = ES.write_ego_session(cls.d / "s", parts=1)
        s = settings.load()
        cls.mp4 = worker.convert_file({"src": str(paths[0]), "rel": "a", "out_dir": str(cls.d / "o"), "settings": s})["outputs"][0]["path"]
        s["audio_in"] = "none"
        cls.mute_mp4 = worker.convert_file({"src": str(paths[0]), "rel": "a", "out_dir": str(cls.d / "o2"), "settings": s})["outputs"][0]["path"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def start(self, path):
        e = E.Engine(E.EngineOptions(headless=True))
        c = C.Controller([e])
        c.open([path])
        end = time.time() + 10
        while time.time() < end and not (e.state == "Playing" and e.time_ms > 100):
            time.sleep(0.05)
        self.addCleanup(e.release)
        return e, c

    def test_plays_mp4_with_audio_and_reports_info(self):
        e, c = self.start(self.mp4)
        self.assertEqual(e.state, "Playing")
        self.assertAlmostEqual(e.length_ms, 3000, delta=150)
        self.assertEqual(e.audio_tracks, 1)
        self.assertEqual(tuple(e.video_size), (320, 240))

    def test_mp4_without_audio_plays_silently(self):
        e, c = self.start(self.mute_mp4)                     # FR-132
        self.assertEqual(e.state, "Playing")
        self.assertEqual(e.audio_tracks, 0)

    def test_speed_seek_pause_and_step(self):
        e, c = self.start(self.mp4)
        self.assertEqual(c.set_rate(2.0), 2.0)
        time.sleep(0.3)
        self.assertAlmostEqual(e.rate, 2.0, places=1)
        c.pause(); time.sleep(0.3)
        self.assertEqual(e.state, "Paused")
        c.seek_ms(1500); time.sleep(0.5)
        self.assertLessEqual(abs(e.time_ms - 1500), 1100)    # GOP 1 dtk: VLC mendarat di sekitar target
        before = e.time_ms
        c.step_frame(); time.sleep(0.3)
        self.assertEqual(e.state, "Paused")
        self.assertGreaterEqual(e.time_ms, before)

    def test_plays_to_end(self):
        e, c = self.start(self.mp4)
        c.set_rate(4.0)
        end = time.time() + 8
        while time.time() < end and e.state not in ("Ended", "Stopped"):
            time.sleep(0.1)
        self.assertIn(e.state, ("Ended", "Stopped"))


@unittest.skipUnless(_vlc_ok() and os.environ.get("QT_QPA_PLATFORM") or False, "libVLC/Qt offscreen tidak tersedia")
class RealTabTwoCameraTests(unittest.TestCase):
    """Jalur persis yang dipakai pengguna: tab Pemutar + libVLC nyata + dua MP4 TANPA sidecar -> kedua tampilan menggambar frame."""
    def test_both_views_render_frames(self):
        from PySide6 import QtWidgets
        from mcap2mp4.player.widgets import PlayerTab
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        paths, _ = ES.write_ego_session(d / "s", parts=1)
        out = worker.convert_file({"src": str(paths[0]), "rel": "a", "out_dir": str(d / "o"), "settings": settings.load()})
        for j in (d / "o").glob("*.json"):
            j.unlink()
        tab = PlayerTab({}, state_dir=d / "st"); tab.resize(900, 500); tab.show()
        self.addCleanup(tab.shutdown)
        tab.load_folder(d / "o")
        self.assertEqual(tab.lst.count(), 1)                                   # satu rekaman, bukan dua
        self.assertTrue(tab.play_recording(tab.lst.item(0).data(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.UserRole)))
        end = time.time() + 6
        while time.time() < end and not all(v._img is not None for v in tab.views):
            app.processEvents(); time.sleep(0.02)
        self.assertEqual(len(tab.rp.engines), 2)
        self.assertTrue(all(v.isVisible() for v in tab.views))
        for v in tab.views:
            self.assertIsNotNone(v._img)
            self.assertGreater(len({v._img.pixel(x, y) for x in range(0, v._img.width(), 20) for y in range(0, v._img.height(), 20)}), 3)  # bukan layar hitam
        self.assertNotIn("player.err", tab.lb_status.text())


try:
    from PySide6 import QtCore, QtWidgets
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 tidak tersedia")
class PlayerTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from mcap2mp4.player.widgets import PlayerTab
        cls.PlayerTab = PlayerTab
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.mp4 = self.d / "x.mp4"
        self.mp4.write_bytes(b"\0")
        self.engs = []

    def make(self, **s):
        def factory():
            e = FakeEngine(30_000)
            self.engs.append(e)
            return e
        tab = self.PlayerTab(dict(s), engine_factory=factory, state_dir=self.d / "state")
        self.addCleanup(tab.shutdown)
        return tab

    def test_controls_disabled_until_file_opened_then_work(self):
        tab = self.make()
        self.assertFalse(tab.bt_play.isEnabled())
        self.assertTrue(tab.open_file(self.mp4))
        eng = self.engs[0]
        self.assertTrue(tab.bt_play.isEnabled())
        self.assertTrue(eng.playing)
        tab.bt_play.click(); self.assertFalse(eng.playing)
        eng.t = 10_000
        tab.bt_f5.click(); self.assertEqual(eng.t, 15_000)
        tab.bt_b30.click(); self.assertEqual(eng.t, 0)
        tab._do("faster"); self.assertEqual(eng.rate, 1.25)
        tab.cmb_speed.setEditText("3x"); tab._speed_typed(); self.assertEqual(eng.rate, 3.0)
        self.assertTrue(eng.muted)                                      # auto bisu >2x
        tab._tick()
        self.assertIn("3", tab.lb_status.text())

    def test_remembers_volume_and_speed_for_next_session(self):
        tab = self.make(player_volume=35, player_speed=1.5)
        self.assertEqual(tab.sl_vol.value(), 35)
        tab.open_file(self.mp4)
        self.assertEqual(self.engs[0].rate, 1.5)
        tab.sl_vol.setValue(60)
        out = tab.collect({})
        self.assertEqual((out["player_volume"], out["player_speed"]), (60, 1.5))
        self.assertEqual(len(out["player_splitter"]), 2)

    def test_seekbar_jump_and_texts_in_three_languages(self):
        tab = self.make()
        tab.open_file(self.mp4)
        tab.seek.setValue(5000); tab._seek_released()
        self.assertEqual(self.engs[0].t, 15_000)
        for lang in ("id", "en", "zh"):
            i18n.set_language(lang)
            tab.retranslate()
            texts = tab.bt_open.text() + tab.lb_speed.text() + tab.bt_snap.text() + tab.left.tabText(0) + tab.left.tabText(1) + tab.cmb_mode.itemText(2)
            self.assertNotIn("player.", texts)
        i18n.set_language("id")

    def test_missing_libvlc_shows_message_and_does_not_crash(self):
        def boom():
            raise E.PlayerUnavailable("x")
        tab = self.PlayerTab({}, engine_factory=boom)
        self.addCleanup(tab.shutdown)
        self.assertFalse(tab.open_file(self.mp4))
        self.assertIn("libVLC", tab.lb_hint.text())

    def test_fullscreen_toggle(self):
        tab = self.make()
        tab.open_file(self.mp4)
        tab.toggle_fullscreen(); self.assertIsNotNone(tab._fs_host)
        tab.toggle_fullscreen(); self.assertIsNone(tab._fs_host)
        self.app.processEvents()

    def test_two_cameras_left_right_swap_and_single_view(self):
        rec = Recording("dua", [Part({"/c/0": str(self.mp4), "/c/1": str(self.mp4)}, 30.0)], "single")
        tab = self.make()
        tab.show()
        self.assertTrue(tab.open_recording(rec))
        self.assertEqual(len(self.engs), 2)
        self.assertTrue(tab.views[0].isVisible() and tab.views[1].isVisible())
        self.assertIs(tab.views[0].engine, self.engs[0])
        tab.swap_cameras()
        self.assertIs(tab.views[0].engine, self.engs[1])
        tab.cycle_view_mode(); self.assertFalse(tab.views[1].isVisible())
        tab.cycle_view_mode(); self.assertFalse(tab.views[0].isVisible()); self.assertTrue(tab.views[1].isVisible())
        tab.cycle_view_mode(); self.assertTrue(tab.views[0].isVisible() and tab.views[1].isVisible())
        self.assertEqual((self.engs[0].muted, self.engs[1].muted), (False, True))             # audio hanya master

    def test_playlist_autoplay_and_persist_across_restart(self):
        from mcap2mp4.player.model import Part as P
        recs = [Recording(f"r{i}", [P({"/c/0": str(self.mp4)}, 2.0)]) for i in range(2)]
        tab = self.make()
        for r in recs:
            tab.playlist_add(r)
        tab.play_playlist_row(0)
        self.assertEqual(tab.playlist.index, 0)
        self.engs[0].advance(35_000)                                         # selesai -> lanjut otomatis ke entri 2
        tab._tick()
        self.assertEqual(tab.playlist.index, 1)
        self.assertEqual(tab.rec.title, "r1")
        tab.sl_vol.setValue(50)
        tab.shutdown()
        tab2 = self.make()                                                    # "restart": daftar terakhir dipulihkan
        self.assertEqual([r.title for r in tab2.playlist.items], ["r0", "r1"])
        self.assertEqual(tab2.playlist.index, 1)
        self.assertEqual(tab2.lst_pl.count(), 2)

    def test_playlist_drag_reorder_updates_model(self):
        from mcap2mp4.player.model import Part as P
        tab = self.make()
        for i in range(3):
            tab.playlist_add(Recording(f"r{i}", [P({"/c/0": str(self.mp4)}, 2.0)]))
        it = tab.lst_pl.takeItem(0)
        tab.lst_pl.addItem(it)
        tab._pl_reordered()
        self.assertEqual([r.title for r in tab.playlist.items], ["r1", "r2", "r0"])

    def test_open_two_picked_files_as_left_right(self):
        b = self.d / "y.mp4"; b.write_bytes(b"\0")
        tab = self.make()
        self.assertTrue(tab.open_files([self.mp4, b]))
        self.assertEqual(len(self.engs), 2)
        self.assertEqual((self.engs[0].path, self.engs[1].path), (str(self.mp4), str(b)))
        self.assertEqual(tab.rec.camera_count, 2)

    def test_camera_without_frames_is_reported_in_status_and_log(self):
        class Sink:
            serial = 0
            def latest(self): return None
        tab = self.make()
        now = [100.0]
        tab._clock = lambda: now[0]
        tab.open_file(self.mp4)
        for e in self.engs:
            e.sink = Sink()
        tab._tick(); self.assertNotIn("player.err", tab.lb_status.text())      # belum 3 dtk
        now[0] += 5
        tab._tick()
        self.assertIn("1", tab.lb_status.text()); self.assertNotIn("player.err.noframes", tab.lb_status.text())
        self.assertIn("player.log", tab.lb_status.text())
        log = (self.d / "state" / "player.log").read_text(encoding="utf-8")
        self.assertIn("tanpa frame", log); self.assertIn("buka", log)

    def test_convert_then_play_unconverted_session(self):
        import ego_synth as ES2
        src = self.d / "raw"; ES2.write_ego_session(src, parts=2)
        tab = self.make()
        tab.load_folder(src)
        self.assertEqual(tab.lst.count(), 1)
        rec = tab.lst.item(0).data(QtCore.Qt.UserRole)
        self.assertFalse(rec.converted)
        self.assertTrue(tab.play_recording(rec))                                 # konversi dimulai di thread latar
        self.assertFalse(tab.convert_and_play(rec))                              # tak bisa dua konversi sekaligus
        end = time.time() + 90
        while time.time() < end and (tab._conv is not None or tab.rec is None):
            self.app.processEvents(); tab._tick(); time.sleep(0.05)
        self.assertIsNotNone(tab.rec)
        self.assertEqual((tab.rec.title, tab.rec.camera_count, tab.rec.has_audio), ("ego_0014~ego_0015", 2, True))
        self.assertEqual(sorted(p.name for p in (src / "mp4").glob("*.mp4")),
                         ["ego_0014~ego_0015_ego_camera_0.mp4", "ego_0014~ego_0015_ego_camera_1.mp4"])
        self.assertEqual(len(self.engs), 2)                                      # dua kamera diputar
        self.assertTrue(self.engs[0].playing)
        items = [tab.lst.item(i).data(QtCore.Qt.UserRole) for i in range(tab.lst.count())]
        self.assertEqual([r.converted for r in items], [True])                   # tidak lagi tampil sebagai 'belum dikonversi'
        from mcap2mp4.player import autoconvert                                  # memutar lagi tidak mengonversi ulang
        f0 = next((src / "mp4").glob("*camera_0.mp4"))
        mtime = f0.stat().st_mtime_ns
        ok, again, msg = autoconvert.convert_recording(rec)
        self.assertEqual((ok, msg), (True, "sudah dikonversi"))
        self.assertEqual(f0.stat().st_mtime_ns, mtime)

    def test_convert_failure_is_reported_not_raised(self):
        from mcap2mp4.player import autoconvert
        bad = Recording("x", [], "mcap", ["tidak_ada.mcap"], converted=False, folder=str(self.d))
        ok, rec, msg = autoconvert.convert_recording(bad)
        self.assertEqual((ok, rec), (False, None))
        self.assertIn("tidak ditemukan", msg)

    def test_folder_browser_lists_recordings_and_history(self):
        tab = self.make()
        d = self.d / "lib"; d.mkdir(); shutil.copy(self.mp4, d / "satu.mp4")
        tab.load_folder(d)
        self.assertEqual(tab.lst.count(), 1)
        self.assertIn("satu", tab.lst.item(0).text())
        self.assertEqual(tab.s["player_folders"][0], str(d))
        tab.load_folder(self.d)
        self.assertEqual(tab.s["player_folders"][:2], [str(self.d), str(d)])


if __name__ == "__main__":
    unittest.main()
