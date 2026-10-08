"""CLI (PRD FR-65). Kode keluar: 0 sukses, 1 ada gagal, 2 ada perlu-perhatian (tidak lengkap/tidak sinkron), 64 argumen/lokasi tidak valid."""
from __future__ import annotations

import argparse
import sys
from fractions import Fraction
from pathlib import Path

from . import __version__, batch, i18n, report, settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcap2mp4", description="MCAP -> MP4 (remux H.265, tanpa re-encode)")
    p.add_argument("--src", help="folder sumber lokal")
    p.add_argument("--dst", help="folder output")
    p.add_argument("--mode", choices=["folder", "beside", "ask"], help="lokasi output (bawaan: folder)")
    p.add_argument("--flat", action="store_true", help="mode folder tanpa mencerminkan struktur sumber")
    p.add_argument("--settings", help="file settings.json (nilai CLI menimpa)")
    p.add_argument("--reports-dir")
    p.add_argument("--sidecar-dir")
    p.add_argument("--temp-dir", help="folder sementara (harus satu volume dengan output)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--channels", nargs="+", help="hanya topik ini (bawaan: semua channel video)")
    p.add_argument("--name-pattern", help="mis. {nama}_{topik}_{tanggal}")
    p.add_argument("--on-name-collision", choices=["number", "fail"])
    p.add_argument("--on-exists", choices=["skip", "number"], help="bila keluaran sebagian sudah ada")
    p.add_argument("--fps-mode", choices=["timestamp", "session", "fraction", "integer"])
    p.add_argument("--fps", help="mis. 30, 29.97 atau 30000/1001 (menyetel mode fraction bila --fps-mode tak diberikan)")
    p.add_argument("--co64", choices=["auto", "always", "off"])
    p.add_argument("--sync", choices=["strict", "warn", "trim"])
    p.add_argument("--verify", choices=["off", "quick", "full"])
    p.add_argument("--workers", type=int)
    p.add_argument("--no-sidecar", action="store_true", help="jangan tulis metadata .json")
    p.add_argument("--exiftool", help="path exiftool.exe (bawaan: yang disertakan)")
    p.add_argument("--reports", nargs="+", choices=["csv", "json", "xlsx", "html"], help="format laporan")
    p.add_argument("--csv-delimiter", choices=[",", ";"])
    p.add_argument("--report-lang", choices=["id", "en", "zh"])
    p.add_argument("--lang", choices=["id", "en", "zh"])
    p.add_argument("--dry-run", action="store_true", help="pindai dan tampilkan rencana tanpa menulis")
    p.add_argument("-v", "--verbose", action="store_true", help="tampilkan detail teknis kesalahan")
    p.add_argument("--version", action="version", version=f"mcap2mp4 {__version__}")
    return p


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def apply_args(a, s: dict) -> dict:
    m = {"source_dir": a.src, "output_dir": a.dst, "output_mode": a.mode, "reports_dir": a.reports_dir,
         "sidecar_dir": a.sidecar_dir, "temp_dir": a.temp_dir, "recursive": a.recursive, "channels": a.channels,
         "name_pattern": a.name_pattern, "on_name_collision": a.on_name_collision, "on_exists": a.on_exists,
         "fps_mode": a.fps_mode, "co64": a.co64, "verify": a.verify, "workers": a.workers,
         "exiftool_path": a.exiftool, "language": a.lang}
    for k, v in m.items():
        if v is not None:
            s[k] = v
    if a.flat:
        s["mirror_structure"] = False
    if a.sync:
        s["sync"]["policy"] = a.sync
    if a.no_sidecar:
        s["metadata"]["sidecar_json"] = False
    if a.reports:
        s["reports"]["formats"] = a.reports
    if a.csv_delimiter:
        s["reports"]["csv_delimiter"] = a.csv_delimiter
    if a.report_lang:
        s["reports"]["language"] = a.report_lang
    if a.fps:
        try:
            if Fraction(a.fps) <= 0:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            raise batch.BatchError("bad_fps")
        s["fps_value"] = a.fps
        if a.fps_mode is None:
            s["fps_mode"] = "fraction"
    if s["fps_mode"] in ("fraction", "integer") and not s.get("fps_value"):
        raise batch.BatchError("bad_fps")
    if s["fps_mode"] == "integer":
        s["fps_value"] = int(Fraction(str(s["fps_value"])))
    return s


def _err_text(exc: batch.BatchError) -> str:
    key = f"msg.{exc.code}"
    return i18n.t(key, **exc.kw) if i18n.t(key) != key else exc.code


def describe_result(r, verbose=False) -> str:
    """Teks ringkas berbahasa UI untuk satu hasil (dipakai CLI dan GUI)."""
    t = i18n.t
    if r["status"] == "success":
        o = r["outputs"]
        return t("cli.ok_detail", n=len(o), frames="+".join(str(x.get("frames")) for x in o),
                 secs=r.get("elapsed_s", 0), mbs=r.get("mb_per_s", 0))
    if r["status"] == "skipped":
        return ""
    code = r.get("error_code")
    txt = ": " + t(f"err.{code}") if code else ""
    sy = r.get("sync")
    if code == "out_of_sync" and sy and sy.get("reason"):
        mm = sy.get("first_mismatch") or {}
        txt += " (" + t("sync." + sy["reason"], counts=sy.get("counts"), index=mm.get("index")) + ")"
    if verbose and r.get("error"):
        txt += "\n    " + t("cli.verbose_detail", text=r["error"])
    return txt


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = build_parser().parse_args(argv)
    if not a.src:
        build_parser().error("--src wajib")
    try:
        s = apply_args(a, settings.load(a.settings))
        i18n.set_language(s.get("language") or "id")
        if s["output_mode"] == "ask":
            if not sys.stdin or not sys.stdin.isatty():
                raise batch.BatchError("output_required")
            ans = input(i18n.t("cli.ask_output")).strip().strip('"')
            if not ans:
                raise batch.BatchError("output_required")
            s["output_dir"], s["output_mode"] = ans, "folder"
        t = i18n.t
        if a.dry_run:
            rows = batch.dry_run(s)
            print(t("cli.dry_run"))
            for r in rows:
                outs = ", ".join(Path(o["path"]).name for o in r["outputs"])
                print(f"{t('status.' + r['status']):<14} {r['source']}  {outs}" + (describe_result(r, a.verbose) if r["status"] == "failed" else ""))
            print(t("cli.found", n=len(rows), size=_fmt_size(sum(r["source_bytes"] for r in rows))))
            return 0
        entries = batch.scan(s)
        if not entries:
            print(t("cli.nothing"))
            return 0
        print(t("cli.found", n=len(entries), size=_fmt_size(sum(e.size for e in entries))))

        def on_event(kind, data):
            if kind == "warning":
                code, kw = data
                print(t(f"msg.{code}", **kw))
            elif kind == "done":                      # satu baris per file agar tidak tercampur antar-worker
                i, n, r = data
                print(t("cli.start", i=i + 1, n=n, name=r["source"]) + t("cli.done", status=t(f"status.{r['status']}"),
                                                                          detail=describe_result(r, a.verbose)), flush=True)

        try:
            results, summary = batch.run(s, on_event)
        except KeyboardInterrupt:
            print("\n" + t("cli.cancelled"))
            return 130
        rep = s["reports"]
        paths = report.export(results, summary, batch.reports_base(s) / "reports", rep["formats"],
                              rep["csv_delimiter"], rep.get("language") or s["language"])
        c = summary["counts"]
        print(t("cli.summary", success=c["success"], skipped=c["skipped"], incomplete=c["incomplete"],
                out_of_sync=c["out_of_sync"], failed=c["failed"], size=_fmt_size(summary["total_source_bytes"]),
                secs=summary["elapsed_s"]))
        for p in paths:
            print(t("cli.reports", path=p))
        return batch.exit_code(summary)
    except batch.BatchError as exc:
        print(_err_text(exc), file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
