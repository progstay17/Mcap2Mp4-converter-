"""Pemuat bahasa (PRD §6.9). Semua teks UI/laporan/CLI diambil lewat t()."""
from __future__ import annotations
import json
import sys
from pathlib import Path

_ROOT = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
I18N_DIR = _ROOT / "i18n"
_cache: dict[str, dict] = {}
_lang = "id"
FALLBACK = "id"

def _load(lang):
    if lang not in _cache:
        f = I18N_DIR / f"{lang}.json"
        _cache[lang] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _cache[lang]

def set_language(lang: str) -> None:
    global _lang
    _lang = lang

def available() -> list[str]:
    return sorted(p.stem for p in I18N_DIR.glob("*.json"))

def t(key: str, lang: str | None = None, **kw) -> str:
    text = _load(lang or _lang).get(key) or _load(FALLBACK).get(key) or key
    return text.format(**kw) if kw else text
