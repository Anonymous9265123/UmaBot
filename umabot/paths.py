"""Resolve project and runtime paths independently of the working directory."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_path(name):
    data_dir = os.getenv("UMABOT_DATA_DIR", "").strip()
    if data_dir:
        directory = Path(data_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError("UMABOT_DATA_DIR must be an absolute path.")
        return directory / name

    path = PROJECT_ROOT / "data" / name
    legacy_path = PROJECT_ROOT / name
    if not path.exists() and legacy_path.exists():
        return legacy_path
    return path
