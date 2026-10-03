"""CLI (PRD FR-65). Kode keluar: 0 sukses, 1 ada gagal, 2 ada perlu-perhatian/tidak sinkron, 3 belum diimplementasikan."""
from __future__ import annotations
import argparse
from . import settings, i18n

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcap2mp4", description="MCAP -> MP4 (remux H.265, tanpa re-encode)")
    p.add_argument("--src", required=True, help="folder sumber lokal")
    p.add_argument("--dst", help="folder output")
    p.add_argument("--mode", choices=["folder", "beside", "ask"], default="folder", help="lokasi output")
    p.add_argument("--reports-dir")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--fps-mode", choices=["timestamp", "session", "fraction", "integer"], default="timestamp")
    p.add_argument("--fps")
    p.add_argument("--co64", choices=["auto", "always", "off"], default="auto")
    p.add_argument("--sync", choices=["strict", "warn", "trim"], default="strict")
    p.add_argument("--verify", choices=["off", "quick", "full"], default="quick")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--lang", choices=["id", "en", "zh"], default="id")
    p.add_argument("--dry-run", action="store_true")
    return p

def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    i18n.set_language(a.lang)
    s = settings.load()
    s.update(source_dir=a.src, output_dir=a.dst or "", output_mode=a.mode, recursive=a.recursive,
             co64=a.co64, verify=a.verify, workers=a.workers, language=a.lang, fps_mode=a.fps_mode)
    s["sync"]["policy"] = a.sync
    print(i18n.t("msg.not_implemented"))
    return 3

if __name__ == "__main__":
    raise SystemExit(main())
