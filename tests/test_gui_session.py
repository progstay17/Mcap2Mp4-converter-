"""GUI: pilihan Audio & Sesi di tab Konversi (FR-106), dari klik Mulai sampai MP4 gabungan."""
import os, shutil, sys, tempfile, time, unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6 import QtWidgets
    HAVE_QT = True
except Exception:
    HAVE_QT = False
import ego_synth as E


@unittest.skipUnless(HAVE_QT, "PySide6 tidak tersedia")
class GuiSessionTests(unittest.TestCase):
    def setUp(self):
        from mcap2mp4 import gui
        self.gui = gui
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        E.write_ego_session(self.d / "src", parts=2)
        self.app = gui.create_app([])
        self.win = gui.MainWindow(cfg_path=self.d / "cfg" / "settings.json")
        self.addCleanup(self.win.close)

    def run_conversion(self, mode, audio="all"):
        w = self.win
        w.show()
        w.ed_src.setText(str(self.d / "src"))
        w.ed_out.setText(str(self.d / f"out_{mode}_{audio}"))
        w.cmb_session.setCurrentIndex(w.cmb_session.findData(mode))
        w.cmb_audio.setCurrentIndex(w.cmb_audio.findData(audio))
        w.bt_start.click()
        t0 = time.time()
        while (w.state != "idle" or w.last_summary is None) and time.time() - t0 < 120:
            self.app.processEvents(); time.sleep(0.02)
        return w.last_summary, self.d / f"out_{mode}_{audio}"

    def test_merged_mode_from_gui_makes_one_mp4_per_camera(self):
        summ, out = self.run_conversion("merged")
        self.assertEqual(summ["counts"]["success"], 1)
        self.assertEqual(sorted(p.name for p in out.glob("*.mp4")),
                         ["ego_0014~ego_0015_ego_camera_0.mp4", "ego_0014~ego_0015_ego_camera_1.mp4"])

    def test_per_file_no_audio_from_gui(self):
        from mcap2mp4 import verify
        summ, out = self.run_conversion("per_file", "none")
        self.assertEqual(summ["counts"]["success"], 2)
        mp4s = sorted(out.glob("*.mp4"))
        self.assertEqual(len(mp4s), 4)
        self.assertTrue(all(verify.parse_mp4(p)["audio"] == [] for p in mp4s))

    def test_choices_persist_and_are_translated(self):
        w = self.win
        w.cmb_session.setCurrentIndex(w.cmb_session.findData("merged"))
        w.cmb_audio.setCurrentIndex(w.cmb_audio.findData("first"))
        s = w.collect_settings()
        self.assertEqual((s["session_mode"], s["audio_in"]), ("merged", "first"))
        w.apply_settings(dict(s, session_mode="per_file", audio_in="none"))
        self.assertEqual((w.cmb_session.currentData(), w.cmb_audio.currentData()), ("per_file", "none"))
        from mcap2mp4 import i18n
        for lang in ("id", "en", "zh"):
            i18n.set_language(lang); w.retranslate()
            txt = w.lb_audio.text() + w.lb_session.text() + "".join(w.cmb_session.itemText(i) + w.cmb_audio.itemText(i) for i in range(2))
            self.assertNotIn("gui.", txt)
        i18n.set_language("id")


if __name__ == "__main__":
    unittest.main()
