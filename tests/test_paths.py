import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from umabot.paths import data_path


class DataPathTests(unittest.TestCase):
    def test_configured_directory_takes_precedence_for_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "uma-alerts.sqlite3"
            (root / name).touch()
            with (
                patch("umabot.paths.PROJECT_ROOT", root),
                patch.dict(os.environ, {"UMABOT_DATA_DIR": str(root / "persistent")}),
            ):
                self.assertEqual(data_path(name), root / "persistent" / name)
                self.assertEqual(data_path("uma-feedback.sqlite3"), root / "persistent" / "uma-feedback.sqlite3")

    def test_unset_directory_keeps_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "uma-alerts.sqlite3"
            (root / name).touch()
            with patch("umabot.paths.PROJECT_ROOT", root), patch.dict(os.environ, {"UMABOT_DATA_DIR": ""}):
                self.assertEqual(data_path(name), root / name)
                self.assertEqual(data_path("uma-feedback.sqlite3"), root / "data" / "uma-feedback.sqlite3")

    def test_relative_directory_is_rejected(self):
        with patch.dict(os.environ, {"UMABOT_DATA_DIR": "relative/path"}):
            with self.assertRaisesRegex(ValueError, "absolute path"):
                data_path("uma-alerts.sqlite3")
