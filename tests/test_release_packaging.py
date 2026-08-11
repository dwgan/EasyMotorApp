import re
import unittest
from pathlib import Path

from easymotor.version import VERSION, __version__, window_title


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_source_version_is_numeric_and_visible_in_window_title(self):
        self.assertRegex(__version__, re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)?$"))
        self.assertEqual(__version__, ".".join(str(part) for part in VERSION))
        self.assertEqual(window_title(), f"EasyMotor v{__version__}")

    def test_release_script_builds_one_file_with_icon_and_version_metadata(self):
        script = (PROJECT_ROOT / "build_easymotor_release.ps1").read_text(
            encoding="utf-8-sig"
        )
        for required in (
            '"--onefile"',
            '"--windowed"',
            '"--icon"',
            '"--version-file"',
            '"--runtime-hook"',
            '"--add-data"',
            "StringStruct('FileVersion'",
            "StringStruct('ProductVersion'",
            "StringStruct('ProductName'",
            "StringStruct('FileDescription'",
            "StringStruct('CompanyName'",
        ):
            with self.subTest(required=required):
                self.assertIn(required, script)
        self.assertNotIn('"--onedir"', script)
        self.assertIn("$invalidVersionParts = @(", script)
        self.assertIn("if ($invalidVersionParts.Count -gt 0)", script)

    def test_batch_launcher_delegates_to_powershell_builder(self):
        launcher = (PROJECT_ROOT / "build_easymotor_release.bat").read_text(
            encoding="utf-8"
        )
        self.assertIn("build_easymotor_release.ps1", launcher)
        self.assertIn("%*", launcher)


if __name__ == "__main__":
    unittest.main()
