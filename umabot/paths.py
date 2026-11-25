"""Resolve project and runtime paths independently of the working directory."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_path(name):
    path = PROJECT_ROOT / "data" / name
    legacy_path = PROJECT_ROOT / name
    if not path.exists() and legacy_path.exists():
        return legacy_path
    return path
