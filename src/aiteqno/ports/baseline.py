"""Contracts for source-grounded end-to-end baseline evaluation.

These types deliberately describe reviewed facts from the source image instead of
deriving expectations from the candidate Document IR.  Candidate element IDs are
therefore evidence only and are never part of the matching contract.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from aiteqno.domain import DocumentIR, ElementType

from .evaluation import (
    EvaluationState,
    HardGateResult,
    NormalizedBoundingBox,
    RelationshipKind,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lower-case hex digits")
    return value


def _finite_range(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(
            f"{field_name} must be finite and between {minimum:g} and {maximum:g}"
        )
    return result


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(value)
    for item in result:
        _non_empty(item, f"{field_name} item")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be an array")
    return value


class ManualCheckStatus(str, Enum):
    """Explicit state of a human-only visual or editability check."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceTextRegion:
    """One reviewed logical text block located on the source image."""

    id: str
    text: str
    bbox: NormalizedBoundingBox
    page_number: int = 1
    essential: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.id, "source text region id")
        _non_empty(self.text, "source text region text")
        if not isinstance(self.bbox, NormalizedBoundingBox):
            raise TypeError("source text region bbox must be normalized")
        _positive_integer(self.page_number, "source text region page_number")
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "page_number": self.page_number,
            "essential": self.essential,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceTextRegion:
        return cls(
            id=data["id"],
            text=data["text"],
            bbox=NormalizedBoundingBox.from_dict(
                _mapping(data["bbox"], "source text region bbox")
            ),
            page_number=data.get("page_number", 1),
            essential=data.get("essential", False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceStructuralItem:
    """One reviewed non-text structure and its source-relative geometry."""

    id: str
    element_type: ElementType
    bbox: NormalizedBoundingBox
    page_number: int = 1
    essential: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.id, "source structural item id")
        object.__setattr__(self, "element_type", ElementType(self.element_type))
        if self.element_type is ElementType.TEXT:
            raise ValueError("source structural items must not use the text type")
        if not isinstance(self.bbox, NormalizedBoundingBox):
            raise TypeError("source structural item bbox must be normalized")
        _positive_integer(self.page_number, "source structural item page_number")
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.element_type.value,
            "bbox": self.bbox.to_dict(),
            "page_number": self.page_number,
            "essential": self.essential,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceStructuralItem:
        return cls(
            id=data["id"],
            element_type=ElementType(data["type"]),
            bbox=NormalizedBoundingBox.from_dict(
                _mapping(data["bbox"], "source structural item bbox")
            ),
            page_number=data.get("page_number", 1),
            essential=data.get("essential", False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRelationship:
    """One reviewed relation between source-truth regions or structures."""

    kind: RelationshipKind
    source: str
    target: str
    essential: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RelationshipKind(self.kind))
        _non_empty(self.source, "source relationship source")
        _non_empty(self.target, "source relationship target")
        if self.source == self.target:
            raise ValueError("source relationship endpoints must differ")
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind.value, self.source, self.target)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "target": self.target,
            "essential": self.essential,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceRelationship:
        return cls(
            kind=RelationshipKind(data["kind"]),
            source=data["source"],
            target=data["target"],
            essential=data.get("essential", False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ManualCheckEvidence:
    """A named human check with an explicit pending, passed, or failed state."""

    name: str
    status: ManualCheckStatus
    note: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.name, "manual check name")
        object.__setattr__(self, "status", ManualCheckStatus(self.status))
        if self.note is not None and not isinstance(self.note, str):
            raise TypeError("manual check note must be null or a string")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ManualCheckEvidence:
        return cls(
            name=data["name"],
            status=ManualCheckStatus(data["status"]),
            note=data.get("note"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBaselineReference:
    """Human-reviewed source truth that is independent of candidate IDs."""

    reference_id: str
    source_sha256: str
    reviewed: bool
    text_regions: tuple[SourceTextRegion, ...]
    structural_items: tuple[SourceStructuralItem, ...] = ()
    relationships: tuple[SourceRelationship, ...] = ()
    essential_text_anchors: tuple[str, ...] = ()
    expected_page_count: int = 1
    required_manual_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.reference_id, "source baseline reference_id")
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "source baseline source_sha256"),
        )
        object.__setattr__(self, "reviewed", _boolean(self.reviewed, "reviewed"))
        text_regions = tuple(self.text_regions)
        structural_items = tuple(self.structural_items)
        relationships = tuple(self.relationships)
        if not text_regions:
            raise ValueError("source baseline requires at least one text region")
        if any(not isinstance(item, SourceTextRegion) for item in text_regions):
            raise TypeError("source baseline text_regions contain an invalid value")
        if any(not isinstance(item, SourceStructuralItem) for item in structural_items):
            raise TypeError("source baseline structural_items contain an invalid value")
        if any(not isinstance(item, SourceRelationship) for item in relationships):
            raise TypeError("source baseline relationships contain an invalid value")
        item_ids = [item.id for item in (*text_regions, *structural_items)]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("source baseline item IDs must be unique")
        object.__setattr__(self, "text_regions", text_regions)
        object.__setattr__(self, "structural_items", structural_items)
        relationship_ids = [item.identity for item in relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("source baseline relationships must be unique")
        item_by_id = {item.id: item for item in (*text_regions, *structural_items)}
        text_ids = {item.id for item in text_regions}
        structure_ids = {item.id for item in structural_items}
        for relationship in relationships:
            missing = {
                endpoint
                for endpoint in (relationship.source, relationship.target)
                if endpoint not in item_by_id
            }
            if missing:
                raise ValueError(
                    "source relationship endpoints are absent from source items: "
                    + ", ".join(sorted(missing))
                )
            if (
                relationship.kind
                in {
                    RelationshipKind.READING_ORDER,
                    RelationshipKind.ADJACENCY,
                }
                and not {relationship.source, relationship.target} <= text_ids
            ):
                raise ValueError(
                    f"{relationship.kind.value} relationships require two text regions"
                )
            if relationship.kind is RelationshipKind.CONTAINMENT and (
                relationship.source not in structure_ids
                or relationship.target not in text_ids
            ):
                raise ValueError(
                    "containment relationships require a structural source and "
                    "text-region target"
                )
            source_page = item_by_id[relationship.source].page_number
            target_page = item_by_id[relationship.target].page_number
            if source_page != target_page:
                raise ValueError(
                    "source relationship endpoints must be on the same page: "
                    f"{relationship.source!r} is on page {source_page}, "
                    f"{relationship.target!r} is on page {target_page}"
                )
        object.__setattr__(self, "relationships", relationships)
        object.__setattr__(
            self,
            "essential_text_anchors",
            _string_tuple(self.essential_text_anchors, "essential_text_anchors"),
        )
        _positive_integer(self.expected_page_count, "expected_page_count")
        object.__setattr__(
            self,
            "required_manual_checks",
            _string_tuple(self.required_manual_checks, "required_manual_checks"),
        )
        for item in (*text_regions, *structural_items):
            if item.page_number > self.expected_page_count:
                raise ValueError(f"source item {item.id!r} exceeds expected_page_count")

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "source_sha256": self.source_sha256,
            "reviewed": self.reviewed,
            "text_regions": [item.to_dict() for item in self.text_regions],
            "structural_items": [item.to_dict() for item in self.structural_items],
            "relationships": [item.to_dict() for item in self.relationships],
            "essential_text_anchors": list(self.essential_text_anchors),
            "expected_page_count": self.expected_page_count,
            "required_manual_checks": list(self.required_manual_checks),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceBaselineReference:
        return cls(
            reference_id=data["reference_id"],
            source_sha256=data["source_sha256"],
            reviewed=data["reviewed"],
            text_regions=tuple(
                SourceTextRegion.from_dict(_mapping(item, "source text region"))
                for item in _sequence(data.get("text_regions", ()), "text_regions")
            ),
            structural_items=tuple(
                SourceStructuralItem.from_dict(_mapping(item, "source structural item"))
                for item in _sequence(
                    data.get("structural_items", ()), "structural_items"
                )
            ),
            relationships=tuple(
                SourceRelationship.from_dict(_mapping(item, "source relationship"))
                for item in _sequence(data.get("relationships", ()), "relationships")
            ),
            essential_text_anchors=tuple(
                _sequence(
                    data.get("essential_text_anchors", ()),
                    "essential_text_anchors",
                )
            ),
            expected_page_count=data.get("expected_page_count", 1),
            required_manual_checks=tuple(
                _sequence(
                    data.get("required_manual_checks", ()),
                    "required_manual_checks",
                )
            ),
        )

    @classmethod
    def from_json(cls, text: str | bytes | bytearray) -> SourceBaselineReference:
        data = json.loads(text)
        return cls.from_dict(_mapping(data, "source baseline reference"))


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBaselineObservation:
    """Candidate IR and final-DOCX evidence measured from the same source."""

    source_sha256: str
    candidate_ir: DocumentIR
    final_docx_text: str
    visible_rendered_text: str | None = None
    rendered_page_count: int | None = None
    manual_checks: tuple[ManualCheckEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "source observation source_sha256"),
        )
        if not isinstance(self.candidate_ir, DocumentIR):
            raise TypeError("source observation candidate_ir must be a DocumentIR")
        if not isinstance(self.final_docx_text, str):
            raise TypeError("source observation final_docx_text must be a string")
        if self.visible_rendered_text is not None and not isinstance(
            self.visible_rendered_text, str
        ):
            raise TypeError("visible_rendered_text must be null or a string")
        if self.rendered_page_count is not None:
            _positive_integer(self.rendered_page_count, "rendered_page_count")
        manual_checks = tuple(self.manual_checks)
        if any(not isinstance(item, ManualCheckEvidence) for item in manual_checks):
            raise TypeError("manual_checks contain an invalid value")
        check_names = [item.name for item in manual_checks]
        if len(check_names) != len(set(check_names)):
            raise ValueError("manual check names must be unique")
        object.__setattr__(self, "manual_checks", manual_checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "candidate_ir": self.candidate_ir.to_dict(),
            "final_docx_text": self.final_docx_text,
            "visible_rendered_text": self.visible_rendered_text,
            "rendered_page_count": self.rendered_page_count,
            "manual_checks": [item.to_dict() for item in self.manual_checks],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceBaselineObservation:
        return cls(
            source_sha256=data["source_sha256"],
            candidate_ir=DocumentIR.from_dict(
                _mapping(data["candidate_ir"], "candidate_ir")
            ),
            final_docx_text=data["final_docx_text"],
            visible_rendered_text=data.get("visible_rendered_text"),
            rendered_page_count=data.get("rendered_page_count"),
            manual_checks=tuple(
                ManualCheckEvidence.from_dict(_mapping(item, "manual check"))
                for item in _sequence(data.get("manual_checks", ()), "manual_checks")
            ),
        )

    @classmethod
    def from_json(cls, text: str | bytes | bytearray) -> SourceBaselineObservation:
        data = json.loads(text)
        return cls.from_dict(_mapping(data, "source baseline observation"))


@dataclass(frozen=True, slots=True, kw_only=True)
class BaselineComponentScore:
    """One weighted source-grounded score and its independent minimum."""

    name: str
    score: float
    weight: float
    minimum: float

    def __post_init__(self) -> None:
        _non_empty(self.name, "baseline component name")
        object.__setattr__(
            self,
            "score",
            _finite_range(
                self.score, "baseline component score", minimum=0, maximum=100
            ),
        )
        object.__setattr__(
            self,
            "weight",
            _finite_range(
                self.weight, "baseline component weight", minimum=0, maximum=1
            ),
        )
        object.__setattr__(
            self,
            "minimum",
            _finite_range(
                self.minimum,
                "baseline component minimum",
                minimum=0,
                maximum=100,
            ),
        )

    @property
    def passed(self) -> bool:
        return self.score >= self.minimum

    @property
    def weighted_score(self) -> float:
        return round(self.score * self.weight, 6)

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "weight": self.weight,
            "weighted_score": self.weighted_score,
            "minimum": self.minimum,
            "status": "pass" if self.passed else "fail",
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class LogicalBlockEvaluation:
    """ID-independent text recovered from one reviewed source region."""

    reference_id: str
    candidate_element_ids: tuple[str, ...]
    observed_text: str
    character_accuracy: float
    covered: bool
    essential: bool

    def __post_init__(self) -> None:
        _non_empty(self.reference_id, "logical block reference_id")
        object.__setattr__(
            self,
            "candidate_element_ids",
            _string_tuple(self.candidate_element_ids, "candidate_element_ids"),
        )
        if not isinstance(self.observed_text, str):
            raise TypeError("logical block observed_text must be a string")
        object.__setattr__(
            self,
            "character_accuracy",
            _finite_range(
                self.character_accuracy,
                "logical block character_accuracy",
                minimum=0,
                maximum=100,
            ),
        )
        object.__setattr__(self, "covered", _boolean(self.covered, "covered"))
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "candidate_element_ids": list(self.candidate_element_ids),
            "observed_text": self.observed_text,
            "character_accuracy": self.character_accuracy,
            "covered": self.covered,
            "essential": self.essential,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuralItemEvaluation:
    """Geometry-based match for one reviewed structural item."""

    reference_id: str
    candidate_element_id: str | None
    similarity: float
    matched: bool
    essential: bool

    def __post_init__(self) -> None:
        _non_empty(self.reference_id, "structural match reference_id")
        if self.candidate_element_id is not None:
            _non_empty(self.candidate_element_id, "candidate_element_id")
        object.__setattr__(
            self,
            "similarity",
            _finite_range(
                self.similarity,
                "structural match similarity",
                minimum=0,
                maximum=100,
            ),
        )
        object.__setattr__(self, "matched", _boolean(self.matched, "matched"))
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))
        if self.matched != (self.candidate_element_id is not None):
            raise ValueError("matched must agree with candidate_element_id presence")

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "candidate_element_id": self.candidate_element_id,
            "similarity": self.similarity,
            "matched": self.matched,
            "essential": self.essential,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipEvaluation:
    """ID-independent result for one reviewed source relationship."""

    kind: RelationshipKind
    source: str
    target: str
    passed: bool
    essential: bool
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RelationshipKind(self.kind))
        _non_empty(self.source, "relationship evaluation source")
        _non_empty(self.target, "relationship evaluation target")
        object.__setattr__(self, "passed", _boolean(self.passed, "passed"))
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))
        _non_empty(self.reason, "relationship evaluation reason")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind.value, self.source, self.target)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "target": self.target,
            "status": "pass" if self.passed else "fail",
            "essential": self.essential,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBaselineResult:
    """Deterministic scores, gates, human evidence, and final baseline state."""

    evaluator_name: str
    evaluator_version: str
    reference_id: str
    source_sha256: str
    text_evidence: str
    overall_score: float
    threshold: float
    components: tuple[BaselineComponentScore, ...]
    logical_blocks: tuple[LogicalBlockEvaluation, ...]
    structural_items: tuple[StructuralItemEvaluation, ...]
    hard_gates: tuple[HardGateResult, ...]
    manual_checks: tuple[ManualCheckEvidence, ...]
    state: EvaluationState
    reasons: tuple[str, ...]
    relationships: tuple[RelationshipEvaluation, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.evaluator_name, "baseline evaluator_name")
        _non_empty(self.evaluator_version, "baseline evaluator_version")
        _non_empty(self.reference_id, "baseline result reference_id")
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "baseline result source_sha256"),
        )
        if self.text_evidence not in {"docx_readback", "rendered_visible"}:
            raise ValueError(
                "text_evidence must be 'docx_readback' or 'rendered_visible'"
            )
        object.__setattr__(
            self,
            "overall_score",
            _finite_range(
                self.overall_score,
                "baseline overall_score",
                minimum=0,
                maximum=100,
            ),
        )
        object.__setattr__(
            self,
            "threshold",
            _finite_range(
                self.threshold,
                "baseline threshold",
                minimum=0,
                maximum=100,
            ),
        )
        for field_name, item_type in (
            ("components", BaselineComponentScore),
            ("logical_blocks", LogicalBlockEvaluation),
            ("structural_items", StructuralItemEvaluation),
            ("hard_gates", HardGateResult),
            ("manual_checks", ManualCheckEvidence),
            ("relationships", RelationshipEvaluation),
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(
                    f"baseline result {field_name} contain an invalid value"
                )
            object.__setattr__(self, field_name, values)
        object.__setattr__(self, "state", EvaluationState(self.state))
        object.__setattr__(self, "reasons", _string_tuple(self.reasons, "reasons"))

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluator": {
                "name": self.evaluator_name,
                "version": self.evaluator_version,
            },
            "reference_id": self.reference_id,
            "source_sha256": self.source_sha256,
            "text_evidence": self.text_evidence,
            "overall_score": self.overall_score,
            "threshold": self.threshold,
            "components": {
                component.name: component.to_dict() for component in self.components
            },
            "logical_blocks": [item.to_dict() for item in self.logical_blocks],
            "structural_items": [item.to_dict() for item in self.structural_items],
            "hard_gates": [item.to_dict() for item in self.hard_gates],
            "manual_checks": [item.to_dict() for item in self.manual_checks],
            "relationships": [item.to_dict() for item in self.relationships],
            "state": self.state.value,
            "reasons": list(self.reasons),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


__all__ = [
    "BaselineComponentScore",
    "LogicalBlockEvaluation",
    "ManualCheckEvidence",
    "ManualCheckStatus",
    "RelationshipEvaluation",
    "SourceBaselineObservation",
    "SourceBaselineReference",
    "SourceBaselineResult",
    "SourceRelationship",
    "SourceStructuralItem",
    "SourceTextRegion",
    "StructuralItemEvaluation",
]
