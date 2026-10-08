"""Uji asap GUI headless (QT_QPA_PLATFORM=offscreen). Jalankan: python tests/gui_smoke.py [folder_keluaran_png]"""
import os, sys, shutil, tempfile, time, json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
import multiprocessing
from PySide6 import QtCore, QtWidgets
from mcap2mp4 import gui, i18n, batch


def pump(cond, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def main():
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    shots.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    from test_pipeline import mk
    src, out = work / "sumber", work / "hasil"
    (src / "sesi1").mkdir(parents=True); (src / "sesi2").mkdir()
    mk(src / "sesi1" / "a.mcap", "ros2", {"/h": 60}); mk(src / "sesi1" / "b.mcap", "foxglove", {"/c0": 40, "/c1": 40})
    mk(src / "sesi2" / "c.mcap", "foxglove", {"/c0": 30, "/c1": 29}); mk(src / "sesi2" / "d.mcap", "foxglove", {"/c": 10}, fmt="h264")
    (src / "sesi2" / "rusak.mcap").write_bytes(b"bukan mcap")

    app = gui.create_app([])
    win = gui.MainWindow(cfg_path=work / "cfg" / "settings.json")
    win.show()
    ok = lambda c, m: (print(("OK   " if c else "FAIL ") + m), None)[1] or c   # noqa: E731
    fails = 0

    win.ed_src.setText(str(src)); win.ed_out.setText(str(out))
    # 1. pindai
    win.bt_scan.click()
    fails += not ok(pump(lambda: win.state == "idle" and win.model.rowCount() == 5), "pindai: 5 baris")
    fails += not ok({r["status"] for r in win.model.rows} == {"pending", "failed"}, "pindai: status awal pending/failed(rusak)")
    # 2. mulai
    win.cmb_sync.setCurrentIndex(win.cmb_sync.findData("trim")); win.cmb_verify.setCurrentIndex(win.cmb_verify.findData("full"))
    win.bt_start.click()
    fails += not ok(win.state == "running", "mulai: status running")
    fails += not ok(pump(lambda: win.state == "idle" and win.last_summary is not None), "selesai")
    c = win.last_summary["counts"]
    fails += not ok((c["success"], c["failed"]) == (3, 2), f"hasil: 3 sukses 2 gagal ({c})")
    fails += not ok(len(list(out.rglob("*.mp4"))) == 5, "5 file MP4 tertulis")
    fails += not ok(len(list((out / "reports").glob("laporan_*"))) >= 2, "laporan otomatis tertulis")
    # 3. metadata viewer
    row = win.model.index_of["sesi1/a.mcap"]
    win.table.selectRow(row); QtWidgets.QApplication.processEvents()
    fails += not ok(win.meta.tree.topLevelItemCount() >= 4, f"metadata: {win.meta.tree.topLevelItemCount()} grup")
    win.meta.filter.setText("duration"); QtWidgets.QApplication.processEvents()
    vis = [win.meta.tree.topLevelItem(i) for i in range(win.meta.tree.topLevelItemCount()) if not win.meta.tree.topLevelItem(i).isHidden()]
    fails += not ok(0 < len(vis) < win.meta.tree.topLevelItemCount(), "metadata: filter menyembunyikan grup tanpa kecocokan")
    win.meta.filter.setText("")
    win.tabs.setCurrentIndex(0)
    win.grab().save(str(shots / "gui_id.png"))
    # 4. bahasa
    for code, word in (("en", "Start"), ("zh", "开始"), ("id", "Mulai")):
        win.cmb_lang.setCurrentIndex(win.cmb_lang.findData(code)); QtWidgets.QApplication.processEvents()
        fails += not ok(win.bt_start.text() == word, f"bahasa {code}: tombol = {win.bt_start.text()!r}")
        if code == "zh":
            win.grab().save(str(shots / "gui_zh.png"))
        if code == "en":
            win.grab().save(str(shots / "gui_en.png"))
            fails += not ok(any("frames" in r["detail"] or r["detail"] == "" or "Codec" in r["detail"] or "MCAP" in r["detail"] for r in win.model.rows), "bahasa en: detail baris ikut diterjemahkan")
    # 5. jalankan ulang -> semua dilewati (manifest)
    win.bt_start.click()
    fails += not ok(pump(lambda: win.state == "idle" and win.last_summary["counts"]["skipped"] == 3), "ulang: 3 dilewati via manifest")
    # 6. jeda -> batal
    shutil.rmtree(out)
    for i in range(6):
        mk(src / "sesi1" / f"x{i}.mcap", "ros2", {"/h": 90})
    win.sp_workers.setValue(1)
    win.bt_start.click()
    pump(lambda: any(r["status"] == "running" for r in win.model.rows), 30)
    win.bt_pause.click()
    fails += not ok(win.state == "paused" and win.bt_pause.text() == "Lanjut", f"jeda: {win.state}/{win.bt_pause.text()}")
    win.bt_pause.click()
    fails += not ok(win.state == "running", "lanjut: running")
    win.control.cancel.set(); win.control.pause.clear()          # batal tanpa dialog konfirmasi
    fails += not ok(pump(lambda: win.state == "idle" and win.last_summary is not None and win.last_summary.get("cancelled")), "batal: kembali idle, cancelled=True")
    fails += not ok(not list(out.rglob(".*partial")), "batal: tidak ada .partial tersisa")
    # 7. pengaturan tersimpan
    win.close()
    saved = json.loads((work / "cfg" / "settings.json").read_text())
    fails += not ok(saved["source_dir"] == str(src) and saved["sync"]["policy"] == "trim", "pengaturan tersimpan")
    print("PNG:", shots)
    shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
