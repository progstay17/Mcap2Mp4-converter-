# Prototipe referensi (bukan kode produk)

Skrip kecil yang dipakai membuktikan asumsi desain pada rekaman nyata `ego_0014.mcap` + `ego_0015.mcap`.
Hasilnya dirangkum di `docs/DATA_NYATA_ego.md`. Jalankan dengan `PYTHONPATH=src python reference/prototypes/<skrip>`.
Path file contoh di dalam skrip perlu disesuaikan.

| Skrip | Membuktikan | Dipakai di |
|---|---|---|
| `jejak_waktu_proto.py` | waktu frame pertama/terakhir per topik dari **indeks chunk** (2 chunk dibaca, 6-68 ms per file, sama dengan pemindaian penuh) | `session.probe` (FR-101) |
| `merge_sesi_proto.py` | menempel dua file jadi satu MP4 per kamera: 7500 frame, 250,000 dtk, 250 keyframe, sambungan satu periode frame, dekode bersih | gabung sesi (FR-107) |
| `audio_inspect.py` | format audio, kontinuitas, selisih audio-video, sambungan antar-file | adaptor audio (FR-90..95) |
