# Status M8 (pemutar dua kamera, playlist, penjelajah, sesi virtual)
Selesai: FR-121 (kiri-kanan, tukar, satu kamera/kiri/kanan/keduanya, letterbox), FR-124 (sinkron: master = kamera pertama, koreksi laju proporsional
atau seek; ambang `player_sync_threshold_ms`=40), FR-125 (kontrol memengaruhi kedua kamera, audio hanya master), FR-126 (playlist: entri = rekaman,
urut/ulang/acak, seret-urut, simpan/buka .m2playlist, pulih saat dibuka lagi, lanjut otomatis), FR-127 (sesi virtual: bagian berikutnya dimuat >=2 dtk
sebelum batas, tukar mulus, seekbar total), FR-129 (penjelajah: pohon folder, riwayat folder, daftar rekaman dengan durasi/kamera/audio/bagian,
badge 'belum dikonversi', menu klik-kanan, cache jejak sesi di memori).
Perubahan arsitektur: video digambar aplikasi dari frame libVLC (vmem/RV32) di FrameView, bukan jendela libVLC. Alasan: bundel libVLC Windows yang
tersedia tidak memuat plugin keluaran Direct3D/GDI; dengan vmem pemutar jalan tanpa GPU/RDP, letterbox & snapshot di tangan aplikasi. Buffer dibatasi 1280 px
lebar (`EngineOptions.max_width`). Konsekuensi: FR-120 'Direct3D11->DirectDraw->GDI' tidak dipakai; ada biaya salin frame (belum diukur di mesin referensi).
Tes: tests/test_player_sync.py (21), test_player.py (24), libVLC nyata 2 kamera: drift antar-kamera maks 1 ms rata 0,2 ms (sandbox; kedua engine satu proses),
jeda sambungan bagian 108 ms (batas 500). Belum: 'konversi lalu putar' (P1), cache jejak ke disk (FR-129 persisten), kolom favorit, ukuran/posisi jendela
(pakai bawaan GUI), pemutar di MP4 >2 kamera (hanya 2 pertama ditampilkan).

## Perbaikan v0.3.1 (laporan: "pemutar dua kamera belum berfungsi")
1. Dua MP4 kamera TANPA sidecar (sidecar dimatikan, `sidecar_dir` di folder lain, atau MP4 versi lama) tampil sebagai dua rekaman satu kamera.
   Kini dipasangkan otomatis menurut nama (`..cam..<satu digit>.mp4`, mis. `*_camera_0/1.mp4`; `ego_0014.mp4`/`ego_0015.mp4` tidak dipasangkan),
   durasi/audio dibaca dari moov, dan tombol Buka… menerima dua berkas (kiri-kanan menurut urutan pilihan).
2. Sinkron: get_time() libVLC kasar (lompat ~250 ms) sedangkan ambang 40 ms -> pengendali mengejar derau (20 koreksi laju/4 dtk, kecepatan kamera kedua
   bergoyang, selisih rata-rata terukur 104 ms). Kini posisi diinterpolasi dari saat nilai berubah (polling 5 ms): selisih nyata <=10 ms, 0 koreksi pada 1x
   (maks 8 ms), jendela tunggu 0,6 dtk setelah putar/seek. Tes nyata mensyaratkan rata-rata <40 ms dan <=2 koreksi.
3. Dekode hardware default dimatikan (vmem menyalin frame ke CPU; jalur d3d11va tak terbukti).
4. Diagnostik: kamera yang 'Playing' tanpa frame >3 dtk dilaporkan di status dan `player.log` (folder pengaturan). Selftest exe kini mewajibkan frame dari SEMUA kamera.
