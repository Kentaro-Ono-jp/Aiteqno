"""Port contracts for resolving verified, bundle-local Document IR assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aiteqno.domain import Asset


class AssetResolutionError(ValueError):
    """Raised when an asset cannot be safely resolved and verified."""

    def __init__(self, code: str, asset_id: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.asset_id = asset_id


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """Immutable bytes read from an asset after all registry checks pass."""

    asset_id: str
    source_path: Path
    data: bytes

    @property
    def byte_size(self) -> int:
        return len(self.data)


class AssetResolver(Protocol):
    """Resolve and verify one registry entry without network access."""

    def resolve(self, asset: Asset) -> ResolvedAsset:
        """Return verified bytes or raise :class:`AssetResolutionError`."""
