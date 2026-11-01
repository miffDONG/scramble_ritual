import json
import os
import tempfile
import unittest
from unittest import mock

from tracker import reactivision as RV
from webui import exhibit_server as X


class TrackerChoiceTest(unittest.TestCase):
    """exhibition_start.bat --tracker: an explicit choice is saved and becomes the default."""

    def setUp(self):
        self.cfg = os.path.join(tempfile.mkdtemp(), "exhibit_config.json")
        self.patch = mock.patch.object(X, "CFG_PATH", self.cfg)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_default_is_reactivision(self):
        self.assertEqual(X.ExhibitPipeline(autostart=False).rt["tracker"], "reactivision")

    def test_choice_is_remembered(self):
        X.ExhibitPipeline(autostart=False, tracker="ritualvision")
        with open(self.cfg, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["tracker"], "ritualvision")
        self.assertEqual(X.ExhibitPipeline(autostart=False).rt["tracker"], "ritualvision")
        X.ExhibitPipeline(autostart=False, tracker="reactivision")
        self.assertEqual(X.ExhibitPipeline(autostart=False).rt["tracker"], "reactivision")

    def test_test_mode_does_not_save(self):
        X.ExhibitPipeline(autostart=False, test_mode=True, tracker="ritualvision")
        self.assertEqual(X.ExhibitPipeline(autostart=False).rt["tracker"], "reactivision")

    def test_tracker_exe(self):
        with mock.patch.object(RV, "find_ritualvision", return_value="C:/x/Ritualvision.exe"), \
                mock.patch.object(RV, "find_exe", return_value="C:/y/reacTIVision.exe"):
            self.assertEqual(RV.tracker_exe({"tracker": "ritualvision"}), ("ritualvision", "C:/x/Ritualvision.exe"))
            self.assertEqual(RV.tracker_exe({"tracker": "reactivision"}), ("reactivision", "C:/y/reacTIVision.exe"))
            self.assertEqual(RV.tracker_exe({"tracker": "bogus"})[0], "reactivision")

    def test_ritualvision_has_no_mode_listing(self):
        with mock.patch.object(RV.subprocess, "run") as run:
            out = RV.list_modes("C:/x/Ritualvision.exe")
        run.assert_not_called()
        self.assertEqual(out["cameras"], [])
        self.assertIsNone(out["error"])


if __name__ == "__main__":
    unittest.main()
