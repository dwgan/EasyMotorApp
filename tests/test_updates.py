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
    APPLY_ARGUMENT,
    HEALTH_ARGUMENT,
    _files_identical,
    apply_update_from_argv,
    health_marker_from_argv,
    _retry_file_operation,
    resolve_install_target,
)
from easymotor.updates.pe import read_pe_machine
from easymotor.features.markdown_view import inline_runs


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
    def test_release_note_markdown_is_converted_to_styled_runs(self):
        runs = inline_runs("Added **safe update** and [details](https://github.com/dwgan/EasyMotorApp).")
        rendered = "".join(run[0] for run in runs)
        self.assertEqual(rendered, "Added safe update and details.")
        self.assertIn(("safe update", "bold", None), runs)
        self.assertIn(("details", "link", "https://github.com/dwgan/EasyMotorApp"), runs)

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
        validate_download_url("https://github.com/dwgan/EasyMotorApp/releases/latest")
        for invalid in (
            "http://github.com/dwgan/EasyMotorApp",
            "https://example.com/update.exe",
            "https://token@github.com/update.exe",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(UpdateError):
                validate_download_url(invalid)


class GitHubReleaseClientTests(unittest.TestCase):
    def _client_and_release(self, executable=b"MZ-test-update"):
        manifest = json.dumps(manifest_payload(executable)).encode("utf-8")
        manifest_url = "https://github.com/dwgan/EasyMotorApp/releases/download/v1.2.3/easymotor-update.json"
        executable_url = "https://github.com/dwgan/EasyMotorApp/releases/download/v1.2.3/EasyMotor_v1.2.3_win-x64.exe"
        latest_url = "https://api.github.com/repos/dwgan/EasyMotorApp/releases/latest"
        release = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/dwgan/EasyMotorApp/releases/tag/v1.2.3",
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
    @staticmethod
    def _helper_arguments(
        current: Path,
        install: Path,
        asset_name: str,
        marker: Path,
        log_path: Path,
    ) -> list[str]:
        return [
            APPLY_ARGUMENT,
            "1234",
            str(current),
            str(install),
            asset_name,
            str(marker),
            str(log_path),
        ]

    @staticmethod
    def _healthy_process_launcher(marker: Path):
        def launch(_executable: Path, *arguments: str):
            if HEALTH_ARGUMENT in arguments:
                marker.write_text("ok\n", encoding="ascii")
            process = mock.Mock()
            process.poll.return_value = None
            return process

        return launch

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

    def test_non_helper_invocation_is_ignored(self):
        self.assertFalse(apply_update_from_argv([]))

    def test_malformed_helper_invocation_is_consumed_without_touching_files(self):
        self.assertTrue(apply_update_from_argv([APPLY_ARGUMENT]))

    def test_packaged_entry_dispatches_staged_executable_helper(self):
        source = (Path(__file__).resolve().parents[1] / "easymotor_app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if apply_update_from_argv():", source)
        self.assertIn("raise SystemExit(0)", source)

    def test_transient_file_lock_is_retried(self):
        operation = mock.Mock(side_effect=[PermissionError("locked"), None])
        with mock.patch("easymotor.updates.installer.time.sleep"):
            _retry_file_operation(operation, timeout=1)
        self.assertEqual(operation.call_count, 2)

    def test_official_three_and_four_part_names_use_new_asset_name(self):
        current = Path(r"C:\Apps\EasyMotor_v1.0.0_win-x64.exe")
        self.assertEqual(
            resolve_install_target(current, "EasyMotor_v1.0.3_win-x64.exe"),
            Path(r"C:\Apps\EasyMotor_v1.0.3_win-x64.exe"),
        )
        self.assertEqual(
            resolve_install_target(
                Path(r"C:\Apps\easymotor_V1.0.0.7_WIN-X64.EXE"),
                "EasyMotor_v1.0.3.4_win-x64.exe",
            ),
            Path(r"C:\Apps\EasyMotor_v1.0.3.4_win-x64.exe"),
        )

    def test_custom_name_is_preserved(self):
        current = Path(r"C:\Apps\Customer Demo.exe")
        self.assertEqual(
            resolve_install_target(current, "EasyMotor_v1.0.3_win-x64.exe"),
            current,
        )

    def test_invalid_or_path_asset_name_is_rejected(self):
        current = Path(r"C:\Apps\EasyMotor_v1.0.2_win-x64.exe")
        for invalid in (
            "EasyMotor.exe",
            "../EasyMotor_v1.0.3_win-x64.exe",
            r"folder\EasyMotor_v1.0.3_win-x64.exe",
            "EasyMotor_v1.0.3_win-x86.exe",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                resolve_install_target(current, invalid)

    def test_file_identity_uses_size_and_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.exe"
            second = root / "second.exe"
            first.write_bytes(b"same")
            second.write_bytes(b"same")
            self.assertTrue(_files_identical(first, second))
            second.write_bytes(b"diff")
            self.assertFalse(_files_identical(first, second))

    def test_success_replaces_official_name_and_starts_new_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-success.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"new-version")
            current.write_bytes(b"old-version")
            launcher = self._healthy_process_launcher(marker)
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch("easymotor.updates.installer._start_process", side_effect=launcher) as start,
            ):
                self.assertTrue(
                    apply_update_from_argv(
                        self._helper_arguments(current, install, asset_name, marker, log_path)
                    )
                )
            self.assertFalse(current.exists())
            self.assertEqual(install.read_bytes(), b"new-version")
            self.assertEqual(Path(start.call_args_list[-1].args[0]), install)
            self.assertFalse(list(app_dir.glob("*.previous")))
            self.assertIn(f"{current.name} -> {install.name}", log_path.read_text(encoding="utf-8"))

    def test_success_preserves_custom_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "Customer Demo.exe"
            marker = updates / "health-custom.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"new-version")
            current.write_bytes(b"old-version")
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch(
                    "easymotor.updates.installer._start_process",
                    side_effect=self._healthy_process_launcher(marker),
                ),
            ):
                apply_update_from_argv(
                    self._helper_arguments(current, current, asset_name, marker, log_path)
                )
            self.assertEqual(current.read_bytes(), b"new-version")
            self.assertFalse((app_dir / asset_name).exists())

    def test_identical_existing_target_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-reuse.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"same-new-version")
            install.write_bytes(b"same-new-version")
            current.write_bytes(b"old-version")
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch(
                    "easymotor.updates.installer._start_process",
                    side_effect=self._healthy_process_launcher(marker),
                ),
            ):
                apply_update_from_argv(
                    self._helper_arguments(current, install, asset_name, marker, log_path)
                )
            self.assertFalse(current.exists())
            self.assertEqual(install.read_bytes(), b"same-new-version")
            self.assertIn("Reusing identical existing target", log_path.read_text(encoding="utf-8"))

    def test_conflicting_existing_target_aborts_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-conflict.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"expected-new-version")
            current.write_bytes(b"old-version")
            install.write_bytes(b"unknown-user-file")
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch("easymotor.updates.installer._start_process") as start,
            ):
                apply_update_from_argv(
                    self._helper_arguments(current, install, asset_name, marker, log_path)
                )
            self.assertEqual(current.read_bytes(), b"old-version")
            self.assertEqual(install.read_bytes(), b"unknown-user-file")
            self.assertEqual(Path(start.call_args.args[0]), current)
            self.assertIn("different content", log_path.read_text(encoding="utf-8"))

    def test_unreadable_existing_target_restarts_old_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-unreadable.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"expected-new-version")
            current.write_bytes(b"old-version")
            install.write_bytes(b"unreadable-placeholder")
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch(
                    "easymotor.updates.installer._files_identical",
                    side_effect=PermissionError("denied"),
                ),
                mock.patch("easymotor.updates.installer._start_process") as start,
            ):
                apply_update_from_argv(
                    self._helper_arguments(current, install, asset_name, marker, log_path)
                )
            self.assertEqual(current.read_bytes(), b"old-version")
            self.assertEqual(Path(start.call_args.args[0]), current)
            self.assertIn("could not be verified", log_path.read_text(encoding="utf-8"))

    def test_start_failure_restores_old_name_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-fail.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"new-version")
            current.write_bytes(b"old-version")
            starts: list[Path] = []

            def launch(executable: Path, *arguments: str):
                starts.append(Path(executable))
                if HEALTH_ARGUMENT in arguments:
                    raise OSError("cannot start new executable")
                return mock.Mock()

            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch("easymotor.updates.installer._start_process", side_effect=launch),
            ):
                apply_update_from_argv(
                    self._helper_arguments(current, install, asset_name, marker, log_path)
                )
            self.assertEqual(current.read_bytes(), b"old-version")
            self.assertFalse(install.exists())
            self.assertEqual(starts[-1], current)
            self.assertIn("previous version restored", log_path.read_text(encoding="utf-8"))

    def test_uac_relaunch_preserves_old_new_and_asset_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = root / "updates"
            updates.mkdir()
            app_dir = root / "app"
            app_dir.mkdir()
            asset_name = "EasyMotor_v1.0.3_win-x64.exe"
            staged = updates / asset_name
            current = app_dir / "EasyMotor_v1.0.2_win-x64.exe"
            install = app_dir / asset_name
            marker = updates / "health-uac.ok"
            log_path = updates / "last-update.log"
            staged.write_bytes(b"new-version")
            current.write_bytes(b"old-version")
            arguments = self._helper_arguments(current, install, asset_name, marker, log_path)
            with (
                mock.patch("easymotor.updates.installer.sys.executable", str(staged)),
                mock.patch("easymotor.updates.installer.update_root", return_value=updates),
                mock.patch("easymotor.updates.installer._wait_for_process", return_value=True),
                mock.patch(
                    "easymotor.updates.installer._probe_target_directory",
                    side_effect=PermissionError("denied"),
                ),
                mock.patch("easymotor.updates.installer._request_elevation") as elevate,
            ):
                apply_update_from_argv(arguments)
            elevated_arguments = elevate.call_args.args[0]
            self.assertEqual(elevated_arguments[:-1], arguments)
            self.assertIn(str(current), elevated_arguments)
            self.assertIn(str(install), elevated_arguments)
            self.assertIn(asset_name, elevated_arguments)


if __name__ == "__main__":
    unittest.main()
