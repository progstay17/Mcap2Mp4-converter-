# Catatan rilis

## 0.3.2
- Kredit "Fadhil Ghifarion 法迪 for AIT": label kecil + tombol Tentang di GUI (dialog memuat pemberitahuan libVLC/Qt/ExifTool), `mcap2mp4-cli --version`,
  sumber daya versi exe GUI/CLI/installer (CompanyName, LegalCopyright), Publisher di Tambah/Hapus Program, branding wizard installer.
- Konversi lalu putar: rekaman MCAP yang belum dikonversi (klik ganda / klik kanan di penjelajah) dikonversi di thread latar ke `<folder>/mp4`
  (sesi >=2 file digabung), lalu langsung diputar. Hasil lama dipakai ulang; daftar tidak lagi menampilkan 'belum dikonversi' untuk sumber yang sudah ada hasilnya.
- Opsi penanda pasangan kamera di dalam MP4 (`metadata.embed_pairing`, CLI `--embed-pairing`, kotak centang GUI; BAWAAN MATI): kotak `moov/udta/m2gp`
  (JSON: ID grup, indeks & topik kamera, nama pasangan, sesi, sumber). Pemutar memasangkan kamera lewat penanda ini sebelum sidecar/nama berkas,
  sehingga tahan sidecar hilang dan berkas diganti nama. Payload video/audio identik dengan tanpa penanda.
- Perbaikan kecil: kunci i18n `gui.about` (judul jendela) tidak lagi bentrok dengan teks tombol.
- Tidak termasuk: libVLC resmi terbaru (videolan.org tidak terjangkau dari lingkungan build; `build/prepare_vlc.py <folder VLC>` siap dipakai).

## 0.3.1
Pemutar dua kamera: pemasangan kamera berdasarkan nama bila sidecar tidak ada, pilih 2 berkas manual, sinkron memakai posisi terinterpolasi (tanpa koreksi palsu), dekode hardware default mati, diagnostik `player.log`.

## 0.3.0
M5 audio, M6 sesi/penggabungan, M7-M8 pemutar (1-2 kamera, playlist, penjelajah, sesi virtual), M9 exe + installer NSIS.
