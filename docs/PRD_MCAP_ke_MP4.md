# PRD: MCAP → MP4 Converter (Windows)

| | |
|---|---|
| **Versi** | 0.2 (universal, i18n, lokasi output) |
| **Tanggal** | 3 Oktober 2026 |
| **Platform** | Windows 10/11 x64, aplikasi desktop + CLI |
| **Status** | Keputusan §13 dimasukkan, menunggu persetujuan akhir |

## 1. Ringkasan

Aplikasi Windows yang mengubah rekaman `.mcap` apa pun yang berisi channel video terkompresi menjadi file `.mp4` **tanpa re-encode** (remux), lalu menghasilkan **metadata lengkap** dan **laporan batch**. Aplikasi **universal**: channel video ditemukan otomatis dari skema pesan (bukan nama topik atau perangkat tertentu), dan penamaan keluaran mengikuti nama file sumber. Berjalan offline di data lokal, UI tiga bahasa (Indonesia, English, 简体中文), tanpa kunci lisensi atau aktivasi. Logika inti berasal dari notebook Colab `mcap_drive_batch`.

## 2. Latar belakang dan masalah
Notebook Colab bergantung pada Google Drive, dijalankan manual per sel, dan tidak cocok untuk volume produksi (ratusan GB total). Audit kode menemukan cacat yang harus diperbaiki, bukan sekadar dibungkus:

| # | Cacat di kode asal | Dampak |
|---|---|---|
| D1 | Seluruh video satu file dimuat ke RAM (`raw` + salinan `samples`, ±2× ukuran) | Gagal pada file besar |
| D2 | Offset `stco` 32-bit dan ukuran `mdat` 32-bit | MP4 rusak di atas 4 GB |
| D3 | FPS hanya bilangan bulat dan timestamp diabaikan (`stts` satu entri) | sumber 29,97 fps terdorong ke 30: melenceng ±1,5 dtk per 25 menit |
| D4 | Jalur ROS2 melompati `header.stamp` | Tidak ada waktu absolut di keluaran |
| D5 | Pemeriksaan "sudah ada" hanya melihat file keluaran | Kondisi setengah jadi (sebagian file keluaran sudah ada) tidak ditangani |
| D6 | Tidak ada verifikasi hasil, laporan, maupun metadata | Hasil tidak dapat dipertanggungjawabkan |

## 3. Tujuan dan non-tujuan
**Tujuan**
1. Konversi massal MCAP → MP4 yang cepat, aman, dan dapat dilanjutkan.
2. Keluaran bit-identik pada data video (hanya dibungkus ulang).
3. Metadata lengkap (MCAP, bitstream, MP4) yang dapat dilihat dan diekspor.
4. Laporan batch yang bisa diaudit.

**Non-tujuan (versi 1)**
- Re-encode atau konversi ke H.264.
- Dukungan macOS/Linux.
- Pengolahan awan atau pembacaan langsung dari Google Drive.
- Penyuntingan video (potong, gabung).
- Ekspor data sensor (ditunda ke fase 2, lihat §6.8).
- Sistem lisensi, kunci, atau aktivasi.

## 4. Pengguna dan skenario
**Pengguna utama:** operator/teknisi data yang memproses banyak rekaman untuk pekerjaan. Tidak diasumsikan paham MCAP atau MP4.

| Skenario | Alur |
|---|---|
| S1 Batch harian | Pilih folder sumber lokal → pindai → tinjau daftar → Mulai → baca laporan |
| S2 Lanjut setelah mati listrik | Buka lagi → aplikasi membaca manifest → hanya file belum selesai yang diproses |
| S3 Audit | Buka penampil metadata untuk satu file atau bandingkan dua file |
| S4 Otomasi | Jalankan CLI dari Task Scheduler dan ambil laporan CSV/JSON |

## 5. Data masukan (berdasarkan diagnosa file contoh)

Aplikasi tidak bergantung pada nama topik atau perangkat. **Channel video** ditemukan dari skema dan encoding-nya:

| Adaptor | Skema dikenali | Encoding | Catatan dari file contoh |
|---|---|---|---|
| Foxglove | `foxglove.CompressedVideo` | protobuf | Contoh A: 2 channel video H.265 (174 dan 175 frame, ±6 dtk), 13 channel total; timestamp payload tampak **relatif** |
| ROS2 | `sensor_msgs/msg/CompressedImage` | CDR | Contoh B: 1 channel video H.265, 46.159 frame, 29,97 fps, 1540 dtk, `header.stamp` **epoch absolut**; channel lain: IMU ≈200 Hz, audio, info kamera |

- Codec v1: **H.265**; H.264 P1 (FR-17). Format lain (JPEG/PNG) dilaporkan "tidak didukung" per channel tanpa menggagalkan channel lain.
- Channel non-video tidak dikonversi, tetapi didaftar di metadata (skema, jumlah pesan, laju).
- Satu file boleh punya 0..N channel video; pengguna memilih channel yang diekspor (bawaan: semua).
- Arti segmen pada nama file **tidak diasumsikan** (format nama bisa berbeda-beda). Nama file dipakai untuk penamaan keluaran dan, bila pengguna mengatur pola, untuk metadata (FR-43).

## 6. Persyaratan fungsional
Prioritas: **P0** wajib v1, **P1** v1 jika waktu cukup, **P2** fase 2.

### 6.1 Pemindaian dan antrean
- **FR-01 (P0)** Pilih folder sumber lokal dan folder tujuan, dengan opsi subfolder; struktur sumber ditiru di tujuan.
- **FR-02 (P0)** Pindai cepat: daftar file, ukuran, jenis, status awal (menunggu / sudah ada / tidak lengkap). Mode **dry-run** tanpa menulis apa pun.
- **FR-03 (P0)** Antrean dengan Mulai, Jeda, Lanjut, Batal (batal bersih, file `.partial` dihapus).
- **FR-04 (P0)** Pemrosesan paralel, jumlah worker dapat diatur; satu file gagal tidak menghentikan antrean.
- **FR-05 (P1)** Drag & drop folder/file; urutan antrean dapat diubah.

### 6.1b Lokasi output
- **FR-06 (P0)** Pilih lokasi output lewat dialog folder, dengan mode: (a) **folder khusus** (bawaan; struktur meniru sumber atau datar), (b) **di samping file sumber**, (c) **tanya setiap run**.
- **FR-07 (P0)** Lokasi terpisah yang dapat dipilih untuk: MP4, sidecar metadata, laporan + manifest, dan folder sementara; bawaan semuanya mengikuti folder keluaran.
- **FR-08 (P0)** Validasi: folder dapat ditulis, ruang cukup, volume sama dengan folder sementara (agar rename atomik), folder keluaran tidak berada di dalam folder sumber yang dipindai (mencegah memproses ulang hasil), peringatan untuk drive jaringan.
- **FR-09 (P1)** Riwayat folder terakhir, tombol "Buka folder output", override lokasi per file/antrean. CLI: `--dst`, `--mode`, `--reports-dir`.

### 6.2 Konversi
- **FR-10 (P0)** Temukan channel video dari skema; ekstrak payload H.265 per channel dan tulis satu MP4 per channel tanpa re-encode.
- **FR-11 (P0)** **Penulisan streaming**: `mdat` ditulis langsung ke disk selama pembacaan; di RAM hanya tabel sampel (±10 byte/frame). `moov` ditulis di akhir.
- **FR-12 (P0)** **co64 dan `mdat` 64-bit (largesize)** sebagai opsi: *Otomatis* (bawaan, hanya bila perlu), *Selalu*, *Mati*. Pada *Mati*, file yang melewati 4 GB ditandai **gagal dengan pesan jelas**, tidak pernah menghasilkan MP4 rusak.
- **FR-13 (P0)** Mode FPS: *dari timestamp* (per-frame, `stts` dikompresi RLE, timescale 90000), *dari `session.json`*, *pecahan manual* (mis. 30000/1001), *bilangan bulat*. Bawaan: dari timestamp bila absolut/monoton, selain itu `session.json`, lalu 30.
- **FR-14 (P0)** Penulisan atomik: tulis ke `.partial`, rename setelah lengkap, urutan publikasi deterministik. Kelengkapan ditentukan **manifest** (status `selesai` ditulis paling akhir), bukan file tertentu.
- **FR-15 (P1)** Opsi faststart (pindahkan `moov` ke depan; menulis ulang seluruh file, bawaan mati).
- **FR-16 (P0)** **Penamaan keluaran mengikuti nama file sumber.** Satu channel video: `{nama}.mp4`. Beberapa channel: `{nama}_{topik}.mp4` (`{topik}` = nama topik dibersihkan, mis. `/a/cam/0` → `a_cam_0`). Pola dapat diubah dengan token `{nama}`, `{topik}`, `{indeks}`, `{tanggal}`. Karakter ilegal Windows dibersihkan, panjang dibatasi, tabrakan nama ditangani (nomor otomatis atau gagal).
- **FR-17 (P1)** Dukungan H.264 (butuh `avcC` dari SPS/PPS).

### 6.3 Penanganan file yang sudah ada
- **FR-20 (P0)** Set keluaran yang diharapkan (hasil FR-16 untuk semua channel terpilih) **lengkap** (manifest `selesai`; bila manifest tidak ada, semua file ada dan lolos verifikasi cepat) → lewati dan catat "sudah ada".
- **FR-21 (P0)** Set **tidak lengkap** → tidak ditimpa, ditandai "perlu perhatian"; tombol manual "bersihkan sisa lalu ulangi".
- **FR-22 (P0)** Tidak ada penimpaan diam-diam di mode mana pun; opsi "beri nomor" tersedia sebagai alternatif.

### 6.4 Verifikasi
- **FR-30 (P0)** Cepat: parse ulang struktur MP4; jumlah `stsz` = jumlah frame; offset terakhir + ukuran = akhir `mdat`; `moov` valid.
- **FR-31 (P0)** Baca hasil dengan ExifTool sebagai pemeriksaan kedua.
- **FR-32 (P1)** Penuh: bandingkan hash aliran payload MP4 dengan sumber.
- **FR-33 (P0)** **Sinkronisasi antar-channel video harus sesuai.** Frame dipasangkan lewat timestamp. Kebijakan: *Ketat* (bawaan: jumlah frame sama dan tiap pasangan dalam toleransi, bawaan ½ periode frame; bila tidak, status `tidak sinkron` dan keluaran **tidak dipublikasikan**), *Peringatan* (tulis dan tandai), atau *Pangkas* (P1: buang frame berlebih di ujung yang aman). Laporan memuat jumlah frame per channel, selisih, dan posisi ketidakcocokan pertama. Contoh A (174 vs 175) akan ditandai tidak sinkron pada mode Ketat.

### 6.5 Metadata
Tiga lapis, disimpan di sidecar JSON dan ditampilkan di penampil.
- **FR-40 (P0) Lapis A: MCAP** (parser sendiri, dari summary section di akhir file; fallback pemindaian bila tidak ada indeks): header/profil/library, semua topik dengan skema dan encoding, jumlah pesan dan laju per topik, waktu mulai/selesai, metadata record, attachment, kompresi chunk, `session.json`. Protobuf didekode lewat deskriptor tertanam; ROS2 untuk tipe yang dikenal.
- **FR-41 (P0) Lapis B: bitstream H.265** (VPS/SPS/PPS): profil, level, resolusi, VUI warna, jumlah keyframe, panjang GOP.
- **FR-42 (P0) Lapis C: MP4 hasil** via ExifTool 13.59 yang disertakan (`-j -G1 -a -s`, mode `-stay_open`); semua modul ExifTool disertakan utuh.
- **FR-43 (P1)** Pola nama file kustom (regex/token) untuk mengambil ID dan waktu dari nama; bawaan **mati**. Pola dapat disimpan sebagai preset dan hasilnya diperiksa konsistensinya dengan timestamp internal.
- **FR-44 (P0)** Penulisan native ke `moov`: `creation_time` (mvhd/tkhd/mdhd) dan ringkasan di `udta`, tanpa menyalin ulang file.
- **FR-45 (P1)** Embed tambahan via ExifTool, **bawaan mati**, dengan peringatan bahwa ExifTool menyalin ulang seluruh file (waktu dan ruang disk 2×).
- **FR-46 (P0)** Sumber waktu pembuatan, berurutan: timestamp absolut dalam pesan → `log_time` record MCAP → pola nama file (bila diatur) → waktu modifikasi file → kosong. Timestamp kecil/relatif tidak dipakai sebagai tanggal. Sumber yang dipakai dicatat. Di M1 aplikasi menampilkan `log_time` dan info perangkat file contoh agar sumber terbaik ditentukan dari data.

### 6.6 Laporan
- **FR-50 (P0)** Manifest (`manifest.jsonl`) ditulis bertahap per file di folder tujuan; sumber kebenaran untuk resume dan laporan.
- **FR-51 (P0)** Isi per file: path relatif, jenis, ukuran sumber, ID/waktu dari nama, status (`sukses`/`dilewati`/`tidak lengkap`/`gagal`), pesan error, lama proses, MB/dtk, frame per kamera, selisih frame, fps terukur dan dipakai, durasi, keyframe, codec/profil/level, resolusi, waktu mulai/selesai (UTC + lokal) dan sumbernya, jumlah pesan per topik, path/ukuran keluaran, hasil verifikasi, ada tidaknya sidecar.
- **FR-52 (P0)** Ringkasan run: total per status, total ukuran, durasi run, throughput, versi aplikasi, **seluruh pengaturan yang dipakai**.
- **FR-53 (P0)** Ekspor: **CSV** (pemisah koma/titik koma, UTF-8 BOM), **JSON**, **XLSX** (sheet Ringkasan, File, Peringatan, Topik MCAP), **HTML** mandiri.
- **FR-54 (P0)** Ekspor ulang kapan saja dari manifest, termasuk dari hasil dry-run.

### 6.7 Antarmuka
- **FR-60 (P0)** Tabel file: status, progres per file dan keseluruhan, kecepatan, ETA, log per file.
- **FR-61 (P0)** Cek ruang disk sebelum mulai dan sebelum tiap file; berhenti rapi bila kurang.
- **FR-62 (P0)** Penampil metadata: pohon tag per grup (File, MCAP, H.265, QuickTime, Track), pencarian/filter, salin nilai, buka lokasi file.
- **FR-63 (P1)** Tag favorit sebagai kolom tambahan di tabel; bandingkan metadata dua file.
- **FR-64 (P1)** Notifikasi Windows saat selesai atau ada kegagalan; pratinjau frame pertama.
- **FR-65 (P0)** **CLI** setara fitur inti: `app.exe --src … --dst … [opsi]`, kode keluar bermakna (0 sukses, 1 ada gagal, 2 ada perlu-perhatian).

### 6.8 Fase 2: ekspor data sensor (opsional, bawaan mati)

**Rekomendasi cakupan** (perkiraan kasar, orang-hari kerja dengan bantuan AI):
- **Masuk v1 (M2):** kalibrasi/ekstrinsik kamera dan IMU sebagai bagian sidecar JSON. Kecil, bernilai tinggi, dan sudah dibaca untuk metadata.
- **FR-70 (P2, ±3-4 hari):** IMU → CSV.
- **FR-71 (P2, ±4-6 hari):** audio → WAV. Sebagian sumber tidak menyertakan info format; sample rate/format diisi manual atau diperkirakan dari ukuran blok dan timestamp, dan hasil perkiraan diberi label.
- Dikerjakan setelah M4; tiap ekspor adalah sakelar terpisah, bawaan mati.

### 6.9 Bahasa (i18n)
- **FR-80 (P0)** Bahasa: **Indonesia, English, 简体中文**; dipilih di Pengaturan, berlaku tanpa restart bila memungkinkan.
- **FR-81 (P0)** **Semua teks** (menu, tombol, pesan error, status, header laporan, bantuan, keluaran CLI) berada di berkas bahasa terpisah yang dapat diubah; tidak ada teks tertanam di kode.
- **FR-82 (P0)** Kode status/error di JSON dan manifest tetap stabil dan netral bahasa; terjemahan hanya di tampilan dan laporan.
- **FR-83 (P0)** Bahasa laporan dapat dipilih terpisah dari bahasa UI.
- **FR-84 (P1)** Pengguna dapat menambah/mengubah berkas bahasa tanpa build ulang. Font CJK (mis. Microsoft YaHei) dan lebar teks dinamis diuji.

## 7. Pengaturan

| Setting | Pilihan | Bawaan |
|---|---|---|
| Sumber | folder lokal; subfolder ikut atau tidak; channel video dipilih | semua channel |
| **Lokasi output** | folder khusus (meniru sumber/datar) / di samping sumber / tanya tiap run | folder khusus, meniru sumber |
| Lokasi terpisah | MP4, sidecar, laporan + manifest, folder sementara | ikut folder keluaran |
| Penamaan keluaran | pola dengan token | `{nama}` / `{nama}_{topik}` |
| Jika nama bentrok | nomor otomatis / gagal | nomor otomatis |
| Mode FPS | timestamp / `session.json` / pecahan / bulat | timestamp |
| co64 | Otomatis / Selalu / Mati | Otomatis |
| Jika keluaran ada | lewati / beri nomor | lewati dan laporkan |
| Sinkronisasi channel | Ketat / Peringatan / Pangkas; toleransi | Ketat, ½ frame |
| Worker | 1 sampai N | 2 (maks otomatis min(inti−1, 4)) |
| Batas RAM per worker | MB | 512 |
| Verifikasi | mati / cepat / penuh | cepat |
| Setelah sukses | biarkan / pindahkan / hapus sumber | **biarkan**; hapus butuh konfirmasi eksplisit |
| Faststart | on/off | off |
| Metadata | ringkas/lengkap; sidecar JSON; embed native; embed ExifTool | lengkap; on; on; **off** |
| Pola nama file → metadata | mati / regex | mati |
| Laporan | CSV/JSON/XLSX/HTML; pemisah CSV; kolom | CSV + JSON |
| Nilai tag | terformat / mentah / keduanya | keduanya |
| Lokasi ExifTool | bawaan paket / path kustom | bawaan |
| Bahasa UI / bahasa laporan | Indonesia, English, 简体中文 | Indonesia |

## 8. Persyaratan non-fungsional
- **Integritas:** data video bit-identik dengan sumber; tidak ada MP4 setengah jadi di nama final.
- **Ketahanan:** aman terhadap mati listrik, kill proses, dan disk penuh; resume tanpa mengulang file selesai.
- **Memori:** mode streaming ≤ batas RAM per worker, tidak bergantung ukuran file; total GUI + worker < 2 GB pada RAM 8 GB.
- **Kinerja:** perangkat target **Intel i5, RAM 8 GB, SSD**. Estimasi awal (belum diukur): ≥100 MB/dtk agregat, jadi ±100 GB ≈ 17 menit; dikonfirmasi lewat benchmark di M1.
- **Offline dan privasi:** tanpa koneksi jaringan dan tanpa telemetri.
- **Kompatibilitas:** jalur panjang Windows (>260 karakter), nama Unicode, drive jaringan terbaca (dengan peringatan kinerja).
- **Kompatibilitas hasil:** MP4 dapat dibuka di VLC dan MPC-HC; pemutar bawaan Windows butuh ekstensi HEVC (dicatat di bantuan aplikasi).
- **Keamanan perangkat lunak:** distribusi onedir; code signing bila dibagikan luas guna menekan false-positive antivirus.
- **Tanpa lisensi:** tidak ada kunci, aktivasi, atau layar EULA. Berkas bawaan ExifTool disertakan utuh apa adanya.

## 9. Arsitektur dan teknologi (usulan)
- **Bahasa/GUI:** Python 3.12, PySide6 (atau customtkinter); inti dipisah dari GUI sebagai pustaka + CLI.
- **Proses:** GUI = pengendali; konversi di proses terpisah (`multiprocessing`) agar jendela tidak membeku dan pembatalan bersih.
- **Modul:** `mcap_reader` (baca streaming + indeks), `source_adapters` (Foxglove/protobuf via deskriptor, ROS2/CDR), `mp4_writer` (streaming, co64, `stts` per-frame), `h265`, `naming`, `sync`, `metadata` (tiga lapis + ExifTool client), `verify`, `manifest`, `report`, `i18n`.
- **Dependensi:** `zstandard`, `lz4`, `protobuf`, `openpyxl`, ExifTool 13.59 (bundel).
- **Pengemasan:** PyInstaller onedir + installer Inno Setup; `exiftool.exe` dan `exiftool_files` di samping aplikasi.

## 10. Kriteria penerimaan
| ID | Kriteria |
|---|---|
| AC-1 | Contoh B (ROS2): 46.159 frame masuk; durasi MP4 = 1540,17 dtk ± 1 frame; tanpa drift |
| AC-2 | Contoh A (Foxglove) mode Ketat: ditandai `tidak sinkron` (174 vs 175), tidak dipublikasikan; mode Peringatan: dua MP4 ditulis dan peringatan muncul |
| AC-3 | Uji sintetis >4 GB: *Otomatis* menghasilkan co64 dan dapat diputar; *Mati* menghasilkan status gagal berpesan jelas |
| AC-4 | RAM per worker tidak melampaui batas pada file uji terbesar |
| AC-5 | Mematikan paksa di tengah batch lalu menjalankan ulang: file selesai tidak diproses ulang, tidak ada `.partial` yatim yang dianggap valid |
| AC-6 | Set tidak lengkap tidak pernah ditimpa dan muncul di laporan sebagai "perlu perhatian" |
| AC-7 | Verifikasi cepat lulus pada semua keluaran uji; hash payload (penuh) sama dengan sumber |
| AC-8 | Laporan CSV/JSON/XLSX/HTML dihasilkan dan terbuka benar di Excel lokal Indonesia |
| AC-9 | Metadata tiga lapis tampil di penampil dan tersimpan di sidecar JSON |
| AC-10 | Pembatalan di tengah proses tidak meninggalkan file `.partial` |
| AC-11 | Ketiga mode lokasi output berfungsi; folder keluaran di dalam folder sumber ditolak; ruang disk dicek |
| AC-12 | Penamaan mengikuti nama file: satu channel → `{nama}.mp4`, banyak channel → `{nama}_{topik}.mp4`; karakter ilegal dan tabrakan nama tertangani |
| AC-13 | Ganti bahasa Indonesia/English/简体中文: tidak ada teks tertanam yang tersisa; CJK tampil benar; laporan mengikuti bahasa pilihan |
| AC-14 | File tanpa channel video atau dengan codec tidak didukung dilaporkan jelas per channel, tanpa membatalkan batch |

## 11. Risiko dan mitigasi
| Risiko | Mitigasi |
|---|---|
| Frame B (urutan tampil ≠ urutan decode) membuat timing salah | Asumsi awal: tanpa B-frame (sesuai contoh). Deteksi dari bitstream; bila ditemukan, tandai peringatan dan tambahkan `ctts` |
| Timestamp relatif, tanggal absolut tidak diketahui | Urutan fallback FR-46; periksa `log_time` MCAP dan `DeviceInfo` pada contoh |
| File MCAP tanpa summary/indeks atau rusak | Fallback pemindaian penuh; status gagal dengan alasan |
| Audio tanpa parameter format | Setting manual dan estimasi (fase 2) |
| Antivirus menandai EXE | onedir, code signing, tanpa packer |
| ExifTool embed menyalin ulang file besar | Bawaan mati, peringatan eksplisit |
| co64 *Mati* lalu file besar | Gagal bersuara, tidak pernah rusak diam-diam |
| Throughput rendah di drive jaringan | Peringatan di pemindaian; saran menyalin ke lokal |

## 12. Fase dan milestone

| M | Isi | Keluaran | Perkiraan |
|---|---|---|---|
| M1 | Inti CLI: adaptor Foxglove + ROS2, penulis MP4 streaming + co64 opsional + timestamp, sinkronisasi, penamaan, verifikasi, manifest; benchmark; **generator file sintetis >4 GB** (menggandakan frame dari file contoh) dan hook uji offset | CLI teruji pada dua file contoh | 5-7 hari |
| M2 | Lapis metadata, ExifTool client, sidecar JSON (termasuk kalibrasi), laporan CSV/JSON/XLSX/HTML | metadata dan laporan | 4-5 hari |
| M3 | GUI: antrean, tabel, penampil metadata, **pemilih lokasi output**, **i18n 3 bahasa** | aplikasi desktop | 7-10 hari |
| M4 | PyInstaller + Inno Setup, uji di mesin bersih (i5/8 GB/SSD) | installer | 2-3 hari |
| M5 | Fase 2: IMU → CSV, audio → WAV | modul opsional | 7-10 hari |

Perkiraan kasar dan akan dikoreksi setelah M1. Total v1 (M1-M4) ≈ 18-25 hari kerja.

## 13. Pertanyaan terbuka

**Keputusan**
| # | Topik | Keputusan |
|---|---|---|
| 1 | Arti segmen nama file | Tidak diasumsikan; pola kustom opsional (FR-43) |
| 2 | Waktu absolut file protobuf | Ditentukan dari data di M1 (urutan FR-46) |
| 3 | Uji co64 | File sintetis dan hook offset; tidak butuh file besar nyata |
| 4 | Sinkronisasi | Harus sesuai: mode Ketat bawaan (FR-33) |
| 5 | PC target | i5, 8 GB, SSD; 2 worker bawaan |
| 6 | Bahasa | Indonesia, English, 简体中文; semua teks dapat diubah |
| 7 | Fase 2 | Sesuai rekomendasi §6.8 |
| 8 | Lisensi | Tidak ada sistem lisensi |
| 9 | Code signing | Tidak untuk v1 (alat internal). Distribusi onedir plus catatan pengecualian SmartScreen/antivirus; signing dipertimbangkan bila dibagikan luas |
| 10 | Bawaan sinkronisasi | Tetap **Ketat** (syarat "harus sesuai"). M1 mencetak posisi ketidakcocokan pertama (mis. 174 vs 175) agar Ketat vs Pangkas diputuskan dari data nyata |
| 11 | Basis waktu berbeda antar-channel | Pasangkan memakai `log_time` MCAP yang sama untuk semua channel; bila tetap tidak sebanding: mode Ketat → `tidak sinkron` (alasan "basis waktu berbeda"), mode lain → peringatan |

**Catatan:** tidak ada pertanyaan terbuka yang menghalangi M1. Hal yang ditentukan dari data nyata dicatat di FR-33 dan FR-46.

## Lampiran A: Struktur keluaran

```
{lokasi_output}/{subfolder meniru sumber}/
  rekaman.mp4                  # satu channel video
  rekaman_{topik}.mp4          # bila beberapa channel
  rekaman.metadata.json        # sidecar tiga lapis
  manifest.jsonl               # status per file (sumber kebenaran resume)
  reports/run_{tanggal}.{csv,json,xlsx,html}
```

## Lampiran B: Status file
| Status | Arti | Tindakan |
|---|---|---|
| `sukses` | semua keluaran ada dan lolos verifikasi | tidak ada |
| `dilewati` | set keluaran lengkap sudah ada | dilaporkan |
| `tidak lengkap` | sebagian keluaran ada | tidak ditimpa; menunggu keputusan pengguna |
| `tidak sinkron` | jumlah/timestamp frame antar-channel tidak sesuai (mode Ketat) | keluaran tidak dipublikasikan; rincian di laporan |
| `gagal` | error baca/tulis/verifikasi | pesan dan penyebab di laporan; antrean lanjut |
