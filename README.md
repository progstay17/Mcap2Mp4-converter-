# MCAP2MP4: paket dasar pembangunan

Konversi `.mcap` -> `.mp4` (remux H.265, tanpa re-encode) untuk Windows, dengan metadata lengkap dan laporan batch.
Spesifikasi lengkap: `docs/PRD_MCAP_ke_MP4.md` (v0.2). Catatan porting: `docs/PORTING_NOTES.md`.

## Isi paket
| Folder | Isi |
|---|---|
| `docs/` | PRD dan catatan porting dari notebook |
| `reference/legacy/` | Notebook `mcap_drive_batch.ipynb` dan 4 skrip asli (`mcap_to_mp4.py`, `extractor.py`, `ros2_video.py`, `worker.py`), sha256 terverifikasi. **Hanya acuan, jangan diimpor** |
| `third_party/exiftool/` | ExifTool (`exiftool.exe` + `exiftool_files`) apa adanya; ExifToolGUI tidak disertakan |
| `src/mcap2mp4/` | Kerangka aplikasi. **Sudah berfungsi:** `settings`, `i18n`, `naming`, `cli` (parser argumen). Sisanya stub bernomor FR |
| `i18n/` | Bahasa awal: `id.json`, `en.json`, `zh.json` |
| `config/` | `settings.default.json` (sesuai PRD §7) |
| `tests/` | `test_naming.py` |
| `build/` | `mcap2mp4.spec` (PyInstaller) dan `installer.iss` (Inno Setup), **belum diuji** |
| `samples/` | Tempat file `.mcap` contoh untuk uji |

## Mulai
```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m unittest discover tests
python -m mcap2mp4 --help
```
Pengemasan: `pyinstaller build/mcap2mp4.spec`, lalu kompilasi `build/installer.iss` dengan Inno Setup.

## Langkah berikutnya (M1)
Implementasikan `mcap_reader`, `source_adapters`, `mp4_writer` (streaming, co64 opsional, `stts` per-frame), `sync`, `verify`, `manifest`, `worker`, dengan memindahkan logika dari `reference/legacy/` sesuai tabel di `docs/PORTING_NOTES.md`. Uji pada dua file contoh di `samples/`, plus file sintetis >4 GB untuk co64.
