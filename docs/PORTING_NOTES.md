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

---
# Catatan pengerjaan v0.3 (audio, sesi, pemutar)

Baca dulu: `PRD_v0.3_Sesi_Audio_Pemutar.md`, `DATA_NYATA_ego.md`, `reference/prototypes/README.md`.

## Urutan kerja
| M | Kerjakan | Dari prototipe / kerangka | Uji |
|---|---|---|---|
| M5 | `audio.py` (adaptor RawAudio, celah), `mp4_writer.py` → multi-track (track `soun`, `ipcm`+`chnl`+`pcmC`, `elst`, interleave menurut `log_time`), `verify.py` (track audio), sidecar | `reference/prototypes/audio_inspect.py` | `tests/test_audio.py` (lepas `skipTest`), `tests/ego_synth.py`; lalu file asli + VLC/ffmpeg |
| M6 | `mcap_reader.chunk_index/topic_time_range`, `session.probe/detect/merge`, `sessions.json`, UI sesi, CLI | `jejak_waktu_proto.py`, `merge_sesi_proto.py` | `tests/test_session.py` |
| M7 | `player/engine.py`, tab Pemutar 1 kamera, kecepatan, paket libVLC | `build/fetch_vlc.py` (BELUM DIUJI) | uji di Windows/CI |
| M8 | `player/sync.py`, `playlist.py`, `filemanager.py`, `widgets.py`, sesi virtual | — | `tests/test_player_sync.py` (engine palsu) |

## Jebakan yang sudah ditemukan (jangan diulang)
1. **Jangan memakai awal/akhir keseluruhan file** untuk menyambung sesi (tumpang-tindih 12 ms dari sensor lain). Pakai waktu per topik video.
2. **Cocokkan topik lewat nama**, bukan nomor channel (nomor bergeser antar-file).
3. Pada sesi gabungan **hanya ada satu offset awal audio-video**; offset per file hanya berlaku pada mode per-file.
4. `Mp4Writer` sekarang satu track dan mengasumsikan `mdat` hanya berisi video (`verify.quick` memeriksa sampel rapat berurutan). Multi-track menuntut verifikasi per track dan interleaving; ubah `verify.py` bersamaan.
5. PySide6 dipin `<6.10` (ICU). PyInstaller MERGE dipakai untuk dua exe (GUI + CLI).
6. MP4 uji audio dibuat ffmpeg; **penulis kita belum pernah diuji** — jalankan AC-23 sebelum menyatakan selesai.
7. libVLC tidak bisa dijalankan sebagai root di Linux (uji sebagai pengguna biasa: `runuser -u nobody`). libVLC tidak bisa diuji di Windows dari sandbox pengembangan.

## Lingkungan uji
`ffmpeg`+`libx265`+`ffprobe` (membuat HEVC asli), Wine 9 + Python Windows 3.12 (build/uji exe), NSIS (installer), VLC 3.0.20 Linux (`cvlc`, uji audio MP4). Jalankan semua tes: `python -m unittest discover tests`; GUI headless: `python tests/gui_smoke.py`.

---

# Rencana v0.3 (audio, sesi, pemutar) — peta dari prototipe ke modul

Dokumen induk: `PRD_v0.3_Sesi_Audio_Pemutar.md`. Bukti data nyata: `DATA_NYATA_ego.md`. Fixture uji: `tests/ego_synth.py`.
Tiap milestone selesai dengan jeda untuk diperiksa pengguna sebelum lanjut.

| Milestone | Mulai dari | Menghasilkan | Tes yang harus dihidupkan (hapus `skipTest`) |
|---|---|---|---|
| **M5 Audio** | `reference/prototypes/audio_inspect.py` (parser protobuf RawAudio), temuan `ipcm` | `audio.py` (adaptor, celah); `mp4_writer.py` **refaktor ke multi-track** (video + audio, interleave, edit list, `ipcm`/`sowt`); `verify.py` track audio; sidecar audio | `tests/test_audio.py` |
| **M6 Sesi** | `reference/prototypes/jejak_waktu_proto.py` (jejak waktu dari ChunkIndex), `merge_sesi_proto.py` (gabung) | `mcap_reader.topic_time_range`; `session.probe/detect/merge`; `sessions.json`; mode `merged` di worker; tampilan sesi di GUI/CLI | `tests/test_session.py` |
| **M7 Pemutar inti** | `build/fetch_vlc.py` (belum teruji), `requirements-player.txt` | `player/engine.py` (python-vlc), kontrol, kecepatan, tab Pemutar satu kamera, paket libVLC di spec PyInstaller | uji manual di Windows asli |
| **M8 Playlist/penjelajah/2 kamera** | — | `player/sync.py`, `playlist.py`, `filemanager.py`, `widgets.py` | `tests/test_player_sync.py` (engine palsu) |
| **M9 Rilis** | `build/installer.nsi` | installer dengan libVLC, benchmark mesin referensi | AC-30, AC-32, AC-33 |

## Aturan yang tidak boleh dilanggar (hasil pelajaran data nyata)
1. **Jangan memakai awal/akhir keseluruhan file untuk menentukan kontinuitas.** Sensor lain membuatnya tumpang-tindih; pakai waktu pesan pertama/terakhir **per topik video**.
2. **Cocokkan channel lewat nama topik**, bukan nomor channel (nomor bergeser antar-file).
3. Waktu perekam bisa **relatif sejak boot**: urutan antar-file valid, tetapi bukan tanggal (FR-46) dan bisa mundur setelah boot ulang.
4. Parameter codec (VPS/SPS/PPS, resolusi) harus identik sebelum menempel dua bagian; jika tidak, sesi terpisah.
5. Audio: PCM disalin **apa adanya**; jangan resample atau encode. Celah diisi hening, bukan digeser.
6. libVLC tidak bisa diuji sebagai tampilan di sandbox; logika sinkron dan playlist **harus** terpisah dari libVLC supaya teruji dengan engine palsu.
7. Penulis MP4 audio kita belum pernah diuji di VLC (uji memakai MP4 dari ffmpeg): uji MP4 tulisan sendiri sebelum menyatakan M5 selesai.

## Menjalankan tes
`python -m unittest discover tests` (butuh ffmpeg + libx265). Tes yang masih `skipTest` adalah daftar kerja M5–M8.
`python tests/gui_smoke.py` untuk GUI headless. VLC untuk uji audio: `apt install vlc-bin vlc-plugin-base`, jalankan bukan sebagai root
(`runuser -u nobody -- cvlc -I dummy --no-video --sout '#transcode{acodec=s16l}:std{access=file,mux=wav,dst=out.wav}' file.mp4 vlc://quit`).
