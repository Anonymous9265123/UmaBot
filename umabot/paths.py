"""Resolve project and runtime paths independently of the working directory."""

import logging
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)


def data_path(name):
    data_dir = os.getenv("UMABOT_DATA_DIR", "").strip()
    if data_dir:
        directory = Path(data_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError("UMABOT_DATA_DIR must be an absolute path.")
        return directory / name

    return PROJECT_ROOT / "data" / name


def runtime_database_path(name):
    path = data_path(name)
    legacy = PROJECT_ROOT / name
    if not path.exists() and legacy.is_file():
        log.warning(
            "UmaBot will use %s. Existing database %s requires a manual copy. "
            "See README.md under 'Migrate an older alert database'.",
            path, legacy,
        )
    return path


def unnumbered_alert_databases():
    name = "uma-alerts.sqlite3"
    candidates = dict.fromkeys((data_path(name), PROJECT_ROOT / "data" / name, PROJECT_ROOT / name))
    return [path for path in candidates if path.is_file()]
