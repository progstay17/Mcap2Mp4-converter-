# Status M5 (audio di MP4)
Selesai: FR-90..97 (kecuali FR-98 lintas-bagian = M6). Tes: tests/test_audio.py (11 tes) + seluruh suite hijau.
Terbukti: ffmpeg membaca track ipcm/sowt tulisan sendiri, PCM hasil dekode identik dengan sumber, offset A/V 32 ms/8 ms di edit list,
celah diisi hening + peringatan audio_gap, tumpang-tindih dipotong, format tak didukung tidak menggagalkan video, audio_in all/first/none.
VLC 3.0.20 (Linux): memutar ipcm dan sowt dan PCM identik pada seluruh isi kecuali ekor +-16 ms yang juga hilang pada MP4 kontrol buatan ffmpeg
(artefak transcode-ke-file VLC, bukan penulis kita).
Belum: uji pada ego_0014/0015 asli, uji tepuk/klaker (R-A2), opsi audio di GUI (kunci i18n gui.audio_in sudah ada), PySide6 tidak ada di sandbox (gui_smoke tidak dijalankan).
Catatan desain: trim sync + audio meninggalkan byte yatim di mdat (verify longgar untuk file beradio); chnl hanya ditulis untuk 1-2 kanal.
