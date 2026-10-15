# PyInstaller onedir: MCAP2MP4.exe (GUI, tanpa konsol) + mcap2mp4-cli.exe (CLI) dalam satu folder.
# Jalankan dari akar proyek:  pyinstaller build/mcap2mp4.spec --noconfirm
from pathlib import Path
root = Path(SPECPATH).parent
import sys
sys.path.insert(0, str(root / "src"))
from mcap2mp4 import CREDIT, __version__ as _ver
_t = tuple(int(x) for x in _ver.split(".")) + (0,) * (4 - len(_ver.split(".")))
_vf = root / "build" / "version_info.generated.txt"
_vf.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_t}, prodvers={_t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', '{CREDIT}'), StringStruct('FileDescription', 'MCAP2MP4 - konverter MCAP ke MP4 dan pemutar'),
    StringStruct('FileVersion', '{_ver}'), StringStruct('InternalName', 'MCAP2MP4'),
    StringStruct('LegalCopyright', '{CREDIT}'), StringStruct('ProductName', 'MCAP2MP4'), StringStruct('ProductVersion', '{_ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")
datas = [(str(root / "i18n"), "i18n"), (str(root / "config"), "config"), (str(root / "assets"), "assets"),
         (str(root / "third_party" / "exiftool"), "third_party/exiftool")]
vlc_dir = root / "third_party" / "vlc"                  # diisi build/fetch_vlc.py (libVLC LGPL; sertakan lisensinya)
if vlc_dir.is_dir():
    datas.append((str(vlc_dir), "third_party/vlc"))
hidden = ["zstandard", "lz4.frame", "openpyxl", "mcap2mp4.worker", "vlc", "mcap2mp4.player.widgets"]
skip = ["tkinter", "matplotlib", "numpy", "pandas", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtSql",
        "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization"]
a_gui = Analysis([str(root / "build" / "entry_gui.py")], pathex=[str(root / "src")], datas=datas,
                 hiddenimports=hidden, excludes=skip)
a_cli = Analysis([str(root / "build" / "entry_cli.py")], pathex=[str(root / "src")], datas=datas,
                 hiddenimports=hidden, excludes=skip + ["PySide6"])
MERGE((a_gui, "entry_gui", "MCAP2MP4"), (a_cli, "entry_cli", "mcap2mp4-cli"))
pyz_gui, pyz_cli = PYZ(a_gui.pure), PYZ(a_cli.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="MCAP2MP4", console=False, upx=False,
              icon=str(root / "assets" / "icon.ico"), version=str(_vf))
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="mcap2mp4-cli", console=True, upx=False,
              icon=str(root / "assets" / "icon.ico"), version=str(_vf))
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas, name="MCAP2MP4", upx=False)
