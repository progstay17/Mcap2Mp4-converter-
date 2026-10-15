# PRD 0.3: MCAP → MP4 — Audio, Sesi, dan Pemutar (Windows)

| | |
|---|---|
| **Versi** | 0.3 (menambah audio, sesi/penggabungan, pemutar tertanam; melanjutkan PRD 0.2) |
| **Tanggal** | 8 Oktober 2026 |
| **Platform** | Windows 10/11 x64, aplikasi desktop + CLI |
| **Basis kode** | v0.2.0 (inti konversi + GUI + installer sudah ada) |
| **Status** | Draf, menunggu persetujuan keputusan di §15 |
| **Dokumen terkait** | `PRD_MCAP_ke_MP4_v0.2.md` (arsip, FR-01…FR-84), `DATA_NYATA_ego.md` (bukti), `PORTING_NOTES.md` |

Nomor FR lama (FR-01…FR-84) tetap berlaku dan tidak diulang. Nomor baru: FR-90… (audio), FR-100… (sesi), FR-120… (pemutar).
Prioritas: **P0** wajib, **P1** jika waktu cukup, **P2** fase berikut.

## 1. Ringkasan
Aplikasi yang sudah mengubah `.mcap` menjadi `.mp4` tanpa re-encode ditambah tiga kemampuan:
1. **Audio ikut** sebagai track di MP4 (PCM lossless, tanpa encode).
2. **Sesi**: beberapa MCAP berurutan dalam satu folder dikenali otomatis sebagai satu rekaman panjang, dan dapat digabung menjadi satu MP4 per kamera.
3. **Pemutar tertanam** (libVLC, dekoder software sehingga **tidak butuh GPU**) dengan playlist, penjelajah file, kecepatan putar, tampilan satu kamera penuh atau dua kamera kiri-kanan yang disinkronkan.

Semua aturan deteksi bersifat **deterministik** (bukti waktu, struktur, dan parameter yang bisa dicek), tanpa heuristik buram dan tanpa AI.

## 2. Status v0.2.0 (sudah ada)
| Cakupan | FR | Status | Bukti |
|---|---|---|---|
| Pindai, dry-run, antrean, jeda/batal, paralel | 01–05 | Selesai (kecuali drag & drop, urutan antrean: P1) | 48 tes unit aktif, uji GUI headless |
| Lokasi output | 06–09 | Selesai (override per file: P1) | tes batch |
| Konversi streaming, co64, FPS dari timestamp, atomik, penamaan | 10–14, 16 | Selesai; faststart (15) dan H.264 (17) belum | uji >4 GB nyata (5,19 GB), ffmpeg dekode utuh |
| File sudah ada / tidak lengkap | 20–22 | Selesai | tes |
| Verifikasi cepat/penuh, sinkron antar-kamera | 30–33 | Selesai | rekaman nyata: payload identik |
| Metadata tiga lapis, sidecar, ExifTool | 40–46 | Selesai (embed ExifTool 45, pola nama 43: belum) | sidecar JSON |
| Manifest, laporan CSV/JSON/XLSX/HTML | 50–54 | Selesai | tes |
| GUI, penampil metadata, CLI, i18n 3 bahasa | 60–65, 80–84 | Selesai (kolom favorit, bandingkan: P1) | selftest exe di Wine |
| Installer | — | NSIS, wizard 3 bahasa, uji pasang/copot di Wine | Belum diuji di Windows asli |

Pengujian pada **rekaman asli** (`ego_0014/0015`) sudah dilakukan untuk konversi per file (lihat `DATA_NYATA_ego.md` §5).

## 3. Latar belakang dan masalah baru
| # | Masalah | Dampak |
|---|---|---|
| M1 | Pemutar MCAP (Foxglove) tidak bisa dipakai di PC kantor dan VPS. Dugaan: tidak ada GPU/WebGL (belum dibuktikan) | Rekaman tidak bisa ditinjau di mesin kerja |
| M2 | Satu sesi terpecah ke banyak file MCAP (contoh: 125 dtk per file); Foxglove menyatukannya saat putar, MP4 per file tidak | Tinjauan sesi panjang tidak nyaman |
| M3 | Audio (`/ego/audio/0`) tidak ikut ke MP4 | Hasil bisu |
| M4 | Tidak ada cara menjelajah hasil, membuat daftar putar, atau memutar dua kamera berdampingan | Harus buka banyak jendela pemutar manual |

## 4. Tujuan dan non-tujuan
**Tujuan:** (1) audio lossless di MP4 tersinkron dengan video; (2) deteksi sesi otomatis yang bisa dikoreksi manual, universal (bukan hanya pola 125 dtk atau nama `ego_`); (3) pemutar yang berjalan lancar di PC tanpa GPU sejauh fisik CPU memungkinkan; (4) playlist dan penjelajah file yang paham sesi dan pasangan kamera.

**Non-tujuan (versi ini):** re-encode video atau audio (AAC, H.264); penyuntingan (potong, ekspor klip); pemutaran langsung dari `.mcap` tanpa konversi; sinkron dua kamera **akurat per frame** (v1 toleransi ±2 frame); pemutar untuk topik sensor (IMU, kalibrasi); macOS/Linux; sistem lisensi.

## 5. Skenario baru
| Skenario | Alur |
|---|---|
| S5 Konversi sesi | Pilih folder → aplikasi mengelompokkan `ego_0014`+`ego_0015` sebagai satu sesi → (opsional) gabung → hasil per kamera, berisi audio |
| S6 Tinjau rekaman | Buka tab Pemutar → pilih rekaman di penjelajah → dua kamera kiri-kanan, satu seekbar sepanjang sesi, atur kecepatan |
| S7 Daftar putar | Tambahkan beberapa rekaman → urutkan → putar berurutan; daftar tersimpan untuk besok |
| S8 Koreksi deteksi | Aplikasi salah menggabung/memisah → pengguna gabung/pisah manual, keputusan diingat |

## 6. Temuan data nyata yang mengubah rancangan
Rincian dan angka lengkap: `DATA_NYATA_ego.md`.
| Temuan | Implikasi |
|---|---|
| Ringkasan file menunjukkan 0015 mulai 12,4 ms **sebelum** 0014 selesai (karena sensor lain), padahal frame video berjarak tepat 1 periode | Deteksi sesi memakai **waktu per topik video**, bukan awal/akhir file (FR-101/102) |
| Waktu per topik terbaca dari indeks chunk dalam 6–68 ms (2 chunk) | Pemindaian folder besar tetap cepat |
| Nomor channel berbeda antar-file | Cocokkan lewat **nama topik** |
| Tiap file dimulai dengan keyframe + VPS/SPS/PPS; parameter identik antar-file | Penggabungan trivial pada data ini; aturan umum tetap perlu untuk perekam lain (FR-108) |
| Audio PCM 16 kHz stereo, 256 sampel/16,000 ms, tanpa celah, sambungan antar-file 0,000 ms; total dua file = 4.000.000 sampel = 250,000 dtk | PCM passthrough kecil (±6% dari video) dan tepat; angka dipakai di AC-23 |
| Audio mulai +24…+32 ms setelah video; bedanya per file (audio satu file 124,992 dtk, video 125,000 dtk) | Offset ditulis sebagai edit list per file (FR-94); pada sesi gabungan hanya ada satu offset awal karena audio dan video sama-sama kontinu |
| Waktu relatif sejak boot | Urutan antar-file valid; boot ulang membuat waktu mundur → otomatis sesi terpisah; tanggal MP4 dari waktu modifikasi file |
| VLC 3.0.20 memutar audio `ipcm` dan `sowt`; PCM hasil dekode identik dengan sumber | Pemutar VLC dapat memutar audio hasil kita (penulis kita tetap harus diuji ulang) |
| Dekode software ±1,5× real-time per stream pada 1 core | Dua kamera 1080p30 butuh ±2 core hanya untuk dekode; kecepatan >1× menaikkannya (§10) |

## 7. Persyaratan fungsional baru

### 7.1 Audio dalam MP4
- **FR-90 (P0)** Deteksi channel audio dari **skema** (bukan nama topik). v1: `foxglove.RawAudio` (protobuf: timestamp, data, format, sample_rate, number_of_channels). Adaptor dapat diperluas (ROS2 menyusul bila ada contoh).
- **FR-91 (P0)** Format didukung v1: `pcm-s16` (little-endian, interleaved), laju dan jumlah kanal dari pesan. Format lain dilaporkan **tidak didukung per channel**; video tetap dikonversi.
- **FR-92 (P0)** **Passthrough lossless**: byte PCM ditulis apa adanya sebagai track `soun`. Sample entry `ipcm` (ISO/IEC 23003-5, dengan `chnl` dan `pcmC`) bawaan; `sowt` sebagai pilihan (setting `audio_codec_tag`). Tanpa encode.
- **FR-93 (P0)** Penempatan audio: setting `audio_in` = `all` (bawaan: setiap MP4 kamera memuat track audio sehingga mandiri; +±6% ukuran) | `first` (hanya MP4 pertama) | `none`. Beberapa channel audio → beberapa track bernama dari topik.
- **FR-94 (P0)** **Sinkron A/V** dari timestamp pesan. Posisi awal audio terhadap video ditulis sebagai **edit list** (`edts/elst`); t=0 file adalah waktu paling awal di antara semua track. Setting `audio_offset_ms` (bawaan 0) untuk koreksi bias perekam. *Asumsi:* `stamp` pesan = waktu sampel pertama blok; belum terbukti dari data, divalidasi dengan uji tepuk/klaker (R-A2).
- **FR-95 (P0)** Kontinuitas: celah antara akhir blok i dan awal blok i+1 melebihi toleransi (bawaan 1 ms) → **diisi hening** (PCM nol sepanjang celah) agar A/V tidak melenceng, dengan peringatan `audio_gap` (posisi, durasi); setting `audio_gap_policy` = `fill` (bawaan) | `ignore`. Tumpang-tindih → blok dipotong dan diperingatkan.
- **FR-96 (P0)** Penulisan **streaming interleaved**: blok audio ditulis ke `mdat` bersama video menurut urutan `log_time`; tabel sampel audio ringkas (`stsz` konstan satu frame PCM, `stts` satu entri, `stsc` run-length, `stco/co64` per blok). Batas RAM FR-11 tetap berlaku.
- **FR-97 (P0)** Verifikasi: jumlah sampel track = jumlah sampel sumber (+ hening isian); mode penuh membandingkan hash payload audio dengan sumber; verifikasi cepat memeriksa tabel sampel track audio; ExifTool membaca track.
- **FR-98 (P1)** Audio kontinu lintas bagian pada sesi gabungan (lihat FR-107); pengecekan celah antar-bagian memakai aturan FR-95.
- **FR-99 (P2)** Ekspor audio ke WAV terpisah (menggantikan FR-71 lama). AAC hanya sebagai opsi dengan pengkode eksternal; bukan v1.

### 7.2 Sesi dan penggabungan
- **FR-100 (P0)** **Sesi** = urutan ≥1 file MCAP dalam **satu folder** yang kontinu pada channel video dan identik secara struktur. Sesi tidak melintasi folder.
- **FR-101 (P0)** **Jejak waktu** per file, murah: per topik video dan audio → waktu pesan pertama dan terakhir (dari indeks chunk, baca ≤2 chunk; fallback pemindaian penuh bila tanpa indeks), periode frame (median), parameter codec (profil, level, resolusi, chroma, bit depth, sidik jari VPS/SPS/PPS), set topik dan skema. Target ≤100 ms per file di SSD (terukur 6–68 ms).
- **FR-102 (P0)** **Aturan sambung A→B.** Semua harus terpenuhi:
  1. folder sama;
  2. set topik video identik (cocokkan **nama topik**, bukan nomor channel);
  3. parameter codec identik (v1; parameter berbeda tetapi kompatibel → sesi terpisah);
  4. `gap = (waktu_frame_pertama_B − waktu_frame_terakhir_A) / periode` memenuhi `0,5 ≤ gap ≤ 1 + toleransi` (bawaan toleransi 1,5 frame) **dan** `gap × periode ≤ 2 dtk` (setting `session_max_gap_s`); berlaku untuk **setiap** topik video;
  5. gap < 0,5 (tumpang-tindih/duplikat) atau waktu mundur (mis. boot ulang) → terpisah, dengan alasan.
  Penanda lemah (tidak menentukan, hanya urutan tie-break dan label): nomor urut pada nama file, ukuran/durasi mirip.
- **FR-103 (P0)** Urutan dalam folder menurut **waktu mulai video**, bukan nama. Berlaku juga untuk waktu relatif sejak boot.
- **FR-104 (P0)** Hasil deteksi adalah **saran**. UI menampilkan sesi (jumlah bagian, durasi total, jumlah kamera, ikon audio) dan alasan sambung/pisah tiap sambungan; pengguna dapat **Gabung** atau **Pisah** manual. Keputusan disimpan di `sessions.json` pada folder dan menjadi sumber kebenaran.
- **FR-105 (P0)** Penamaan hasil gabungan: `{nama_pertama}~{nama_terakhir}[_{topik}].mp4` (pola dapat diubah, token `{pertama}`, `{terakhir}`, `{jumlah}`); batas panjang dan tabrakan nama mengikuti FR-16.
- **FR-106 (P0)** Setting `session_mode`: `per_file` (bawaan: perilaku v0.2) | `merged` (satu MP4 per topik per sesi). Deteksi dan tampilan sesi aktif pada kedua mode.
- **FR-107 (P0)** **Gabung saat konversi:** satu lintasan per bagian berurutan, satu penulis MP4 per topik; timeline berlanjut dengan selisih waktu nyata dipertahankan (tidak diratakan); VPS/SPS/PPS unik digabung ke `hvcC`; audio kontinu (FR-98); penulisan atomik `.partial`; co64 otomatis; verifikasi FR-30…33 pada hasil gabungan, sinkron antar-kamera pada total.
- **FR-108 (P0)** Bagian ke-2 dan seterusnya yang **tidak dimulai keyframe**: frame non-keyframe di awal dibuang dan dilaporkan (jumlah, posisi); membuangnya mungkin membuat video beku singkat di sambungan, audio tetap kontinu. Opsi `session_keep_leading` (P1) mempertahankannya bila GOP berlanjut dari bagian sebelumnya.
- **FR-109 (P0)** Kegagalan satu bagian menggagalkan sesi gabungan (status `gagal`, alasan menyebut bagian ke-k); tidak ada hasil setengah jadi. Pengguna dapat memisahkan bagian itu (FR-104) lalu mengulang. Resume: sesi selesai bila hasil gabungan lengkap menurut manifest.
- **FR-110 (P1)** `session_id` = hash daftar file berurutan. Bila sesi bertambah file (sedang direkam), hasil lama tidak ditimpa; hasil baru diberi nomor dan dilaporkan.
- **FR-111 (P0)** **Sidecar** diperluas: per topik video/audio memuat waktu pertama/terakhir, periode, jumlah frame/sampel, sidik jari codec, dan keterangan sesi (id, indeks, jumlah) — agar penjelajah file dan pemutar mengenali kontinuitas **tanpa membuka MCAP/MP4** (Lampiran C).

### 7.3 Pemutar tertanam
- **FR-120 (P0)** Tab **Pemutar** dengan mesin **libVLC 3.0.x** tertanam (`python-vlc`; `libvlc.dll` + plugin ikut paket). Dekoder software (libavcodec) bawaan sehingga jalan di mesin tanpa GPU; dekode hardware dipakai bila tersedia (setting `player_hw_decode`: `auto` | `off`). Keluaran video memilih urutan fallback (Direct3D11 → DirectDraw → GDI) agar tampil di RDP/VPS tanpa GPU.
- **FR-121 (P0)** **Tata letak otomatis:** 1 video → satu tampilan penuh; 2 video → **kiri-kanan** sama lebar (topik berurutan naik, `…/0` kiri; dapat ditukar); rasio aspek dipertahankan (letterbox). Tombol "satu kamera saja" untuk memperbesar salah satu. >2 kamera → grid (P1).
- **FR-122 (P0)** **Kontrol bersama** untuk semua kamera: Putar/Jeda, **satu seekbar** sepanjang rekaman (seluruh sesi), lompat ±5/±30 dtk, **maju satu frame**, volume/bisu, layar penuh, tampilan waktu (relatif dari awal rekaman dan, opsional, waktu perekam).
- **FR-123 (P0)** **Kecepatan putar:** 0,25×, 0,5×, 0,75×, 1×, 1,25×, 1,5×, 2×, 3×, 4× dan nilai kustom; berlaku serentak pada semua kamera dan audio (audio time-stretch tanpa mengubah nada, `scaletempo`). Di atas 2× audio dibisukan otomatis (dapat dimatikan). Kecepatan >1× pada dua kamera adalah *best effort* (FR-125).
- **FR-124 (P0)** **Sinkron dua kamera:** satu *master* (audio bila ada, jika tidak kamera pertama); pemutar lain dikoreksi bila selisih posisi > ambang (`player_sync_threshold_ms`, bawaan 40 ms ≈ 1,2 frame) lewat penyesuaian laju kecil atau seek; putar/jeda/seek/kecepatan diterapkan ke semua sekaligus. Audio hanya dari master, lainnya dibisukan. Toleransi v1: ±2 frame (bukan akurat per frame).
- **FR-125 (P0)** **Beban dekode:** indikator frame terjatuh dan ketertinggalan; bila CPU tidak mencukupi, saran otomatis: "Mode ringan" (lewati loop filter/frame non-referensi pada kamera sekunder) atau "satu kamera saja". Jumlah thread dekode = jumlah core logis.
- **FR-126 (P0)** **Playlist:** satu entri = **rekaman** (sesi atau file tunggal), bukan file; menampilkan nama, durasi total, jumlah kamera, ikon audio, jumlah bagian. Tambah/hapus, ubah urutan (seret), mode putar (berurutan / ulang / acak), otomatis lanjut. Simpan dan muat (`.m2playlist`, Lampiran E); daftar terakhir dipulihkan saat aplikasi dibuka.
- **FR-127 (P0)** **Sesi pada pemutar:** entri sesi yang belum digabung diputar sebagai satu timeline: seekbar total, pemilihan bagian + offset saat seek, bagian berikutnya **dimuat sebelum** batas (≥2 dtk) agar jeda sambungan ≤500 ms di mesin referensi (diukur di M8; bukan tanpa jeda). Bila MP4 gabungan ada, dipakai langsung dan mulus.
- **FR-128 (P1)** **Cache remux** untuk sesi multi-bagian: gabung MP4→MP4 tingkat sampel (tanpa re-encode, ±100 MB/dtk) ke folder cache (`player_cache_dir`, batas `player_cache_limit_gb`, hapus yang terlama dipakai) lalu putar hasilnya tanpa jeda sambungan.
- **FR-129 (P0)** **Penjelajah file:** panel pohon folder dan daftar di sisi kiri. Folder berisi rekaman dikelompokkan menjadi **sesi** (FR-101…104, hasil di-cache per (path, ukuran, mtime)). MP4 hasil konverter dikenali lewat sidecar dan **dipasangkan kiri-kanan** (awalan nama sama, topik berurutan); MP4 lain diputar sebagai satu kamera; `.mcap` yang belum dikonversi tampil dengan lencana "belum dikonversi" dan aksi **Konversi lalu putar** (P1). Dua klik = putar; klik kanan = tambah ke playlist, buka lokasi, salin path, lihat metadata (penampil v0.2). Seret-lepas folder/file ke jendela. Riwayat folder.
- **FR-130 (P1)** Simpan **tangkapan frame** (PNG) per kamera pada posisi sekarang.
- **FR-131 (P0)** Pintasan keyboard: Spasi putar/jeda; ←/→ ±5 dtk (Shift ±30); `[` `]` kecepatan turun/naik; `.` maju satu frame; `M` bisu; `F` layar penuh; Ctrl+O buka folder; semua dapat dilihat di bantuan (i18n).
- **FR-132 (P0)** Memutar MP4 tanpa track audio berjalan senyap tanpa kesalahan; MP4 tanpa sidecar tetap dapat diputar.
- **FR-133 (P0)** Semua teks baru lewat i18n (id/en/zh); jendela mengingat ukuran, posisi, volume, kecepatan terakhir, lebar panel.

### 7.4 Perubahan pada persyaratan lama
| FR lama | Perubahan |
|---|---|
| FR-16 penamaan | token `{pertama}`, `{terakhir}`, `{jumlah}` untuk sesi |
| FR-33 sinkron | berlaku juga pada total sesi gabungan; ditambah pemeriksaan kontinuitas audio |
| FR-40/41 sidecar | memuat jejak waktu per topik dan keterangan sesi (FR-111); lapis audio |
| FR-46 tanggal | tidak berubah; waktu relatif sejak boot tetap jatuh ke waktu modifikasi file |
| FR-51 laporan | kolom baru: sesi (id, indeks), jumlah bagian, audio (sampel, celah, offset) |
| FR-60 UI | tab Konversi, Pemutar, Metadata, Log; daftar sesi di tab Konversi |
| FR-71 audio→WAV | dipindah menjadi FR-99 (P2) |

## 8. Pengaturan baru
| Kunci | Nilai | Bawaan |
|---|---|---|
| `audio_in` | `all` / `first` / `none` | `all` |
| `audio_codec_tag` | `ipcm` / `sowt` | `ipcm` |
| `audio_gap_policy` | `fill` / `ignore` | `fill` |
| `audio_gap_tolerance_ms` | angka | 1 |
| `audio_offset_ms` | angka (±) | 0 |
| `session_mode` | `per_file` / `merged` | `per_file` |
| `session_gap_tolerance_frames` | angka | 1,5 |
| `session_max_gap_s` | angka | 2 |
| `session_name_pattern` | teks | `{pertama}~{terakhir}` |
| `session_keep_leading` | bool (P1) | `false` |
| `player_hw_decode` | `auto` / `off` | `auto` |
| `player_sync_threshold_ms` | angka | 40 |
| `player_default_speed` | angka | 1.0 |
| `player_preload_s` | angka | 2 |
| `player_cache_dir`, `player_cache_limit_gb` | path, angka (P1) | `%LOCALAPPDATA%\MCAP2MP4\cache`, 20 |
| `player_swap_cameras` | bool | `false` |

## 9. Antarmuka pemutar
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [Konversi] [Pemutar] [Metadata] [Log]                          Bahasa: [id ▾] │
├────────────────────┬─────────────────────────────────────────────┬───────────┤
│ ▸ D:\rekaman       │  ┌───────────────────┬───────────────────┐  │ Playlist  │
│   ▾ sesi_01        │  │   kamera 0        │   kamera 1        │  │ 1 ego_14… │
│     ◉ ego_0014~15  │  │   (kiri)          │   (kanan)         │  │   2 bagian│
│       2 bagian     │  │                   │                   │  │   04:10 ♪ │
│       04:10  ♪ ◫◫  │  └───────────────────┴───────────────────┘  │ 2 ego_20… │
│   ▸ sesi_02        │  ◀◀  ▶/❚❚  ▶▶ ⏭  ───────●────────  02:04/04:10│ …         │
│ ○ lepas.mp4        │  Kecepatan [1× ▾]  Vol ▮▮▮  ⛶  1 kamera       │ [↑][↓][✕] │
├────────────────────┴─────────────────────────────────────────────┴───────────┤
│ Sinkron: 0,8 frame   Terjatuh: 0%   Waktu perekam 2308,964 dtk               │
└──────────────────────────────────────────────────────────────────────────────┘
```
Alur: pilih rekaman di kiri (dua klik) → tampil di tengah; tombol **Tambah** (atau seret) memasukkan ke playlist kanan. Satu kamera → satu tampilan penuh; dua → kiri-kanan.

## 10. Persyaratan non-fungsional
| Aspek | Target |
|---|---|
| **Mesin referensi** | **Belum ditetapkan** (spesifikasi PC kantor/VPS diperlukan; §15 K6). Sandbox: 1 core, 1 stream 1080p30 ≈1,5× real-time |
| Perkiraan kasar beban | ≈0,7 core per stream 1080p30 H.265 pada kecepatan 1× → dua kamera ≈1,4 core + audio/UI; 2× ≈2,7 core; 4× ≈5,3 core. Ekstrapolasi dari satu pengukuran; **wajib diukur ulang di mesin referensi (M7/M9)** |
| Putar 1 kamera 1080p30 @1× | frame terjatuh ≤1% pada mesin referensi |
| Putar 2 kamera @1× | ≤5% terjatuh; bila tidak tercapai, "Mode ringan" tersedia dan terdokumentasi |
| Buka rekaman → frame pertama | ≤2 dtk |
| Seek acak | ≤1 dtk (GOP 30 frame = 1 dtk pada data nyata) |
| Sinkron dua kamera | selisih ≤2 frame selama 5 menit pada 1× dan 2× |
| Jeda sambungan sesi virtual | ≤500 ms |
| Memori pemutar | ≤1,5 GB untuk dua stream 1080p |
| Pindai folder 100 file (sesi) | <1 dtk jejak waktu pada SSD (FR-101) |
| Ukuran installer | ±+40–70 MB dari 35 MB sekarang (perkiraan; libVLC dipangkas) |
| Lisensi | libVLC LGPL 2.1+: sertakan teks lisensi dan catatan sumber; paket internal, bukan distribusi publik |
| Kompatibilitas MP4 | audio `ipcm`/`sowt` terjamin di VLC dan ffmpeg; pemutar bawaan Windows **tidak dijamin** (dokumentasikan; gunakan pemutar kita atau VLC) |

## 11. Arsitektur
- Modul baru: `session.py` (probe, detect, merge), `audio.py` (adaptor, deteksi celah), `mp4_writer.py` **multi-track** (video + audio, edit list, interleaving), `verify.py` (track audio), paket `player/` (`engine`, `sync`, `playlist`, `filemanager`, `widgets`).
- `mcap_reader.py`: tambah `topic_time_range` dan `chunk_index` (prototipe: `reference/prototypes/jejak_waktu_proto.py`).
- Pemutar: satu `Engine` libVLC per kamera, tertanam di widget (`set_hwnd`), `SyncController` terpisah dari UI sehingga dapat diuji dengan engine palsu.
- Pengemasan: PyInstaller menyertakan `third_party/vlc` (dari `build/fetch_vlc.py`) dan `python-vlc`; `VLC_PLUGIN_PATH` diarahkan ke sana; `vlc-cache-gen` dijalankan saat build.
- Pemisahan: konversi tetap berjalan di proses terpisah (batch); pemutar berjalan di proses GUI.
- Dependensi tetap: PySide6 `<6.10`, python-vlc `≥3.0.20`.

## 12. Kriteria penerimaan (tambahan)
Memakai `ego_0014/0015` asli dan fixture `tests/ego_synth.py`.
| ID | Kriteria |
|---|---|
| AC-20 | `ego_0014`+`ego_0015` terdeteksi **1 sesi, 2 bagian**; alasan sambung "gap 1,00 frame"; jejak waktu < 1 dtk untuk folder 100 file; waktu per kamera sama dengan pemindaian penuh |
| AC-21 | Terpisah (dengan alasan) untuk: gap 30 dtk; tumpang-tindih; waktu mundur (boot ulang); topik berbeda; resolusi berbeda; folder berbeda |
| AC-22 | Gabung: tiap kamera 1 MP4 dengan **7500 frame**, **250,000 dtk ±1 frame**, 250 keyframe; hash payload = sambungan hash sumber; `ffmpeg` mendekode area sambungan tanpa error; verifikasi dan sinkron antar-kamera lulus |
| AC-23 | Tiap MP4 punya track audio PCM 16 kHz stereo; **total sampel sesi = 4.000.000** (= 250,000 dtk); hash payload audio = sumber (mode penuh); ffprobe membaca; **VLC memutar** dan PCM hasil dekode identik |
| AC-24 | Audio kontinu: 0 hening isian, 0 peringatan `audio_gap`. Mode per-file: offset audio-video **+31,955 ms** (`ego_0014`) dan **+23,949 ms** (`ego_0015`), ±1 ms. Mode gabung: **satu** offset awal +31,955 ms dan audio melewati sambungan tanpa celah |
| AC-25 | Celah audio buatan (fixture) diisi hening sepanjang celah dan dilaporkan; format audio tak didukung tidak menggagalkan video |
| AC-26 | Kecepatan 0,25–4× bekerja; ganti kecepatan/seek/jeda berlaku ke dua kamera; selisih sinkron ≤2 frame (log internal) pada mesin referensi |
| AC-27 | Playlist: tambah, ubah urutan, hapus, otomatis lanjut, simpan/pulih setelah restart |
| AC-28 | Penjelajah file: sesi tampil sebagai satu rekaman; pasangan kamera kiri-kanan; MP4 tanpa sidecar diputar satu kamera; MP4 tanpa audio senyap |
| AC-29 | Sesi virtual (belum digabung): seekbar total benar; seek acak memuat bagian tepat; jeda sambungan ≤500 ms |
| AC-30 | Pada mesin referensi tanpa GPU: 1 kamera 1× ≤1% terjatuh; 2 kamera hasil diukur dan dicatat (target ≤5%) |
| AC-31 | Semua teks baru ada di id/en/zh; CJK tampil benar |
| AC-32 | Override manual sesi (gabung/pisah) bertahan setelah restart (`sessions.json`) |
| AC-33 | Installer baru memasang pemutar; pasang/copot bersih; tanpa libVLC yang hilang di mesin bersih |

## 13. Risiko dan mitigasi
| ID | Risiko | Mitigasi |
|---|---|---|
| R-A1 | Penulis `ipcm` buatan kita tidak diterima pemutar tertentu (uji VLC/ffmpeg dilakukan pada MP4 buatan ffmpeg) | AC-23 wajib lulus di VLC dan ffmpeg; opsi `sowt`; jalankan uji pada MP4 tulisan sendiri sebelum M5 selesai |
| R-A2 | `stamp` audio bisa berarti awal atau akhir blok → bias A/V konstan (±16 ms) | uji tepuk/klaker pada rekaman nyata; `audio_offset_ms` |
| R-A3 | Audio di setiap MP4 menggandakan ±8 MB per 125 dtk | `audio_in` |
| R-S1 | Salah gabung/pisah | aturan berbukti, alasan terlihat, override manual, mode `per_file` bawaan |
| R-S2 | Perekam lain memecah di tengah GOP | FR-108 (buang+lapor), opsi P1 |
| R-S3 | Sesi sedang tumbuh (file baru muncul) | FR-110 |
| R-P1 | CPU kantor tak kuat dua stream 1080p H.265 (sandbox 1 core hanya ±1,5×/stream) | indikator beban, mode ringan, satu kamera, ukur di mesin referensi sebelum menjanjikan |
| R-P2 | Tampilan libVLC di RDP/VPS tanpa GPU gagal | urutan fallback keluaran video; uji di VPS sungguhan |
| R-P3 | Sinkron dua pemutar tidak akurat per frame | toleransi ±2 frame didokumentasikan; frame sama dijamin oleh FR-33; mode frame-akurat P2 |
| R-P4 | Menyematkan libVLC di Qt tidak bisa diuji di sandbox Windows | uji di CI Windows/mesin nyata di M7; sandbox hanya menguji logika dengan engine palsu dan libVLC Linux |
| R-P5 | Ukuran paket dan lisensi libVLC | pangkas plugin setelah uji; sertakan lisensi |
| R-P6 | Pemutar bawaan Windows tidak memutar audio `ipcm` | dokumentasikan; gunakan pemutar kita/VLC; `sowt` bila perlu |

## 14. Milestone (kasar, orang-hari dengan bantuan AI)
| M | Isi | Hari |
|---|---|---|
| M5 | Audio: adaptor, penulis MP4 multi-track, edit list, celah, verifikasi, sidecar; uji pada file asli; AC-23…25 | 5–7 |
| M6 | Sesi: jejak waktu, deteksi, `sessions.json`, gabung saat konversi, UI sesi di tab Konversi, CLI; AC-20…22, 32 | 6–8 |
| M7 | Pemutar inti: libVLC tertanam, satu kamera, kontrol, kecepatan, tangkapan frame, paket libVLC, benchmark awal di Windows | 5–7 |
| M8 | Playlist, penjelajah file, dua kamera tersinkron, sesi virtual; AC-26…29, 31 | 8–10 |
| M9 | Pengemasan, installer, benchmark mesin referensi, dokumentasi; AC-30, 33 | 3–4 |
| M10 | Ekspor sensor (dulu M5 PRD 0.2), P2 | — |

## 15. Keputusan dan pertanyaan terbuka
**Keputusan yang diusulkan (perlu persetujuan):**
| # | Keputusan | Rekomendasi |
|---|---|---|
| K1 | Bawaan `session_mode` | `per_file` (tak merusak, resume per file, paralel); gabung dipilih sengaja. Pemutar tetap memutar sesi sebagai satu timeline |
| K2 | `audio_in` | `all` (tiap MP4 mandiri, ±6% lebih besar) |
| K3 | Sample entry audio | `ipcm` (standar ISO), `sowt` sebagai pilihan; keduanya lulus uji VLC 3.0.20 |
| K4 | Mesin pemutar | libVLC 3.0.x tertanam (sesuai permintaan "+VLC") |
| K5 | Sesi pada pemutar | v1 pemuatan berurutan dengan pra-muat (jeda ≤500 ms); cache remux mulus = P1 |
| K6 | Spesifikasi mesin referensi | **terbuka**: jumlah core/RAM/OS PC kantor dan VPS diperlukan untuk menetapkan target AC-30 |
| K7 | Lebih dari dua kamera | grid = P1; apakah ada rekaman >2 kamera belum diketahui |

## Lampiran A — Fakta data nyata
Lihat `DATA_NYATA_ego.md`.

## Lampiran B — Struktur track audio di MP4
```
trak (track_id=2)
 ├ tkhd   volume=0x0100
 ├ edts/elst  [empty edit = offset audio terhadap video (skala film 1000)] [media_time 0, durasi penuh]
 └ mdia
    ├ mdhd  timescale = laju sampel (16000), durasi = jumlah frame PCM
    ├ hdlr  'soun'
    └ minf ─ smhd, dinf, stbl
        ├ stsd  'ipcm' (AudioSampleEntry: kanal, 16 bit, laju) + chnl + pcmC(format_flags=1 little-endian, ukuran=16)
        ├ stts  [(total_frame, 1)]
        ├ stsc  run-length: 256 sampel/chunk (satu chunk per blok audio)
        ├ stsz  sample_size=4 (kanal×byte), sample_count=total_frame
        └ stco/co64  satu offset per blok
```
Catatan: struktur dikonfirmasi diterima oleh ffmpeg 6.1 dan VLC 3.0.20 pada MP4 yang ditulis ffmpeg; penulis kita harus lulus AC-23.

## Lampiran C — Tambahan sidecar (`*.metadata.json`)
```json
{
  "video": [{"topic": "/ego/camera/0", "first_time_ns": 2183963902000, "last_time_ns": 2308930574000,
             "frame_period_ns": 33333333, "frames": 3750,
             "codec_fingerprint": "hevc/main/L4.0/1920x1080/yuv420p/8bit/vps:ead097e4/sps:97c1bc1d/pps:4a139675"}],
  "audio": [{"topic": "/ego/audio/0", "first_time_ns": 2183995857000, "end_time_ns": 2308987857000,
             "sample_rate": 16000, "channels": 2, "samples": 1999872, "gaps": 0, "offset_to_video_ms": 31.955}],
  "session": {"id": "ego_0014~ego_0015", "index": 1, "count": 2}
}
```

## Lampiran D — `sessions.json` (di folder rekaman)
```json
{"version": 1,
 "sessions": [{"id": "ego_0014~ego_0015", "origin": "auto",
               "files": ["ego_0014.mcap", "ego_0015.mcap"],
               "joins": [{"from": "ego_0014.mcap", "to": "ego_0015.mcap", "gap_frames": 1.0001, "reason": "continuous"}]}],
 "overrides": {"join": [], "split": []}}
```

## Lampiran E — `.m2playlist`
```json
{"version": 1, "play_mode": "sequential",
 "items": [{"kind": "session", "title": "ego_0014~ego_0015", "duration_s": 250.0,
            "parts": [{"cameras": {"/ego/camera/0": "D:\\hasil\\ego_0014_ego_camera_0.mp4",
                                   "/ego/camera/1": "D:\\hasil\\ego_0014_ego_camera_1.mp4"}, "duration_s": 125.0},
                      {"cameras": {"/ego/camera/0": "D:\\hasil\\ego_0015_ego_camera_0.mp4",
                                   "/ego/camera/1": "D:\\hasil\\ego_0015_ego_camera_1.mp4"}, "duration_s": 125.0}]}],
 "state": {"index": 0, "position_s": 0.0, "speed": 1.0}}
```

