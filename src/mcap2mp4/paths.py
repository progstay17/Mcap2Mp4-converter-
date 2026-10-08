"""Bantuan path Windows: jalur panjang (>260 karakter) lewat awalan \\\\?\\ (PRD §8)."""
from __future__ import annotations

import os
from pathlib import Path


def lp(path) -> str:
    s = os.path.abspath(str(path))
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def is_network(path) -> bool:
    s = os.path.abspath(str(path))
    if s.startswith("\\\\") and not s.startswith("\\\\?\\"):
        return True
    if os.name == "nt":
        try:
            import ctypes
            drive = os.path.splitdrive(s)[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4      # DRIVE_REMOTE
        except Exception:
            return False
    return False


def is_inside(child, parent) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False
