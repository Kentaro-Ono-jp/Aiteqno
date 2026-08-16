"""Contracts for observing and evaluating reconstructed DOCX documents."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from os import PathLike
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from aiteqno.domain import ElementType

from .docx import DocxRenderReport


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _finite_unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return result


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _optional_sha256(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be null or 64 lower-case hex digits")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(value)
    for item in result:
        _non_empty(item, f"{field_name} item")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


class EvaluationState(str, Enum):
    """Final machine evaluation decision."""

    PASS = "pass"
    FAIL = "fail"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class RelationshipKind(str, Enum):
    """Structural relation kinds used by the V1 quality contract."""

    READING_ORDER = "reading_order"
    CONTAINMENT = "containment"
    ADJACENCY = "adjacency"


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedBoundingBox:
    """A page-relative rectangle whose coordinates are all in the 0..1 range."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite_unit(self.x, "bbox.x"))
        object.__setattr__(self, "y", _finite_unit(self.y, "bbox.y"))
        object.__setattr__(self, "width", _finite_unit(self.width, "bbox.width"))
        object.__setattr__(self, "height", _finite_unit(self.height, "bbox.height"))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("bbox width and height must be greater than zero")
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("bbox must remain inside normalized page bounds")

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def to_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NormalizedBoundingBox:
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceElement:
    """One reviewed element expected to survive in the generated DOCX."""

    id: str
    element_type: ElementType
    page_number: int
    text: str | None = None
    bbox: NormalizedBoundingBox | None = None
    reading_order: int | None = None
    content_sha256: str | None = None
    essential: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.id, "reference element id")
        object.__setattr__(self, "element_type", ElementType(self.element_type))
        _positive_integer(self.page_number, "reference element page_number")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("reference element text must be null or a string")
        if self.element_type is ElementType.TEXT and self.text is None:
            raise ValueError("text reference elements require text")
        if self.bbox is not None and not isinstance(self.bbox, NormalizedBoundingBox):
            raise TypeError("reference element bbox must be normalized")
        if self.reading_order is not None and (
            isinstance(self.reading_order, bool)
            or not isinstance(self.reading_order, int)
            or self.reading_order < 0
        ):
            raise ValueError("reference element reading_order must be non-negative")
        object.__setattr__(
            self,
            "content_sha256",
            _optional_sha256(self.content_sha256, "reference content_sha256"),
        )
        object.__setattr__(self, "essential", _boolean(self.essential, "essential"))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.element_type.value,
            "page_number": self.page_number,
            "text": self.text,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
            "reading_order": self.reading_order,
            "content_sha256": self.content_sha256,
            "essential": self.essential,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReferenceElement:
        bbox = data.get("bbox")
        return cls(
            id=data["id"],
            element_type=ElementType(data["type"]),
            page_number=data["page_number"],
            text=data.get("text"),
            bbox=(
                None
                if bbox is None
                else NormalizedBoundingBox.from_dict(_mapping(bbox, "bbox"))
            ),
            reading_order=data.get("reading_order"),
            content_sha256=data.get("content_sha256"),
            essential=data.get("essential", False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservedElement:
    """One visible element read from the generated DOCX package."""

    id: str
    element_type: ElementType
    page_number: int = 1
    text: str | None = None
    bbox: NormalizedBoundingBox | None = None
    reading_order: int | None = None
    source_element_id: str | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.id, "observed element id")
        object.__setattr__(self, "element_type", ElementType(self.element_type))
        _positive_integer(self.page_number, "observed element page_number")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("observed element text must be null or a string")
        if self.element_type is ElementType.TEXT and self.text is None:
            raise ValueError("observed text elements require text")
        if self.bbox is not None and not isinstance(self.bbox, NormalizedBoundingBox):
            raise TypeError("observed element bbox must be normalized")
        if self.reading_order is not None and (
            isinstance(self.reading_order, bool)
            or not isinstance(self.reading_order, int)
            or self.reading_order < 0
        ):
            raise ValueError("observed element reading_order must be non-negative")
        if self.source_element_id is not None:
            _non_empty(self.source_element_id, "observed source_element_id")
        object.__setattr__(
            self,
            "content_sha256",
            _optional_sha256(self.content_sha256, "observed content_sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.element_type.value,
            "page_number": self.page_number,
            "text": self.text,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
            "reading_order": self.reading_order,
            "source_element_id": self.source_element_id,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObservedElement:
        bbox = data.get("bbox")
        return cls(
            id=data["id"],
            element_type=ElementType(data["type"]),
            page_number=data.get("page_number", 1),
            text=data.get("text"),
            bbox=(
                None
                if bbox is None
                else NormalizedBoundingBox.from_dict(_mapping(bbox, "bbox"))
            ),
            reading_order=data.get("reading_order"),
            source_element_id=data.get("source_element_id"),
            content_sha256=data.get("content_sha256"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuralRelationship:
    """A deterministic relationship between visible or structural nodes."""

    kind: RelationshipKind
    source: str
    target: str
    essential: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RelationshipKind(self.kind))
        _non_empty(self.source, "relationship source")
        _non_empty(self.target, "relationship target")
        if self.source == self.target:
            raise ValueError("relationship source and target must differ")
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
    def from_dict(cls, data: Mapping[str, Any]) -> StructuralRelationship:
        return cls(
            kind=RelationshipKind(data["kind"]),
            source=data["source"],
            target=data["target"],
            essential=data.get("essential", False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationReference:
    """Reviewed expected content and structure for one licensed fixture."""

    reference_id: str
    ir_version: str
    reviewed: bool
    elements: tuple[ReferenceElement, ...]
    relationships: tuple[StructuralRelationship, ...] = ()
    essential_text_anchors: tuple[str, ...] = ()
    required_human_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.reference_id, "reference_id")
        _non_empty(self.ir_version, "reference ir_version")
        object.__setattr__(self, "reviewed", _boolean(self.reviewed, "reviewed"))
        elements = tuple(self.elements)
        relationships = tuple(self.relationships)
        if any(not isinstance(item, ReferenceElement) for item in elements):
            raise TypeError("reference elements contain an invalid value")
        if any(not isinstance(item, StructuralRelationship) for item in relationships):
            raise TypeError("reference relationships contain an invalid value")
        element_ids = [item.id for item in elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("reference element IDs must be unique")
        relationship_ids = [item.identity for item in relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("reference relationships must be unique")
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "relationships", relationships)
        object.__setattr__(
            self,
            "essential_text_anchors",
            _string_tuple(self.essential_text_anchors, "essential_text_anchors"),
        )
        object.__setattr__(
            self,
            "required_human_checks",
            _string_tuple(self.required_human_checks, "required_human_checks"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "ir_version": self.ir_version,
            "reviewed": self.reviewed,
            "elements": [element.to_dict() for element in self.elements],
            "relationships": [
                relationship.to_dict() for relationship in self.relationships
            ],
            "essential_text_anchors": list(self.essential_text_anchors),
            "required_human_checks": list(self.required_human_checks),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationReference:
        return cls(
            reference_id=data["reference_id"],
            ir_version=data["ir_version"],
            reviewed=data["reviewed"],
            elements=tuple(
                ReferenceElement.from_dict(_mapping(item, "reference element"))
                for item in _sequence(data.get("elements", ()), "elements")
            ),
            relationships=tuple(
                StructuralRelationship.from_dict(
                    _mapping(item, "reference relationship")
                )
                for item in _sequence(data.get("relationships", ()), "relationships")
            ),
            essential_text_anchors=tuple(
                _sequence(
                    data.get("essential_text_anchors", ()),
                    "essential_text_anchors",
                )
            ),
            required_human_checks=tuple(
                _sequence(
                    data.get("required_human_checks", ()),
                    "required_human_checks",
                )
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DocxObservation:
    """Normalized visible and structural facts read back from a DOCX."""

    observer_name: str
    observer_version: str
    source_sha256: str
    package_readable: bool
    python_docx_reopenable: bool
    elements: tuple[ObservedElement, ...] = ()
    relationships: tuple[StructuralRelationship, ...] = ()
    external_relationships: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.observer_name, "observer_name")
        _non_empty(self.observer_version, "observer_version")
        if (
            not isinstance(self.source_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.source_sha256) is None
        ):
            raise ValueError(
                "observation source_sha256 must be 64 lower-case hex digits"
            )
        object.__setattr__(
            self,
            "package_readable",
            _boolean(self.package_readable, "package_readable"),
        )
        object.__setattr__(
            self,
            "python_docx_reopenable",
            _boolean(self.python_docx_reopenable, "python_docx_reopenable"),
        )
        elements = tuple(self.elements)
        relationships = tuple(self.relationships)
        if any(not isinstance(item, ObservedElement) for item in elements):
            raise TypeError("observation elements contain an invalid value")
        if any(not isinstance(item, StructuralRelationship) for item in relationships):
            raise TypeError("observation relationships contain an invalid value")
        element_ids = [item.id for item in elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("observation element IDs must be unique")
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "relationships", relationships)
        object.__setattr__(
            self,
            "external_relationships",
            _string_tuple(self.external_relationships, "external_relationships"),
        )
        object.__setattr__(self, "errors", _string_tuple(self.errors, "errors"))

    def to_dict(self) -> dict[str, object]:
        return {
            "observer_name": self.observer_name,
            "observer_version": self.observer_version,
            "source_sha256": self.source_sha256,
            "package_readable": self.package_readable,
            "python_docx_reopenable": self.python_docx_reopenable,
            "elements": [element.to_dict() for element in self.elements],
            "relationships": [
                relationship.to_dict() for relationship in self.relationships
            ],
            "external_relationships": list(self.external_relationships),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DocxObservation:
        return cls(
            observer_name=data["observer_name"],
            observer_version=data["observer_version"],
            source_sha256=data["source_sha256"],
            package_readable=data["package_readable"],
            python_docx_reopenable=data["python_docx_reopenable"],
            elements=tuple(
                ObservedElement.from_dict(_mapping(item, "observed element"))
                for item in _sequence(data.get("elements", ()), "elements")
            ),
            relationships=tuple(
                StructuralRelationship.from_dict(
                    _mapping(item, "observed relationship")
                )
                for item in _sequence(data.get("relationships", ()), "relationships")
            ),
            external_relationships=tuple(
                _sequence(
                    data.get("external_relationships", ()),
                    "external_relationships",
                )
            ),
            errors=tuple(_sequence(data.get("errors", ()), "errors")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRegion:
    """One normalized region measured from a rendered DOCX page snapshot."""

    id: str
    element_type: ElementType
    bbox: NormalizedBoundingBox
    source_element_id: str | None = None
    observed_element_id: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.id, "snapshot region id")
        object.__setattr__(self, "element_type", ElementType(self.element_type))
        if not isinstance(self.bbox, NormalizedBoundingBox):
            raise TypeError("snapshot region bbox must be normalized")
        if self.source_element_id is not None:
            _non_empty(self.source_element_id, "snapshot source_element_id")
        if self.observed_element_id is not None:
            _non_empty(self.observed_element_id, "snapshot observed_element_id")
        if self.source_element_id is None and self.observed_element_id is None:
            raise ValueError(
                "snapshot region requires source_element_id or observed_element_id"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.element_type.value,
            "bbox": self.bbox.to_dict(),
            "source_element_id": self.source_element_id,
            "observed_element_id": self.observed_element_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SnapshotRegion:
        return cls(
            id=data["id"],
            element_type=ElementType(data["type"]),
            bbox=NormalizedBoundingBox.from_dict(
                _mapping(data["bbox"], "snapshot bbox")
            ),
            source_element_id=data.get("source_element_id"),
            observed_element_id=data.get("observed_element_id"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotObservation:
    """LibreOffice or equivalent evidence for the rendered DOCX layout."""

    renderer_name: str
    renderer_version: str
    available: bool
    opened_without_repair: bool | None
    regions: tuple[SnapshotRegion, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.renderer_name, "snapshot renderer_name")
        _non_empty(self.renderer_version, "snapshot renderer_version")
        object.__setattr__(self, "available", _boolean(self.available, "available"))
        if self.opened_without_repair is not None and not isinstance(
            self.opened_without_repair, bool
        ):
            raise TypeError("opened_without_repair must be bool or null")
        regions = tuple(self.regions)
        if any(not isinstance(item, SnapshotRegion) for item in regions):
            raise TypeError("snapshot regions contain an invalid value")
        ids = [item.id for item in regions]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot region IDs must be unique")
        object.__setattr__(self, "regions", regions)
        object.__setattr__(self, "errors", _string_tuple(self.errors, "errors"))

    def to_dict(self) -> dict[str, object]:
        return {
            "renderer_name": self.renderer_name,
            "renderer_version": self.renderer_version,
            "available": self.available,
            "opened_without_repair": self.opened_without_repair,
            "regions": [region.to_dict() for region in self.regions],
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SnapshotObservation:
        return cls(
            renderer_name=data["renderer_name"],
            renderer_version=data["renderer_version"],
            available=data["available"],
            opened_without_repair=data.get("opened_without_repair"),
            regions=tuple(
                SnapshotRegion.from_dict(_mapping(item, "snapshot region"))
                for item in _sequence(data.get("regions", ()), "regions")
            ),
            errors=tuple(_sequence(data.get("errors", ()), "errors")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RestorationEvaluationInput:
    """Complete normalized evidence consumed by the deterministic evaluator."""

    ir_version: str
    ir_schema_valid: bool
    reference: EvaluationReference
    observation: DocxObservation
    render_report: DocxRenderReport
    snapshot: SnapshotObservation | None = None
    completed_human_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.ir_version, "evaluation ir_version")
        object.__setattr__(
            self,
            "ir_schema_valid",
            _boolean(self.ir_schema_valid, "ir_schema_valid"),
        )
        if not isinstance(self.reference, EvaluationReference):
            raise TypeError("reference must be an EvaluationReference")
        if not isinstance(self.observation, DocxObservation):
            raise TypeError("observation must be a DocxObservation")
        if not isinstance(self.render_report, DocxRenderReport):
            raise TypeError("render_report must be a DocxRenderReport")
        if self.snapshot is not None and not isinstance(
            self.snapshot, SnapshotObservation
        ):
            raise TypeError("snapshot must be SnapshotObservation or null")
        object.__setattr__(
            self,
            "completed_human_checks",
            _string_tuple(self.completed_human_checks, "completed_human_checks"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentScore:
    """One weighted component of the 0..100 composite score."""

    name: str
    score: float
    weight: float

    @property
    def weighted_score(self) -> float:
        return round(self.score * self.weight, 6)

    def to_dict(self) -> dict[str, float]:
        return {
            "score": self.score,
            "weight": self.weight,
            "weighted_score": self.weighted_score,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ElementMatch:
    """One deterministic expected-to-observed element assignment."""

    reference_id: str
    observed_id: str
    similarity: float

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "observed_id": self.observed_id,
            "similarity": self.similarity,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class HardGateResult:
    """A mandatory gate; null means that machine evidence is insufficient."""

    name: str
    passed: bool | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        status = (
            "unknown" if self.passed is None else ("pass" if self.passed else "fail")
        )
        return {"name": self.name, "status": status, "reason": self.reason}


@dataclass(frozen=True, slots=True, kw_only=True)
class RestorationEvaluationResult:
    """Serializable score, hard gates, explanation, and final decision."""

    evaluator_name: str
    evaluator_version: str
    ir_version: str
    reference_id: str
    overall_score: float
    threshold: float
    components: tuple[ComponentScore, ...]
    matches: tuple[ElementMatch, ...]
    missing_element_ids: tuple[str, ...]
    unexpected_element_ids: tuple[str, ...]
    hard_gates: tuple[HardGateResult, ...]
    state: EvaluationState
    reasons: tuple[str, ...]
    required_human_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluator": {
                "name": self.evaluator_name,
                "version": self.evaluator_version,
            },
            "ir_version": self.ir_version,
            "reference_id": self.reference_id,
            "overall_score": self.overall_score,
            "threshold": self.threshold,
            "components": {
                component.name: component.to_dict() for component in self.components
            },
            "elements": {
                "matched": [match.to_dict() for match in self.matches],
                "missing": list(self.missing_element_ids),
                "unexpected": list(self.unexpected_element_ids),
            },
            "hard_gates": [gate.to_dict() for gate in self.hard_gates],
            "state": self.state.value,
            "reasons": list(self.reasons),
            "required_human_checks": list(self.required_human_checks),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


class DocxObservationError(RuntimeError):
    """Raised when a DOCX observation cannot start because input is unavailable."""


class EvaluationWriteError(RuntimeError):
    """Raised when evaluation.json cannot be published safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocxObserver(Protocol):
    """Adapter boundary for reading visible facts back from a generated DOCX."""

    def observe(self, docx_path: str | PathLike[str]) -> DocxObservation:
        """Return normalized evidence without consulting the source image."""


class EvaluationArtifactWriter(Protocol):
    """Adapter boundary for create-only publication of evaluation.json."""

    def write(
        self,
        result: RestorationEvaluationResult,
        output_path: str | PathLike[str],
    ) -> Path:
        """Publish one deterministic evaluation artifact without overwriting."""


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be an array")
    return value


__all__ = [
    "ComponentScore",
    "DocxObservation",
    "DocxObservationError",
    "DocxObserver",
    "ElementMatch",
    "EvaluationArtifactWriter",
    "EvaluationReference",
    "EvaluationState",
    "EvaluationWriteError",
    "HardGateResult",
    "NormalizedBoundingBox",
    "ObservedElement",
    "ReferenceElement",
    "RelationshipKind",
    "RestorationEvaluationInput",
    "RestorationEvaluationResult",
    "SnapshotObservation",
    "SnapshotRegion",
    "StructuralRelationship",
]
