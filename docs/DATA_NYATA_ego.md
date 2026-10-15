# Data nyata: `ego_0014.mcap` + `ego_0015.mcap`

Dua file berurutan dari satu sesi perekaman, diunggah pengguna pada 8 Oktober 2026. Semua angka di bawah **diukur**, bukan
diperkirakan (alat: `src/mcap2mp4` + `reference/prototypes/`, ffprobe/ffmpeg 6.1, VLC 3.0.20). File mentah tidak disertakan
(±273 MB masing-masing); pengguna memilikinya.

## 1. Struktur file
| | `ego_0014` | `ego_0015` |
|---|---|---|
| Ukuran | 273.331.875 B | 273.287.954 B |
| Pesan / chunk | 78.690 / 339 | 78.700 / 338 |
| Library / profil / kompresi | libmcap 1.4.1 / (kosong) / tanpa kompresi | sama |
| Indeks (summary) | ada | ada |
| Waktu pesan (ringkasan file) | 2183,963895 → 2308,976320 dtk | 2308,963901 → 2433,979857 dtk |

Waktu **relatif sejak boot** (±36 menit), bukan tanggal. `stamp` di dalam pesan == `log_time` MCAP (selisih 0,000 ms).

## 2. Topik (13, sama di kedua file, tetapi **nomor channel berbeda**)
| Topik | Skema | Pesan (0014 / 0015) |
|---|---|---|
| `/ego/camera/0`, `/ego/camera/1` | `foxglove.CompressedVideo` (protobuf) | 3750 / 3750 per kamera |
| `/ego/audio/0` | `foxglove.RawAudio` (protobuf) | 7812 / 7813 |
| `/ego/imu/0/quat` | `visio_schema.v1.ros.geometry_msgs.Quaternion` | 58.823 / 58.821 |
| `/ego/imu/0/raw` | `visio_schema.v1.sensor.ImuRaw` | 3695 / 3693 |
| `/ego/camera/{0,1}/intrinsics` | `foxglove.CameraCalibration` | 125 |
| `/ego/camera/1/extrinsics`, `/ego/imu/0/extrinsics` | `foxglove.FrameTransform` | 125 |
| `/ego/imu/0/info` | `visio_schema.v1.calibration.ImuCalibration` | 125 |
| `/device_info` | `visio_schema.v1.service.device_info.DeviceInfo` | 125 |
| `/ego/system_health`, `/ego/command_result` | `visio_schema.v1.sensor.SystemHealth` / `...control.CommandResult` | 25 / 24, 85 / 99 |

Contoh pergeseran nomor channel: `/ego/camera/1` adalah channel 3 di 0014 dan channel 1 di 0015.

## 3. Video (per kamera, kedua file sama)
- H.265 Main, level 4.0, 1920×1080, 4:2:0, 8-bit; **3750 frame**, tepat **30 fps** (median 33,333 ms; min 33,29; maks 33,38).
- GOP tetap 30 frame (125 keyframe). **Setiap file dimulai dengan keyframe** (NAL 19) berisi VPS/SPS/PPS → 0 frame dibuang.
- VPS/SPS/PPS **identik** antar-file (sidik jari SHA-1: `ead097e4` / `97c1bc1d` / `4a139675`).
- Kamera 1 mendahului kamera 0 sebesar 7 µs; jumlah frame sama (3750 = 3750).

| Topik | Frame pertama → terakhir 0014 | Frame pertama → terakhir 0015 |
|---|---|---|
| `/ego/camera/0` | 2183,963902 → 2308,930574 | 2308,963908 → 2433,930595 |
| `/ego/camera/1` | 2183,963895 → 2308,930567 | 2308,963901 → 2433,930587 |

**Sambungan video:** frame terakhir 0014 → frame pertama 0015 = **33,334 ms = 1,0000 periode frame** (kontinu).
**Ringkasan file menyesatkan:** akhir 0014 (2308,976320) lebih besar dari awal 0015 (2308,963901) → *tumpang-tindih 12,4 ms*
karena sensor lain (audio/IMU) melewati akhir video. Deteksi sesi wajib memakai waktu **per topik video**, bukan ringkasan.
Waktu per topik didapat murah dari indeks chunk: 2 chunk dibaca, 6–68 ms per file, hasil identik dengan pemindaian penuh.

## 4. Audio (`foxglove.RawAudio`)
- `pcm-s16` little-endian interleaved, **16.000 Hz, 2 kanal**, 1024 B = **256 sampel/pesan** tiap **16,000 ms** (tepat).
- Kontinu: celah maksimum 0,000 ms di dalam file; **sambungan antar-file 0,000 ms**.
- 0014: 1.999.872 sampel (124,992 dtk); 0015: 2.000.128 sampel (125,008 dtk); jumlah **4.000.000 sampel = 250,000 dtk** tepat.
- Mulai setelah video: +31,955 ms (0014), +23,949 ms (0015). Arti `stamp` (awal vs akhir blok) **belum terbukti** dari data.
- Beban: 64 KB/dtk → ±8 MB per 125 dtk (±6% dari video).

## 5. Hasil konversi saat ini (v0.2.0, per file, `--verify full`)
4 MP4 (±125 MB masing-masing); HEVC Main 1920×1080 30 fps, 3750 frame, 124,99999–125,00003 dtk; verifikasi penuh lulus
(payload sama dengan sumber); `ffmpeg` mendekode utuh tanpa error; 4,5 dtk untuk 521 MB (±65 MB/dtk, 1 core). Tanggal
pembuatan jatuh ke waktu modifikasi file karena waktu perekam relatif (FR-46). Audio belum ikut (diperbaiki PRD 0.3).

## 6. Prototipe penggabungan sesi (kamera 0, dua file → satu MP4)
7500 frame, 250,000033 dtk, 250 keyframe, 250.039.621 B payload; selisih antar-frame terbesar 33,376 ms (tanpa lonjakan di
sambungan); `ffmpeg` mendekode area sambungan bersih. Waktu proses 2,1 dtk.

## 7. Audio di dalam MP4 (uji kelayakan)
- ffmpeg 6.1 menulis PCM sebagai `ipcm` (ISO/IEC 23003-5) di MP4 dan membacanya kembali (pcm_s16le, 16 kHz, 2 kanal).
- **VLC 3.0.20** memutar varian `ipcm` maupun `sowt`; PCM hasil dekode **identik** dengan sumber (potongan 1000 frame ditemukan
  persis di offset byte yang diharapkan). Untuk `ipcm` VLC hanya mencetak peringatan tak berbahaya "unknown box type chnl".
- Catatan: MP4 uji ditulis ffmpeg, bukan penulis kita. Penulis kita wajib diuji ulang (AC-23).

## 8. Kecepatan dekode (satu pengukuran, 1 core, ffmpeg software)
1 stream 1080p30 H.265 didekode ±1,5× real-time (80 dtk untuk 125 dtk). Itu satu titik data pada CPU sandbox, **bukan** target
mesin kantor; dipakai hanya untuk perkiraan kasar beban dua kamera (PRD §10).

## 9. Hal yang tidak ada di data ini
B-frame (GOP I/P saja), celah waktu di sambungan, file yang dimulai di tengah GOP, lebih dari dua kamera, lebih dari satu topik
audio, tanggal absolut. Pengguna menyebut sesi ±2,9 menit per file; file ini 125 dtk → durasi tidak boleh dijadikan aturan.
