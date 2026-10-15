"""'Konversi lalu putar' (FR-129 P1): ubah rekaman MCAP yang belum dikonversi menjadi MP4 di <folder>/mp4, lalu kembalikan Recording
yang dapat diputar. Tanpa Qt; dijalankan di thread latar oleh tab Pemutar. Hasil lama dipakai ulang bila sumbernya sama."""
from __future__ import annotations

from pathlib import Path

from .library import scan_folder
from .model import Recording

AUTOCONVERT_DIR = "mp4"


def _find(out: Path, source_files) -> Recording | None:
    want = set(source_files)
    for r in scan_folder(out, detect_mcap=False):
        if r.playable and set(r.source_files) == want:
            return r
    return None


def convert_recording(rec: Recording, settings_dict: dict | None = None, out_dir=None):
    """Kembalikan (ok, Recording | None, pesan). Tidak melempar galat konversi: kegagalan dikembalikan sebagai pesan."""
    from .. import session as sessmod, settings as setmod, worker
    try:
        folder = Path(rec.folder)
        files = list(rec.source_files)
        srcs = [folder / f for f in files]
        if not files or not all(p.exists() for p in srcs):
            return False, None, "berkas MCAP sumber tidak ditemukan"
        out = Path(out_dir) if out_dir else folder / AUTOCONVERT_DIR
        done = _find(out, files) if out.is_dir() else None
        if done is not None:
            return True, done, "sudah dikonversi"
        s = setmod.load()
        s.update(settings_dict or {})
        s["metadata"] = dict(s.get("metadata") or {}, sidecar_json=True)        # pemutar memasangkan kamera lewat sidecar
        s["verify"] = "quick"
        n = len(srcs)
        stem = sessmod.session_stem(files, s.get("session_name_pattern")) if n > 1 else Path(files[0]).stem
        task = {"src": str(srcs[0]), "srcs": [str(p) for p in srcs] if n > 1 else None, "rel": files[0], "out_dir": str(out),
                "stem": stem, "settings": s,
                "session": {"id": rec.title, "index": 1, "count": n, "merged": True, "parts": files} if n > 1 else None}
        res = worker.convert_file(task)
        if not res.get("outputs"):
            return False, None, res.get("error") or res.get("status") or "konversi gagal"
        got = _find(out, files)
        if got is None:
            return False, None, "hasil konversi tidak ditemukan"
        return True, got, res.get("status", "success")
    except Exception as exc:                                                    # jangan jatuhkan GUI
        return False, None, f"{type(exc).__name__}: {exc}"
