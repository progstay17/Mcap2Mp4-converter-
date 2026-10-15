# MCAP2MP4 — paket dasar build v0.3

> **v0.3.2** — Fadhil Ghifarion 法迪 for AIT. Lihat `docs/CHANGELOG.md`.
>
> **v0.3.0 — M5..M9 selesai**: audio, sesi/penggabungan, pemutar (1-2 kamera, playlist, penjelajah, sesi virtual), exe + installer NSIS (`build/build_windows.sh`). Lihat `docs/STATUS_M8.md`, `docs/STATUS_M9.md`.
>
> **Status M7 (pemutar inti, 1 kamera) selesai** — `player/engine.py`, `controller.py`, `widgets.py` (tab Pemutar), `build/bench_player.py`. Lihat `docs/STATUS_M7.md`.
>
> **Status M6 (sesi/penggabungan) selesai** — `session.py`, `mcap_reader.topic_time_range`, worker multi-bagian, batch/CLI (`--session-mode merged`, `--list-sessions`, `--join/--split`). Lihat `docs/STATUS_M6.md`.
>
> **Status M5 (audio) selesai** — `audio.py`, `mp4_writer.py` multi-track, `verify.py` per-track, worker/CLI/sidecar/laporan. 60 tes aktif, 15 dilewati (M6–M8). Lihat `docs/STATUS_M5.md`.

**Mulai di sini:** baca `docs/PRD_v0.3_Sesi_Audio_Pemutar.md` (apa yang akan dibangun dan kriteria penerimaannya), lalu
`docs/DATA_NYATA_ego.md` (bukti dari rekaman asli) dan `docs/PORTING_NOTES.md` (urutan kerja dan aturan).

| Isi paket | Keterangan |
|---|---|
| `src/mcap2mp4/` | Kode v0.2.0 yang sudah jalan (konversi, GUI, CLI) + kerangka baru `audio.py`, `session.py`, `player/` (belum diimplementasikan) |
| `tests/` | 49 tes aktif; 22 tes `skipTest` = daftar kerja M5–M8; `ego_synth.py` meniru rekaman asli |
| `reference/prototypes/` | Skrip pembuktian pada rekaman nyata (jejak waktu, gabung sesi, audio) |
| `docs/` | PRD 0.3 (baru), PRD 0.2 (arsip), data nyata, catatan porting |
| `build/` | PyInstaller spec, installer NSIS, `fetch_vlc.py` (belum teruji), pembuat ikon |
| `third_party/exiftool/` | ExifTool bawaan |

Cek cepat bahwa fondasi sehat: `pip install -r requirements-dev.txt` lalu `python -m unittest discover tests`.

---

# MCAP → MP4 (Windows)

## Mulai di sini (v0.3: dasar build)
Paket ini berisi **kode v0.2.0 yang sudah berjalan** (konversi + GUI + installer) ditambah **PRD 0.3** dan fondasi untuk tiga
kemampuan baru: **audio di MP4**, **sesi/penggabungan beberapa MCAP**, dan **pemutar VLC tertanam** (playlist, penjelajah
file, satu/dua kamera, kecepatan putar).

| Baca | Isi |
|---|---|
| `docs/PRD_v0.3_Sesi_Audio_Pemutar.md` | Persyaratan baru (FR-90…133), keputusan yang perlu disetujui (§15), kriteria penerimaan, milestone M5–M9 |
| `docs/DATA_NYATA_ego.md` | Bukti terukur dari rekaman asli `ego_0014/0015` |
| `docs/PORTING_NOTES.md` | Urutan kerja, jebakan yang sudah ditemukan, lingkungan uji |
| `docs/PRD_MCAP_ke_MP4_v0.2.md` | PRD lama (FR-01…84), sudah diimplementasikan |
| `reference/prototypes/` | Skrip pembuktian (jejak waktu per topik, gabung sesi, audio) |
| `tests/ego_synth.py` | Pembuat MCAP sintetis yang meniru rekaman asli (2 kamera, audio, IMU, channel tertukar) |
| `src/mcap2mp4/{session,audio}.py`, `player/` | Kerangka modul baru (belum diimplementasikan) |
| `tests/test_{session,audio,player_sync}.py` | Kerangka tes (dilewati sampai milestone-nya dikerjakan) |

Tes: `python -m unittest discover tests` → 48 aktif + 22 dilewati (kerangka M5–M8). Butuh `ffmpeg` + `libx265`.
`build/fetch_vlc.py` (pengambil libVLC Windows) **belum diuji**.

---

Konversi `.mcap` -> `.mp4` (remux H.265, tanpa re-encode) untuk Windows, dengan metadata lengkap dan laporan batch.
Spesifikasi lengkap: `docs/PRD_MCAP_ke_MP4_v0.2.md` (v0.2). Catatan porting: `docs/PORTING_NOTES.md`.

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

## Status (v0.2.0 — GUI + installer)

- **GUI** (`MCAP2MP4.exe`, PySide6): antrean, tabel status/progres/kecepatan/ETA, Jeda/Lanjut/Batal bersih,
  pemilih lokasi output (folder / di samping sumber / tanya saat mulai), penampil metadata (pohon, filter, salin,
  buka lokasi), laporan CSV/JSON/XLSX/HTML, bahasa Indonesia/English/简体中文 langsung tanpa restart.
- **CLI** (`mcap2mp4-cli.exe`): fitur setara; kode keluar 0/1/2/64.
- **Inti**: remux H.265 (Foxglove protobuf & ROS2 CDR) streaming tanpa re-encode, co64/largesize otomatis (>4 GB),
  durasi per-frame dari timestamp, sinkron antar-channel (strict/warn/trim), verifikasi quick/full, resume lewat
  `manifest.jsonl`, sidecar JSON + ExifTool.
- Belum: B-frame, codec selain H.265, `after_success`, `faststart`, ekspor IMU/audio (P2).

### Build
```
pip install -r requirements-dev.txt
pyinstaller build/mcap2mp4.spec --noconfirm        # -> dist/MCAP2MP4/ (GUI + CLI)
makensis -DVERSION=0.2.0 build/installer.nsi        # -> dist/installer/MCAP2MP4-Setup-0.2.0.exe
```
Installer: NSIS (wizard Indonesia/English/简体中文, Program Files, pintasan Start Menu/Desktop, entri Add/Remove Programs,
pemasangan senyap `/S`, pencopotan hanya menghapus berkas milik aplikasi; pengaturan di `%APPDATA%\MCAP2MP4` dipertahankan).
PySide6 di-pin `<6.10` (6.10+ butuh `icuuc.dll`). Workflow `build-windows` membangun semuanya di runner Windows.

### Tes
`python -m unittest discover tests` (butuh ffmpeg+libx265) dan `python tests/gui_smoke.py` (GUI headless).
`MCAP2MP4.exe --selftest <src> <out> <png>` menjalankan konversi nyata secara headless pada paket terpasang.
