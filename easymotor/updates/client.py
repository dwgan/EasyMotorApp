"""Anonymous, allowlisted GitHub release discovery and download."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable, Final

from .contract import MANIFEST_NAME, UpdateManifest, UpdateRelease, parse_version


GITHUB_OWNER: Final = "dwgan"
GITHUB_REPOSITORY: Final = "EasyMotor"
API_VERSION: Final = "2026-03-10"
ALLOWED_HTTPS_HOSTS: Final = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
ProgressCallback = Callable[[int, int], None]


class UpdateError(RuntimeError):
    """Safe, user-displayable update failure."""


class UpdateCancelled(UpdateError):
    """Raised when a user cancels an update download."""


def validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or parsed.hostname not in ALLOWED_HTTPS_HOSTS:
        raise UpdateError("GitHub returned an untrusted download address")
    if parsed.username or parsed.password:
        raise UpdateError("authenticated download addresses are not allowed")


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _asset_digest(asset: dict[str, object]) -> str:
    digest = str(asset.get("digest") or "").lower()
    if digest.startswith("sha256:"):
        return digest[7:]
    return ""


class GitHubReleaseClient:
    def __init__(self, *, timeout: float = 20.0, opener=None) -> None:
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(_AllowlistedRedirectHandler())

    @property
    def latest_url(self) -> str:
        return (
            f"https://api.github.com/repos/{GITHUB_OWNER}/"
            f"{GITHUB_REPOSITORY}/releases/latest"
        )

    def _request(self, url: str, *, accept: str = "application/vnd.github+json"):
        validate_download_url(url)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": "EasyMotor-Updater",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError("No published EasyMotor release was found") from exc
            if exc.code in (403, 429):
                raise UpdateError("GitHub update checks are temporarily rate limited") from exc
            raise UpdateError(f"GitHub update check failed (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError("Unable to reach GitHub; check the network connection") from exc

    def _read_bytes(self, url: str, *, maximum: int) -> bytes:
        with self._request(url, accept="application/octet-stream") as response:
            data = response.read(maximum + 1)
        if len(data) > maximum:
            raise UpdateError("GitHub update metadata is unexpectedly large")
        return data

    def fetch_latest(self) -> UpdateRelease:
        try:
            with self._request(self.latest_url) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise UpdateError("GitHub returned invalid release metadata") from exc
        if not isinstance(payload, dict):
            raise UpdateError("GitHub returned invalid release metadata")
        if payload.get("draft") or payload.get("prerelease"):
            raise UpdateError("GitHub latest release is not a stable published release")
        tag = str(payload.get("tag_name") or "")
        try:
            tag_version = tag[1:] if tag.startswith("v") else tag
            parse_version(tag_version)
        except ValueError as exc:
            raise UpdateError("GitHub release tag is not a valid EasyMotor version") from exc
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("GitHub release has no downloadable assets")
        asset_map = {
            str(asset.get("name")): asset
            for asset in assets
            if isinstance(asset, dict) and asset.get("state") == "uploaded"
        }
        manifest_asset = asset_map.get(MANIFEST_NAME)
        if manifest_asset is None:
            raise UpdateError("GitHub release is missing the update manifest")
        manifest_url = str(manifest_asset.get("browser_download_url") or "")
        manifest_bytes = self._read_bytes(manifest_url, maximum=256 * 1024)
        manifest_digest = _asset_digest(manifest_asset)
        if manifest_digest and hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
            raise UpdateError("GitHub update manifest digest mismatch")
        try:
            manifest = UpdateManifest.from_json(json.loads(manifest_bytes.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise UpdateError(f"Invalid EasyMotor update manifest: {exc}") from exc
        if parse_version(manifest.version) != parse_version(tag_version):
            raise UpdateError("Release tag and update manifest version do not match")
        executable_asset = asset_map.get(manifest.asset_name)
        if executable_asset is None:
            raise UpdateError("GitHub release is missing the EasyMotor executable")
        if int(executable_asset.get("size") or 0) != manifest.asset_size:
            raise UpdateError("GitHub executable size does not match the manifest")
        github_digest = _asset_digest(executable_asset)
        if not github_digest:
            raise UpdateError("GitHub did not provide an executable SHA-256 digest")
        if github_digest != manifest.sha256:
            raise UpdateError("GitHub executable digest does not match the manifest")
        download_url = str(executable_asset.get("browser_download_url") or "")
        validate_download_url(download_url)
        release_url = str(payload.get("html_url") or "")
        validate_download_url(release_url)
        return UpdateRelease(
            manifest=manifest,
            download_url=download_url,
            release_url=release_url,
            release_notes=str(payload.get("body") or ""),
            github_digest=github_digest,
        )

    def download(
        self,
        release: UpdateRelease,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        completed = 0
        try:
            with self._request(release.download_url, accept="application/octet-stream") as response:
                with partial.open("wb") as stream:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise UpdateCancelled("Update download was cancelled")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        digest.update(chunk)
                        completed += len(chunk)
                        if completed > release.manifest.asset_size:
                            raise UpdateError("Downloaded update is larger than the manifest")
                        if progress is not None:
                            progress(completed, release.manifest.asset_size)
            if completed != release.manifest.asset_size:
                raise UpdateError("Downloaded update size does not match the manifest")
            if digest.hexdigest() != release.manifest.sha256:
                raise UpdateError("Downloaded update SHA-256 verification failed")
            os.replace(partial, destination)
            return destination
        except Exception:
            partial.unlink(missing_ok=True)
            raise
