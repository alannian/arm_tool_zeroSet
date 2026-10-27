from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mit_sender.rerun_feedback import _ensure_current_python_bin_on_path, _set_rerun_time_seconds


class FakeRecordingWithSetTime:
    def __init__(self) -> None:
        self.calls = []

    def set_time(self, timeline: str, *, duration: float) -> None:
        self.calls.append((timeline, duration))


class FakeRecordingWithSetTimeSeconds:
    def __init__(self) -> None:
        self.calls = []

    def set_time_seconds(self, timeline: str, seconds: float) -> None:
        self.calls.append((timeline, seconds))


class RerunFeedbackTests(unittest.TestCase):
    def test_set_rerun_time_uses_current_set_time_api(self) -> None:
        recording = FakeRecordingWithSetTime()

        _set_rerun_time_seconds(recording, "feedback_time", 1.25)

        self.assertEqual(recording.calls, [("feedback_time", 1.25)])

    def test_set_rerun_time_falls_back_to_old_set_time_seconds_api(self) -> None:
        recording = FakeRecordingWithSetTimeSeconds()

        _set_rerun_time_seconds(recording, "feedback_time", 2.5)

        self.assertEqual(recording.calls, [("feedback_time", 2.5)])

    def test_ensure_current_python_bin_on_path_prepends_rerun_bin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            python_path = Path("/usr/bin/python3")
            rerun_path = bin_dir / "rerun"
            rerun_path.write_text("")

            with patch("mit_sender.rerun_feedback.sys.executable", str(python_path)), patch(
                "mit_sender.rerun_feedback.sys.prefix", str(Path(directory))
            ), patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                _ensure_current_python_bin_on_path()
                _ensure_current_python_bin_on_path()

                path_parts = os.environ["PATH"].split(os.pathsep)

        self.assertEqual(path_parts[0], str(bin_dir))
        self.assertEqual(path_parts.count(str(bin_dir)), 1)


if __name__ == "__main__":
    unittest.main()
