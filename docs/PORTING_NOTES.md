# Catatan porting dari notebook ke aplikasi

Sumber acuan: `reference/legacy/` (notebook `mcap_drive_batch.ipynb` dan 4 skrip hasil dekode, sha256 terverifikasi).

| Legacy | Modul baru | Perubahan wajib |
|---|---|---|
| `mcap_to_mp4.iter_records`, `u16`, `mstr` | `mcap_reader` | baca streaming; tambah pembacaan summary/indeks MCAP |
| `Track`, `add_payload`, `collect_all`, `finalize` | `mcap_reader` + `mp4_writer` | **D1**: jangan tampung semua frame di RAM; simpan hanya tabel sampel |
| `write_mp4` | `mp4_writer` | **D2**: `co64` + `mdat` largesize (opsi Otomatis/Selalu/Mati); **D3**: `stts` per-frame dari timestamp |
| `get_fps` | `mp4_writer` / `settings` | **D3**: dukung pecahan (30000/1001) dan mode timestamp |
| `ros2_video.parse_compressed_image` | `source_adapters` | **D4**: baca `header.stamp`, jangan dilompati |
| `ros2_video.detect_layout` | `source_adapters` | ganti deteksi dari nama topik tetap menjadi deteksi skema (Foxglove / ROS2) |
| `extract_ros2`, skip `output.exists()` | `manifest` + `naming` | **D5**: kelengkapan lewat manifest; penamaan `{nama}` / `{nama}_{topik}` |
| `worker.py` | `worker` | proses terpisah per file; kirim progres ke GUI/CLI |
| (tidak ada) | `verify`, `sync`, `metadata`, `report` | **D6**: baru, lihat PRD §6.4-6.6 |

## ExifTool
- Jalankan satu proses: `third_party/exiftool/exiftool.exe -stay_open True -@ -` dan kirim argumen per baris, akhiri dengan `-execute`.
- Opsi baca: `-j -G1 -a -s` (dan `-n` untuk nilai mentah).
- Tulis tag (embed) menyalin ulang seluruh file: bawaan mati (PRD FR-45).

## Urutan pembangunan
M1 inti CLI -> M2 metadata + laporan -> M3 GUI + i18n -> M4 pengemasan -> M5 data sensor (lihat PRD §12).
