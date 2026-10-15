"""Siapkan third_party/vlc (libVLC Windows 64-bit) dari folder VLC mana pun yang berisi libvlc.dll + libvlccore.dll + plugins/.

    python build/prepare_vlc.py <folder_sumber> [--full]

Hanya plugin yang dibutuhkan pemutar yang disalin (hemat ~70 MB): akses berkas, demux MP4/MKV/AVI/TS/WAV, dekoder avcodec (HEVC)
+ PCM, paket, konversi chroma (swscale -> RV32), keluaran vmem (frame dirender aplikasi), audio_output, scaletempo/resampler.
--full menyalin semuanya. Sumber yang dipakai build ini: rilis RSATom/WebChimera.js v0.5.2 (VLC 3.0.11 x64, LGPL-2.1+).
Alternatif resmi: build/fetch_vlc.py (unduh VLC dari videolan.org lalu panggil skrip ini)."""
import shutil
import sys
from pathlib import Path

KEEP = {
    "access": {"libfilesystem_plugin.dll", "libidummy_plugin.dll"},
    "demux": {"libmp4_plugin.dll", "libmkv_plugin.dll", "libavi_plugin.dll", "libwav_plugin.dll", "libts_plugin.dll",
              "libps_plugin.dll", "libes_plugin.dll", "libh26x_plugin.dll", "librawaud_plugin.dll", "librawvid_plugin.dll"},
    "codec": {"libavcodec_plugin.dll", "libaraw_plugin.dll", "liblpcm_plugin.dll", "libd3d11va_plugin.dll", "libdxva2_plugin.dll"},
    "video_filter": {"libdeinterlace_plugin.dll", "libscale_plugin.dll", "libcroppadd_plugin.dll"},
    "packetizer": None, "video_chroma": None, "video_output": None, "audio_output": None, "audio_filter": None,
    "audio_mixer": None, "stream_filter": None,                      # None = seluruh isi folder
}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    full = "--full" in sys.argv
    dst = Path(__file__).resolve().parents[1] / "third_party" / "vlc"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for dll in ("libvlc.dll", "libvlccore.dll"):
        shutil.copy2(src / dll, dst / dll)
    if full:
        shutil.copytree(src / "plugins", dst / "plugins")
    else:
        for sub, names in KEEP.items():
            d = src / "plugins" / sub
            if not d.is_dir():
                continue
            (dst / "plugins" / sub).mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                if f.is_file() and (names is None or f.name in names):
                    shutil.copy2(f, dst / "plugins" / sub / f.name)
    (dst / "README_VLC.txt").write_text(
        "libVLC (VideoLAN) disertakan sebagai pustaka dinamis di bawah lisensi GNU LGPL v2.1 atau lebih baru.\n"
        "Sumber kode: https://www.videolan.org/vlc/ dan https://code.videolan.org/videolan/vlc\n"
        "Build yang disertakan: VLC 3.0.11 x64 dari rilis RSATom/WebChimera.js v0.5.2 (hanya subset plugin).\n"
        "Anda dapat mengganti folder ini dengan libVLC 3.0.x lain (libvlc.dll, libvlccore.dll, plugins/).\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 2**20
    print(f"third_party/vlc siap: {size:.1f} MB")


if __name__ == "__main__":
    main()
