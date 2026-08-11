"""Public GitHub release update support for EasyMotor."""

from .client import GitHubReleaseClient, UpdateCancelled, UpdateError
from .contract import UpdateManifest, UpdateRelease, parse_version
from .installer import launch_update_helper

__all__ = (
    "GitHubReleaseClient",
    "UpdateCancelled",
    "UpdateError",
    "UpdateManifest",
    "UpdateRelease",
    "launch_update_helper",
    "parse_version",
)
