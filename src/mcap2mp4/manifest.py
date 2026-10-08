"""manifest.jsonl bertahap (PRD FR-50, FR-20, FR-21). Status final ditulis paling akhir.

Satu baris JSON per kejadian; rekaman terakhir per sumber menang. Status 'running' yang tidak
diikuti status final berarti proses terhenti di tengah jalan (resume memprosesnya lagi).
Kode status netral bahasa (FR-82): running | success | skipped | incomplete | out_of_sync | failed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FINAL_OK = ("success", "skipped")


class Manifest:
    def __init__(self, path):
        self.path = Path(path)
        self.latest: dict[str, dict] = {}
        self.load()

    def load(self):
        self.latest = {}
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue                             # baris terakhir bisa terpotong saat mati listrik
                if "source" in rec:
                    self.latest[rec["source"]] = rec

    def append(self, record: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.latest[record["source"]] = record

    def is_done(self, source_key: str) -> bool:
        """True bila manifest mencatat selesai DAN semua keluaran masih ada dengan ukuran yang sama."""
        rec = self.latest.get(source_key)
        if not rec or rec.get("status") not in FINAL_OK:
            return False
        outs = rec.get("outputs") or []
        if not outs:
            return False
        for o in outs:
            p = Path(o["path"])
            try:
                if p.stat().st_size != o["bytes"]:
                    return False
            except OSError:
                return False
        return True
