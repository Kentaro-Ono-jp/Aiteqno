"""Contracts for deterministic source-geometry OCR region grouping."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .ocr_experiment import OcrExperimentComparisonResult, OcrExperimentDecision
from .ocr_language import OcrProtectedLiteralRecovery


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _json_object(value: Any, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must contain only finite JSON values") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping is guarded above
        raise TypeError(f"{field_name} must be a JSON object")
    return MappingProxyType(decoded)


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strings(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrRegionGroupingConfig:
    """One fixed geometry-only rule for merging adjacent same-row OCR crops."""

    enabled: bool = False
    minimum_vertical_overlap_ratio: float = 0.45
    maximum_horizontal_gap_height_ratio: float = 1.0
    block_vertical_separators: bool = True

    def __post_init__(self) -> None:
        for field_name in ("enabled", "block_vertical_separators"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a boolean")
        for field_name in (
            "minimum_vertical_overlap_ratio",
            "maximum_horizontal_gap_height_ratio",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be a number")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, normalized)
        if not 0.0 < self.minimum_vertical_overlap_ratio <= 1.0:
            raise ValueError(
                "minimum_vertical_overlap_ratio must be greater than 0 and at most 1"
            )
        if self.maximum_horizontal_gap_height_ratio < 0.0:
            raise ValueError("maximum_horizontal_gap_height_ratio must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "minimum_vertical_overlap_ratio": self.minimum_vertical_overlap_ratio,
            "maximum_horizontal_gap_height_ratio": (
                self.maximum_horizontal_gap_height_ratio
            ),
            "block_vertical_separators": self.block_vertical_separators,
            "uses_ocr_text": False,
            "uses_ocr_confidence": False,
        }

    def digest(self, *, algorithm: str, algorithm_version: str) -> str:
        return _json_sha256(
            {
                "algorithm": algorithm,
                "algorithm_version": algorithm_version,
                "configuration": self.to_dict(),
            }
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrRegionGroupingEvidence:
    """Auditable source regions, links, unions, and singletons for one plan."""

    schema_version: str
    algorithm: str
    algorithm_version: str
    configuration: Mapping[str, object]
    configuration_digest: str
    source_regions: tuple[Mapping[str, object], ...]
    vertical_separators: tuple[Mapping[str, object], ...]
    planned_regions: tuple[Mapping[str, object], ...]
    groups: tuple[Mapping[str, object], ...]
    adjacency_decisions: tuple[Mapping[str, object], ...]
    singleton_region_refs: tuple[str, ...]
    plan_digest: str

    def __post_init__(self) -> None:
        for field_name in ("schema_version", "algorithm", "algorithm_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("configuration_digest", "plan_digest"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be 64 lower-case hex digits")
        object.__setattr__(
            self,
            "configuration",
            _json_object(self.configuration, "configuration"),
        )
        for field_name in (
            "source_regions",
            "vertical_separators",
            "planned_regions",
            "groups",
            "adjacency_decisions",
        ):
            values = getattr(self, field_name)
            if isinstance(values, (str, bytes, bytearray)) or not isinstance(
                values, Sequence
            ):
                raise TypeError(f"{field_name} must be a sequence")
            object.__setattr__(
                self,
                field_name,
                tuple(_json_object(value, f"{field_name} item") for value in values),
            )
        object.__setattr__(
            self,
            "singleton_region_refs",
            _strings(self.singleton_region_refs, "singleton_region_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "configuration": dict(self.configuration),
            "configuration_digest": self.configuration_digest,
            "source_regions": [dict(value) for value in self.source_regions],
            "vertical_separators": [dict(value) for value in self.vertical_separators],
            "planned_regions": [dict(value) for value in self.planned_regions],
            "groups": [dict(value) for value in self.groups],
            "adjacency_decisions": [dict(value) for value in self.adjacency_decisions],
            "singleton_region_refs": list(self.singleton_region_refs),
            "counts": {
                "source_regions": len(self.source_regions),
                "planned_regions": len(self.planned_regions),
                "groups": len(self.groups),
                "singletons": len(self.singleton_region_refs),
            },
            "plan_digest": self.plan_digest,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrRegionGroupingComparisonResult:
    """Generic decision plus grouping-specific recovery and integrity evidence."""

    comparison: OcrExperimentComparisonResult
    protected_literals: tuple[OcrProtectedLiteralRecovery, ...]
    multilingual_smoke: Mapping[str, object]
    target_recovery: Mapping[str, object]
    singleton_observations: Mapping[str, object]
    decision: OcrExperimentDecision
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.comparison, OcrExperimentComparisonResult):
            raise TypeError("comparison must be an OcrExperimentComparisonResult")
        protected = tuple(self.protected_literals)
        if any(
            not isinstance(value, OcrProtectedLiteralRecovery) for value in protected
        ):
            raise TypeError(
                "protected_literals must contain OcrProtectedLiteralRecovery values"
            )
        literals = tuple(value.literal for value in protected)
        if len(literals) != len(set(literals)):
            raise ValueError("protected_literals must not contain duplicates")
        object.__setattr__(self, "protected_literals", protected)
        for field_name in (
            "multilingual_smoke",
            "target_recovery",
            "singleton_observations",
        ):
            object.__setattr__(
                self,
                field_name,
                _json_object(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "decision", OcrExperimentDecision(self.decision))
        reasons = _strings(self.reasons, "reasons")
        if not reasons:
            raise ValueError("reasons must not be empty")
        object.__setattr__(self, "reasons", reasons)

    @property
    def text_character_accuracy(self):
        return self.comparison.text_character_accuracy

    @property
    def logical_block_coverage(self):
        return self.comparison.logical_block_coverage

    @property
    def essential_anchor_recall(self):
        return self.comparison.essential_anchor_recall

    @property
    def anchors(self):
        return self.comparison.anchors

    @property
    def blocks(self):
        return self.comparison.blocks

    @property
    def checks(self):
        return self.comparison.checks

    def to_dict(self) -> dict[str, object]:
        report = self.comparison.to_dict()
        report["adoption_policy"].update(
            {
                "protected_literals_must_not_regress": True,
                "multilingual_smoke_must_pass": True,
                "singleton_observations_must_be_identical": True,
                "newly_recovered_target_required": [
                    "title",
                    "content-structure",
                ],
            }
        )
        report["recovery"]["protected_literals"] = {
            "items": [value.to_dict() for value in self.protected_literals],
            "lost": [value.literal for value in self.protected_literals if value.lost],
        }
        report["recovery"]["grouping_targets"] = dict(self.target_recovery)
        report["multilingual_smoke"] = dict(self.multilingual_smoke)
        report["singleton_observations"] = dict(self.singleton_observations)
        report["decision"] = self.decision.value
        report["reasons"] = list(self.reasons)
        return report

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


__all__ = [
    "OcrRegionGroupingComparisonResult",
    "OcrRegionGroupingConfig",
    "OcrRegionGroupingEvidence",
]
