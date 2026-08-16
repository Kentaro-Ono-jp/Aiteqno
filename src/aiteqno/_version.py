"""Resolve the package version from installed distribution metadata."""

from importlib.metadata import PackageNotFoundError, version


DISTRIBUTION_NAME = "aiteqno"
UNINSTALLED_VERSION = "0.0.0+uninstalled"


def _resolve_version() -> str:
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return UNINSTALLED_VERSION


__version__ = _resolve_version()
