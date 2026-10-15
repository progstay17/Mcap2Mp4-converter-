"""Pemutar tertanam berbasis libVLC (PRD 0.3 §6.12, FR-120..FR-133). Belum diimplementasikan (milestone M7/M8).

Modul:
    engine.py       pembungkus python-vlc: satu Engine per kamera (set_hwnd/set_xwindow, rate, seek, snapshot, statistik frame)
    sync.py         pengendali sinkron dua kamera: master clock, koreksi slave bila selisih > ambang (FR-124)
    playlist.py     model playlist (rekaman = sesi/file), simpan/muat .m2playlist, urutan, auto-next (FR-126)
    filemanager.py  penjelajah folder + pengelompokan sesi, kenali MP4 hasil konverter lewat sidecar (FR-129)
    widgets.py      tab Pemutar PySide6: tampilan 1/2 kamera, kontrol bersama, kecepatan, status
"""
