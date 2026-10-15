"""Mengambil libVLC Windows 64-bit untuk disertakan di paket (PRD 0.3 FR-120). BELUM DIUJI (sandbox pengembangan
tidak dapat mengunduh dari VideoLAN) — jalankan di mesin build/CI Windows dan periksa hasilnya.

    python build/fetch_vlc.py [--version 3.0.21] [--dest third_party/vlc]

Hasil: third_party/vlc/{libvlc.dll, libvlccore.dll, plugins/, LICENSE-*.txt}. Spec PyInstaller menyalin folder ini ke
paket (datas) dan pemutar memakai VLC_PLUGIN_PATH menunjuk ke sana. libVLC berlisensi LGPL 2.1+: sertakan teks lisensi dan
catatan sumber (lihat PRD 0.3 §8). Pemangkasan plugin (target total <=70 MB) ditentukan setelah uji putar di Windows.
"""
import argparse, shutil, sys, tempfile, urllib.request, zipfile
from pathlib import Path

KEEP_TOP = ("libvlc.dll", "libvlccore.dll", "plugins", "COPYING.txt", "NEWS.txt")
DROP_PLUGIN_DIRS = ()          # isi setelah uji: mis. plugins/lua bila ada; jangan menebak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="3.0.21")
    ap.add_argument("--dest", default="third_party/vlc")
    a = ap.parse_args()
    url = f"https://get.videolan.org/vlc/{a.version}/win64/vlc-{a.version}-win64.zip"
    dest = Path(a.dest)
    tmp = Path(tempfile.mkdtemp())
    z = tmp / "vlc.zip"
    print("mengunduh", url)
    urllib.request.urlretrieve(url, z)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(tmp / "x")
    root = next((tmp / "x").iterdir())
    dest.mkdir(parents=True, exist_ok=True)
    for name in KEEP_TOP:
        src = root / name
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
        elif src.exists():
            shutil.copy2(src, dest / name)
    for d in DROP_PLUGIN_DIRS:
        shutil.rmtree(dest / d, ignore_errors=True)
    (dest / "VLC-VERSION.txt").write_text(a.version + "\n")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 2**20
    print(f"selesai: {dest} ({size:.0f} MB)")
    if not (dest / "libvlc.dll").exists():
        sys.exit("libvlc.dll tidak ditemukan; struktur zip VLC mungkin berubah")


if __name__ == "__main__":
    main()
