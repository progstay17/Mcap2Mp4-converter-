# Status M7 (pemutar inti, satu kamera)
Selesai: FR-120 (libVLC tertanam via python-vlc, dimuat malas; dekode hardware auto/off; urutan fallback vout Windows), FR-122 (play/jeda, seekbar,
+-5/+-30, maju satu frame, volume/bisu, layar penuh, waktu), FR-123 (0,25x..4x + kustom, bisu otomatis >2x), FR-130 (tangkapan PNG), FR-131 (pintasan),
FR-132 (MP4 tanpa audio/sidecar), FR-133 (i18n id/en/zh; volume, kecepatan, folder terakhir diingat). Tab Pemutar ada di GUI.

Arsitektur: `engine.py` (pembungkus libVLC, tanpa Qt) -> `controller.py` (logika perintah murni, menerima 1..N engine; dipakai M8) -> `widgets.py` (PySide6).
Jika libVLC tidak ada: tab menampilkan pesan, konversi tetap normal. Seret-lepas MP4 ke tab untuk membuka.

Tes: tests/test_player.py (24 tes): logika murni, Controller dengan engine palsu (tests/fake_engine.py), libVLC NYATA headless memutar MP4 tulisan converter
(dengan/tanpa audio, kecepatan 2x/4x, seek, jeda, maju frame, sampai selesai), PlayerTab offscreen (PySide6 6.12).
gui_smoke (offscreen) lulus dengan tab baru.

Benchmark awal (build/bench_player.py; SANDBOX 1 core logis, klip sintetis 1080p30 H.265 derau, dekode software, keluaran dummy):
  1 stream: 1x = 0,75 core, 0 frame jatuh | 2x = 0,97 core, 0 jatuh, selesai 5,3 dtk | 4x = 0,98 core, selesai 3,2 dtk (CPU penuh, ketinggalan dari 2,5 dtk ideal)
  2 stream: 1x = 0,99 core total (0,5/stream), 0 jatuh, selesai 10,4 dtk | 2x = 6,3 dtk (ideal 5) | 4x = 6,2 dtk (ideal 2,5): tidak terkejar pada 1 core
Angka 0,75 core/stream pada 1x searah dengan perkiraan PRD (0,7). Ini BUKAN mesin referensi dan klipnya bukan rekaman asli: wajib diulang di Windows (M9).

Belum / tidak bisa diuji di sini:
 - Windows: keluaran video Direct3D11/DirectDraw/GDI (nama modul VLC di vout_candidates perlu dikonfirmasi), set_hwnd pada layar penuh, RDP/VPS tanpa GPU.
 - build/fetch_vlc.py tidak bisa mengunduh dari VideoLAN di sandbox; spec PyInstaller sudah menyalin third_party/vlc bila ada, ukuran paket belum diukur.
 - Tangkapan PNG nyata butuh vout sungguhan (di sandbox hanya dummy): logika diuji dengan engine palsu, jalur libVLC video_take_snapshot belum teruji.
 - Dekode non-keyframe/seek presisi: VLC mendarat dalam kisaran 1 GOP (1 dtk pada data uji), bukan akurat per frame.
Berikutnya M8: sinkron dua kamera (player/sync.py), playlist, penjelajah file, sesi virtual.
