# Status M6 (sesi dan penggabungan)
Selesai: FR-100..FR-111 (tanpa GUI), FR-98 (audio kontinu lintas bagian), AC-20, AC-20b, AC-21, AC-22. Tes: tests/test_session.py (8 tes); suite 77 tes hijau, 9 dilewati (M7-M8).

Cara pakai (CLI):
  mcap2mp4 --src FOLDER --list-sessions            lihat sesi + alasan sambung/pisah
  mcap2mp4 --src FOLDER --dst OUT --session-mode merged     satu MP4 per topik per sesi (nama: {pertama}~{terakhir}_{topik}.mp4)
  mcap2mp4 --join A.mcap B.mcap | --split A.mcap B.mcap | --clear-override A.mcap B.mcap    keputusan manual -> sessions.json di folder itu
Bawaan tetap per_file (perilaku v0.2); sidecar per file kini membawa session {id,index,count}.

Aturan sambung (deterministik, FR-102): folder sama, nama topik video identik, sidik jari codec identik (profil/level/resolusi/hash VPS-SPS-PPS),
gap antar-bagian 0,5 s/d 1+1,5 frame dan <= 2 dtk untuk SETIAP topik video. Alasan pisah: different_folder, topics_differ, codec_differs,
overlap (<0,5 frame), time_backwards (<0), gap_too_large, period_unknown.
Jejak waktu dari indeks chunk (<=4 chunk dibaca per file pada uji 47 chunk).

Penggabungan: satu lintasan, satu Mp4Writer per topik; VPS/SPS/PPS unik digabung ke hvcC; awal bagian berikutnya yang bukan keyframe dibuang dan
dilaporkan (session_leading_dropped, dropped_seam_frames); bagian yang rusak menggagalkan seluruh sesi dan menyebut nama bagiannya, tanpa file setengah jadi.
Verifikasi penuh membandingkan hash dengan sambungan frame sumber; sync antar-kamera dan audio dihitung pada total sesi.

Belum: UI sesi di GUI (PySide6 tidak ada di sandbox, jadi tidak bisa diuji di sini), uji pada ego_0014/0015 asli, cache jejak (M8, FR-129),
opsi 'pertahankan non-keyframe awal' (P1). Catatan: urutan sesi mengikuti waktu mulai video, jadi file hasil boot ulang yang waktunya mundur
muncul sebagai sesi terpisah dengan alasan gap_too_large (atau time_backwards bila tumpang-tindih sebagian).
