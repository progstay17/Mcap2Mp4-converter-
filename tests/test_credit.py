"""Kredit 'Fadhil Ghifarion 法迪 for AIT' (v0.3.2): modul, CLI (aman di konsol non-UTF-8), GUI, installer, sumber daya versi exe."""
import os, subprocess, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import mcap2mp4

CREDIT = "Fadhil Ghifarion 法迪 for AIT"


class CreditTests(unittest.TestCase):
    def test_constant_and_version(self):
        self.assertEqual(mcap2mp4.CREDIT, CREDIT)
        self.assertEqual(mcap2mp4.__author__, CREDIT)
        self.assertEqual(mcap2mp4.__version__, "0.3.2")

    def test_cli_version_prints_credit_even_on_legacy_console_encoding(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="cp1252")      # tanpa UTF-8: tidak boleh crash
        r = subprocess.run([sys.executable, "-m", "mcap2mp4.cli", "--version"], capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8", "replace")
        self.assertIn("mcap2mp4 0.3.2", out)
        self.assertIn("Fadhil Ghifarion", out)
        self.assertIn("for AIT", out)

    def test_installer_and_exe_version_resource_carry_credit(self):
        nsi = (ROOT / "build" / "installer.nsi").read_text(encoding="utf-8-sig")
        self.assertIn(f'!define CREDIT "{CREDIT}"', nsi)
        for key in ('"Publisher" "${CREDIT}"', 'VIAddVersionKey "CompanyName" "${CREDIT}"', 'VIAddVersionKey "LegalCopyright" "${CREDIT}"',
                    "BrandingText"):
            self.assertIn(key, nsi)
        spec = (ROOT / "build" / "mcap2mp4.spec").read_text(encoding="utf-8")
        self.assertIn("CompanyName", spec)
        self.assertEqual(spec.count("version=str(_vf)"), 2)                                    # GUI dan CLI

    def test_gui_about_and_footer(self):
        try:
            from PySide6 import QtWidgets  # noqa: F401
        except Exception:
            self.skipTest("PySide6 tidak tersedia")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import tempfile
        from mcap2mp4 import gui, i18n
        app = gui.create_app([])
        w = gui.MainWindow(cfg_path=Path(tempfile.mkdtemp()) / "s.json")
        self.addCleanup(w.close)
        self.assertEqual(w.lb_credit.text(), CREDIT)
        for lang in ("id", "en", "zh"):
            i18n.set_language(lang); w.retranslate()
            self.assertNotIn("gui.", w.bt_about.text())
            self.assertIn("{version}", i18n.t("gui.about"))                                    # kunci judul jendela tidak tertimpa
            self.assertIn("0.3.2", w.windowTitle())
            html = gui.about_html()
            self.assertIn(CREDIT, html); self.assertIn("0.3.2", html); self.assertIn("LGPL", html)
        i18n.set_language("id")


if __name__ == "__main__":
    unittest.main()
