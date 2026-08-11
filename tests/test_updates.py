import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easymotor.updates.client import GitHubReleaseClient, UpdateCancelled, UpdateError, validate_download_url
from easymotor.updates.contract import UpdateManifest, parse_version
from easymotor.updates.installer import (
    HEALTH_ARGUMENT,
    _HELPER_SCRIPT,
    health_marker_from_argv,
)
from easymotor.updates.pe import read_pe_machine


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses

    def open(self, request, timeout=None):
        del timeout
        return FakeResponse(self.responses[request.full_url])


def manifest_payload(data: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "product": "EasyMotor",
        "version": "1.2.3",
        "architecture": "win-x64",
        "asset_name": "EasyMotor_v1.2.3_win-x64.exe",
        "asset_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "published_at": "2026-08-12T00:00:00Z",
    }


class UpdateContractTests(unittest.TestCase):
    def test_version_comparison_is_numeric_and_strict(self):
        self.assertGreater(parse_version("v1.10.0"), parse_version("1.2.99"))
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3, 0))
        for invalid in ("1.2", "1.2.x", "1.2.3-beta", "1.2.70000"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_version(invalid)

    def test_manifest_rejects_wrong_product_name_or_asset(self):
        payload = manifest_payload(b"exe")
        UpdateManifest.from_json(payload)
        for key, value in (("product", "Other"), ("asset_name", "setup.exe")):
            broken = dict(payload)
            broken[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                UpdateManifest.from_json(broken)

    def test_url_allowlist_rejects_http_credentials_and_foreign_hosts(self):
        validate_download_url("https://github.com/dwgan/EasyMotor/releases/latest")
        for invalid in (
            "http://github.com/dwgan/EasyMotor",
            "https://example.com/update.exe",
            "https://token@github.com/update.exe",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(UpdateError):
                validate_download_url(invalid)


class GitHubReleaseClientTests(unittest.TestCase):
    def _client_and_release(self, executable=b"MZ-test-update"):
        manifest = json.dumps(manifest_payload(executable)).encode("utf-8")
        manifest_url = "https://github.com/dwgan/EasyMotor/releases/download/v1.2.3/easymotor-update.json"
        executable_url = "https://github.com/dwgan/EasyMotor/releases/download/v1.2.3/EasyMotor_v1.2.3_win-x64.exe"
        latest_url = "https://api.github.com/repos/dwgan/EasyMotor/releases/latest"
        release = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/dwgan/EasyMotor/releases/tag/v1.2.3",
            "body": "Safe release",
            "assets": [
                {
                    "name": "easymotor-update.json",
                    "state": "uploaded",
                    "size": len(manifest),
                    "digest": "sha256:" + hashlib.sha256(manifest).hexdigest(),
                    "browser_download_url": manifest_url,
                },
                {
                    "name": "EasyMotor_v1.2.3_win-x64.exe",
                    "state": "uploaded",
                    "size": len(executable),
                    "digest": "sha256:" + hashlib.sha256(executable).hexdigest(),
                    "browser_download_url": executable_url,
                },
            ],
        }
        opener = FakeOpener(
            {
                latest_url: json.dumps(release).encode("utf-8"),
                manifest_url: manifest,
                executable_url: executable,
            }
        )
        client = GitHubReleaseClient(opener=opener)
        return client, client.fetch_latest(), executable

    def test_fetches_manifest_and_requires_matching_github_digest(self):
        _client, release, executable = self._client_and_release()
        self.assertEqual(release.version, "1.2.3")
        self.assertEqual(release.manifest.asset_size, len(executable))
        self.assertTrue(release.is_newer_than("1.2.2"))

    def test_download_is_atomic_and_hash_checked(self):
        client, release, executable = self._client_and_release()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / release.manifest.asset_name
            result = client.download(release, destination)
            self.assertEqual(result.read_bytes(), executable)
            self.assertFalse(destination.with_suffix(".exe.part").exists())

    def test_download_rejects_hash_mismatch_without_replacing_destination(self):
        client, release, _executable = self._client_and_release()
        client.opener.responses[release.download_url] = b"tampered bytes"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / release.manifest.asset_name
            destination.write_bytes(b"existing safe file")
            with self.assertRaises(UpdateError):
                client.download(release, destination)
            self.assertEqual(destination.read_bytes(), b"existing safe file")

    def test_cancelled_download_removes_partial_file(self):
        client, release, _executable = self._client_and_release()
        cancel = mock.Mock()
        cancel.is_set.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / release.manifest.asset_name
            with self.assertRaises(UpdateCancelled):
                client.download(release, destination, cancel=cancel)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".exe.part").exists())


class InstallerSafetyTests(unittest.TestCase):
    def test_pe_machine_parser_accepts_x64_header(self):
        image = bytearray(256)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 0x3C, 0x80)
        image[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", image, 0x84, 0x8664)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.exe"
            path.write_bytes(image)
            self.assertEqual(read_pe_machine(path), 0x8664)

    def test_health_marker_must_stay_under_local_update_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                valid = Path(directory) / "EasyMotor" / "updates" / "healthy.ok"
                self.assertEqual(
                    health_marker_from_argv([HEALTH_ARGUMENT, str(valid)]), valid.resolve()
                )
                self.assertIsNone(
                    health_marker_from_argv([HEALTH_ARGUMENT, str(Path(directory) / "outside.ok")])
                )

    def test_helper_probes_permissions_before_moving_target(self):
        self.assertLess(_HELPER_SCRIPT.index("Set-Content -LiteralPath $probe"), _HELPER_SCRIPT.index("Move-Item -LiteralPath $Target"))
        self.assertIn("-Verb RunAs", _HELPER_SCRIPT)
        self.assertIn("previous version was restored", _HELPER_SCRIPT)


if __name__ == "__main__":
    unittest.main()
