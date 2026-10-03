# PyInstaller (onedir). BELUM DIUJI: jalankan dari akar proyek di Windows: pyinstaller build/mcap2mp4.spec
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / "src" / "mcap2mp4" / "__main__.py")],
             pathex=[str(root / "src")],
             datas=[(str(root / "i18n"), "i18n"), (str(root / "config"), "config"),
                    (str(root / "third_party" / "exiftool"), "third_party/exiftool")],
             hiddenimports=["zstandard", "lz4.frame", "google.protobuf"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mcap2mp4", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="mcap2mp4", upx=False)
