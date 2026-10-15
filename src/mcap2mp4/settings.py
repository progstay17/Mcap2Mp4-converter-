"""Pengaturan bawaan sesuai PRD §7. Nilai JSON-serializable."""
from __future__ import annotations
import copy
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
    "metadata": {"level": "full", "sidecar_json": True, "embed_native": True, "embed_exiftool": False, "embed_pairing": False},
    "filename_pattern_regex": None,
    "reports": {"formats": ["csv", "json"], "csv_delimiter": ",", "language": None},
    "tag_values": "both",               # formatted | raw | both
    "exiftool_path": None,
    "language": "id",                   # id | en | zh
    "audio_in": "all",                  # all | first | none  (FR-93)
    "audio_codec_tag": "ipcm",          # ipcm | sowt        (FR-92)
    "audio_gap_policy": "fill",         # fill | ignore      (FR-95)
    "audio_gap_tolerance_ms": 1.0,
    "audio_offset_ms": 0.0,
    "player_hw_decode": "off",          # off | auto  (vmem menyalin frame ke CPU: dekode hardware tidak menguntungkan)
    "player_volume": 80,
    "player_speed": 1.0,
    "player_mute_above_2x": True,       # FR-123
    "player_last_dir": "",
    "player_folders": [],              # riwayat folder penjelajah (FR-129)
    "player_splitter": [300, 800],    # lebar panel (FR-133)
    "player_sync_threshold_ms": 40,   # FR-124
    "session_mode": "per_file",         # per_file | merged  (FR-106)
    "session_gap_tolerance_frames": 1.5,
    "session_max_gap_s": 2.0,
    "session_name_pattern": "{pertama}~{terakhir}",             # koreksi bias perekam (FR-94)
}

def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

def load(path=None) -> dict:
    over = json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else {}
    return _merge(copy.deepcopy(DEFAULTS), over)

def save(settings: dict, path) -> None:
    Path(path).write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def user_config_path() -> Path:
    """Lokasi pengaturan pengguna: %APPDATA%\\MCAP2MP4 di Windows, ~/.config/mcap2mp4 selain itu."""
    import os
    base = os.environ.get("APPDATA")
    return (Path(base) / "MCAP2MP4" if base else Path.home() / ".config" / "mcap2mp4") / "settings.json"
