"""Pengaturan bawaan sesuai PRD §7. Nilai JSON-serializable."""
from __future__ import annotations
import json
from pathlib import Path

DEFAULTS = {
    "source_dir": "", "recursive": True, "channels": "all",
    "output_mode": "folder",            # folder | beside | ask
    "output_dir": "", "mirror_structure": True,
    "sidecar_dir": None, "reports_dir": None, "temp_dir": None,   # None = ikut folder keluaran
    "name_pattern": None,               # None = {nama} / {nama}_{topik}
    "on_name_collision": "number",      # number | fail
    "fps_mode": "timestamp",            # timestamp | session | fraction | integer
    "fps_value": None,
    "co64": "auto",                     # auto | always | off
    "on_exists": "skip",                # skip | number
    "sync": {"policy": "strict", "tolerance_frames": 0.5},   # strict | warn | trim
    "workers": 2, "ram_limit_mb": 512,
    "verify": "quick",                  # off | quick | full
    "after_success": "keep",            # keep | move | delete
    "faststart": False,
    "metadata": {"level": "full", "sidecar_json": True, "embed_native": True, "embed_exiftool": False},
    "filename_pattern_regex": None,
    "reports": {"formats": ["csv", "json"], "csv_delimiter": ",", "language": None},
    "tag_values": "both",               # formatted | raw | both
    "exiftool_path": None,
    "language": "id",                   # id | en | zh
}

def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

def load(path=None) -> dict:
    over = json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else {}
    return _merge(DEFAULTS, over)

def save(settings: dict, path) -> None:
    Path(path).write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
