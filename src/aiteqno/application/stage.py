"""Pure cumulative-stage gate for source-grounded questionnaire fixtures."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Final, Sequence


STAGE_GATE_VERSION: Final = "1.0"


def _score(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError(f"{field_name} must be finite and between 0 and 100")
    return result


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class StageFixtureMeasurement:
    """One fixture's formal score plus independently verified integrity."""

    fixture_id: str
    overall_score: float
    integrity_passed: bool
    artifact_path: str
    previous_overall_score: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fixture_id",
            _non_empty(self.fixture_id, "fixture_id"),
        )
        object.__setattr__(
            self,
            "overall_score",
            _score(self.overall_score, "overall_score"),
        )
        if not isinstance(self.integrity_passed, bool):
            raise TypeError("integrity_passed must be a boolean")
        object.__setattr__(
            self,
            "artifact_path",
            _non_empty(self.artifact_path, "artifact_path"),
        )
        if self.previous_overall_score is not None:
            object.__setattr__(
                self,
                "previous_overall_score",
                _score(self.previous_overall_score, "previous_overall_score"),
            )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "fixture_id": self.fixture_id,
            "overall_score": self.overall_score,
            "integrity_passed": self.integrity_passed,
            "artifact_path": self.artifact_path,
        }
        if self.previous_overall_score is not None:
            result["previous_overall_score"] = self.previous_overall_score
            result["score_delta_diagnostic"] = round(
                self.overall_score - self.previous_overall_score,
                6,
            )
        return result


@dataclass(frozen=True, slots=True, kw_only=True)
class StageFixtureDecision:
    """One independently evaluated fixture decision."""

    measurement: StageFixtureMeasurement
    threshold: float
    passed: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.measurement.to_dict(),
            "threshold": self.threshold,
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StageGateResult:
    """Order-independent all-fixtures gate; averages are diagnostic only."""

    gate_version: str
    threshold: float
    minimum_overall: float
    average_overall_diagnostic: float
    passed: bool
    fixtures: tuple[StageFixtureDecision, ...]

    @property
    def state(self) -> str:
        return "pass" if self.passed else "fail"

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_version": self.gate_version,
            "threshold": self.threshold,
            "minimum_overall": self.minimum_overall,
            "average_overall_diagnostic": self.average_overall_diagnostic,
            "average_used_for_decision": False,
            "state": self.state,
            "fixtures": [item.to_dict() for item in self.fixtures],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


def evaluate_stage_gate(
    measurements: Sequence[StageFixtureMeasurement],
    *,
    threshold: float = 70.0,
) -> StageGateResult:
    """Pass only when every fixture independently clears score and integrity."""

    if isinstance(measurements, (str, bytes, bytearray)):
        raise TypeError("measurements must be a sequence")
    normalized = tuple(measurements)
    if not normalized:
        raise ValueError("stage gate requires at least one fixture measurement")
    if any(not isinstance(item, StageFixtureMeasurement) for item in normalized):
        raise TypeError("measurements contain an invalid value")
    fixture_ids = [item.fixture_id for item in normalized]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise ValueError("stage fixture IDs must be unique")
    minimum = _score(threshold, "threshold")

    decisions: list[StageFixtureDecision] = []
    for measurement in sorted(normalized, key=lambda item: item.fixture_id):
        reasons: list[str] = []
        if measurement.overall_score < minimum:
            reasons.append(
                "overall_below_threshold:"
                f"{measurement.overall_score:g}<{minimum:g}"
            )
        if not measurement.integrity_passed:
            reasons.append("integrity_failed")
        passed = not reasons
        if passed:
            reasons.append("individual_overall_and_integrity_passed")
        decisions.append(
            StageFixtureDecision(
                measurement=measurement,
                threshold=minimum,
                passed=passed,
                reasons=tuple(reasons),
            )
        )

    minimum_overall = min(item.overall_score for item in normalized)
    average = sum(item.overall_score for item in normalized) / len(normalized)
    return StageGateResult(
        gate_version=STAGE_GATE_VERSION,
        threshold=minimum,
        minimum_overall=round(minimum_overall, 6),
        average_overall_diagnostic=round(average, 6),
        passed=all(item.passed for item in decisions),
        fixtures=tuple(decisions),
    )
