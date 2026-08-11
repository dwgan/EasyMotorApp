"""Versioned update manifest and GitHub release contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final


PRODUCT_NAME: Final = "EasyMotor"
ARCHITECTURE: Final = "win-x64"
MANIFEST_NAME: Final = "easymotor-update.json"
VERSION_RE: Final = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def parse_version(value: str) -> tuple[int, int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid EasyMotor version: {value!r}")
    parts = [int(item) if item is not None else 0 for item in match.groups()]
    if any(part > 65535 for part in parts):
        raise ValueError("version components must be between 0 and 65535")
    return tuple(parts)  # type: ignore[return-value]


@dataclass(frozen=True)
class UpdateManifest:
    schema_version: int
    product: str
    version: str
    architecture: str
    asset_name: str
    asset_size: int
    sha256: str
    published_at: str

    @classmethod
    def from_json(cls, payload: Any) -> "UpdateManifest":
        if not isinstance(payload, dict):
            raise ValueError("update manifest must be a JSON object")
        required = {
            "schema_version",
            "product",
            "version",
            "architecture",
            "asset_name",
            "asset_size",
            "sha256",
            "published_at",
        }
        missing = sorted(required.difference(payload))
        if missing:
            raise ValueError("update manifest is missing: " + ", ".join(missing))
        manifest = cls(
            schema_version=int(payload["schema_version"]),
            product=str(payload["product"]),
            version=str(payload["version"]),
            architecture=str(payload["architecture"]),
            asset_name=str(payload["asset_name"]),
            asset_size=int(payload["asset_size"]),
            sha256=str(payload["sha256"]).lower(),
            published_at=str(payload["published_at"]),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported update manifest schema")
        if self.product != PRODUCT_NAME:
            raise ValueError("update manifest product mismatch")
        parse_version(self.version)
        if self.architecture != ARCHITECTURE:
            raise ValueError("update manifest architecture mismatch")
        expected_name = f"EasyMotor_v{self.version}_{ARCHITECTURE}.exe"
        if self.asset_name != expected_name:
            raise ValueError("update executable name mismatch")
        if self.asset_size <= 0:
            raise ValueError("update executable size must be positive")
        if SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("update executable SHA-256 is invalid")
        if not self.published_at.strip():
            raise ValueError("update publish time is missing")

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "product": self.product,
            "version": self.version,
            "architecture": self.architecture,
            "asset_name": self.asset_name,
            "asset_size": self.asset_size,
            "sha256": self.sha256,
            "published_at": self.published_at,
        }


@dataclass(frozen=True)
class UpdateRelease:
    manifest: UpdateManifest
    download_url: str
    release_url: str
    release_notes: str
    github_digest: str

    @property
    def version(self) -> str:
        return self.manifest.version

    def is_newer_than(self, current_version: str) -> bool:
        return parse_version(self.version) > parse_version(current_version)
