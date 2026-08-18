"""Deterministic geometry-only planning for same-row OCR region crops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

from aiteqno.domain import Confidence, PixelBoundingBox, Provenance
from aiteqno.ports.ocr_grouping import (
    OcrRegionGroupingConfig,
    OcrRegionGroupingEvidence,
)
from aiteqno.ports.structure import (
    LineCandidate,
    LineOrientation,
    RegionCandidate,
    RegionKind,
)


OCR_REGION_GROUPING_ALGORITHM = "source-geometry-same-row-adjacent"
OCR_REGION_GROUPING_ALGORITHM_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class PlannedOcrRegion:
    """One singleton or union region passed to the OCR backend."""

    region_ref: str
    region: RegionCandidate
    member_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.region_ref, str) or not self.region_ref:
            raise ValueError("planned OCR region_ref must be non-empty")
        if not isinstance(self.region, RegionCandidate):
            raise TypeError("planned OCR region must be a RegionCandidate")
        if self.region.kind is not RegionKind.TEXT:
            raise ValueError("planned OCR region must be a text region")
        if not self.member_refs or any(
            not isinstance(value, str) or not value for value in self.member_refs
        ):
            raise ValueError("planned OCR member_refs must be non-empty strings")
        if len(self.member_refs) != len(set(self.member_refs)):
            raise ValueError("planned OCR member_refs must not contain duplicates")


@dataclass(frozen=True, slots=True)
class OcrRegionGroupingPlan:
    """Planned OCR regions and their complete audit evidence."""

    regions: tuple[PlannedOcrRegion, ...]
    evidence: OcrRegionGroupingEvidence


@dataclass(slots=True)
class _RegionRow:
    entries: list[tuple[str, RegionCandidate]]
    top: int
    bottom: int
    left: int


def plan_ocr_regions(
    region_entries: Sequence[tuple[str, RegionCandidate]],
    lines: Sequence[LineCandidate],
    *,
    config: OcrRegionGroupingConfig = OcrRegionGroupingConfig(),
) -> OcrRegionGroupingPlan:
    """Return stable singleton/union crops without consulting OCR observations."""

    if not isinstance(config, OcrRegionGroupingConfig):
        raise TypeError("config must be an OcrRegionGroupingConfig")
    if isinstance(region_entries, (str, bytes, bytearray)):
        raise TypeError("region_entries must be a sequence")
    if isinstance(lines, (str, bytes, bytearray)):
        raise TypeError("lines must be a sequence")
    entries = tuple(region_entries)
    if any(
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not value[0]
        or not isinstance(value[1], RegionCandidate)
        for value in entries
    ):
        raise TypeError(
            "region_entries must contain (non-empty str, RegionCandidate) tuples"
        )
    if any(value[1].kind is not RegionKind.TEXT for value in entries):
        raise ValueError("region_entries must contain only text regions")
    refs = tuple(value[0] for value in entries)
    if len(refs) != len(set(refs)):
        raise ValueError("region_entries must have unique references")
    collected_lines = tuple(lines)
    if any(not isinstance(value, LineCandidate) for value in collected_lines):
        raise TypeError("lines must contain only LineCandidate values")

    ordered = tuple(sorted(entries, key=_entry_position_key))
    separators = tuple(
        sorted(
            (
                value
                for value in collected_lines
                if value.orientation is LineOrientation.VERTICAL
            ),
            key=_line_position_key,
        )
    )
    separator_entries = tuple(
        (f"p001-vertical-separator-{index:04d}", value)
        for index, value in enumerate(separators)
    )

    adjacency_decisions: list[dict[str, object]] = []
    components: list[tuple[tuple[str, RegionCandidate], ...]] = []
    if config.enabled:
        for row_index, row in enumerate(_region_rows(ordered, config)):
            current: list[tuple[str, RegionCandidate]] = []
            for entry in sorted(row.entries, key=_entry_horizontal_key):
                if not current:
                    current.append(entry)
                    continue
                decision = _adjacency_decision(
                    current[-1],
                    entry,
                    row_index=row_index,
                    separators=separator_entries,
                    config=config,
                )
                adjacency_decisions.append(decision)
                if decision["grouped"] is True:
                    current.append(entry)
                else:
                    components.append(tuple(current))
                    current = [entry]
            if current:
                components.append(tuple(current))
    else:
        components.extend((entry,) for entry in ordered)

    components.sort(key=_component_position_key)
    planned: list[PlannedOcrRegion] = []
    groups: list[dict[str, object]] = []
    singleton_refs: list[str] = []
    group_index = 0
    for component in components:
        member_refs = tuple(value[0] for value in component)
        if len(component) == 1:
            region_ref, region = component[0]
            planned.append(
                PlannedOcrRegion(
                    region_ref=region_ref,
                    region=region,
                    member_refs=member_refs,
                )
            )
            singleton_refs.append(region_ref)
            continue
        region_ref = f"p001-text-line-group-{group_index:04d}"
        group_index += 1
        union_region = _union_region(component)
        planned.append(
            PlannedOcrRegion(
                region_ref=region_ref,
                region=union_region,
                member_refs=member_refs,
            )
        )
        member_links = [
            value
            for value in adjacency_decisions
            if value["left_region_ref"] in member_refs
            and value["right_region_ref"] in member_refs
            and value["grouped"] is True
        ]
        groups.append(
            {
                "region_ref": region_ref,
                "member_refs": list(member_refs),
                "member_bboxes": [_bbox_dict(value[1].bbox) for value in component],
                "union_bbox": _bbox_dict(union_region.bbox),
                "adjacency_links": member_links,
            }
        )

    source_records = tuple(
        {
            "region_ref": region_ref,
            "bbox": _bbox_dict(region.bbox),
        }
        for region_ref, region in ordered
    )
    separator_records = tuple(
        {
            "separator_ref": separator_ref,
            "bbox": _bbox_dict(line.bbox),
            "start": {"x": line.start.x, "y": line.start.y},
            "end": {"x": line.end.x, "y": line.end.y},
        }
        for separator_ref, line in separator_entries
    )
    planned_records = tuple(
        {
            "region_ref": value.region_ref,
            "kind": "singleton" if len(value.member_refs) == 1 else "group",
            "member_refs": list(value.member_refs),
            "bbox": _bbox_dict(value.region.bbox),
        }
        for value in planned
    )
    configuration = config.to_dict()
    configuration_digest = config.digest(
        algorithm=OCR_REGION_GROUPING_ALGORITHM,
        algorithm_version=OCR_REGION_GROUPING_ALGORITHM_VERSION,
    )
    evidence_without_digest = {
        "schema_version": "1.0",
        "algorithm": OCR_REGION_GROUPING_ALGORITHM,
        "algorithm_version": OCR_REGION_GROUPING_ALGORITHM_VERSION,
        "configuration": configuration,
        "configuration_digest": configuration_digest,
        "source_regions": list(source_records),
        "vertical_separators": list(separator_records),
        "planned_regions": list(planned_records),
        "groups": groups,
        "adjacency_decisions": adjacency_decisions,
        "singleton_region_refs": singleton_refs,
    }
    plan_digest = _json_sha256(evidence_without_digest)
    evidence = OcrRegionGroupingEvidence(
        schema_version="1.0",
        algorithm=OCR_REGION_GROUPING_ALGORITHM,
        algorithm_version=OCR_REGION_GROUPING_ALGORITHM_VERSION,
        configuration=configuration,
        configuration_digest=configuration_digest,
        source_regions=source_records,
        vertical_separators=separator_records,
        planned_regions=planned_records,
        groups=tuple(groups),
        adjacency_decisions=tuple(adjacency_decisions),
        singleton_region_refs=tuple(singleton_refs),
        plan_digest=plan_digest,
    )
    return OcrRegionGroupingPlan(regions=tuple(planned), evidence=evidence)


def _region_rows(
    entries: Sequence[tuple[str, RegionCandidate]],
    config: OcrRegionGroupingConfig,
) -> tuple[_RegionRow, ...]:
    rows: list[_RegionRow] = []
    for entry in entries:
        bbox = entry[1].bbox
        matches: list[tuple[float, int, _RegionRow]] = []
        for index, row in enumerate(rows):
            overlap = max(
                0,
                min(bbox.y + bbox.height, row.bottom) - max(bbox.y, row.top),
            )
            denominator = min(bbox.height, row.bottom - row.top)
            ratio = overlap / denominator if denominator else 0.0
            if ratio >= config.minimum_vertical_overlap_ratio:
                matches.append((ratio, -index, row))
        if matches:
            row = max(matches, key=lambda value: (value[0], value[1]))[2]
            row.entries.append(entry)
            row.top = min(row.top, bbox.y)
            row.bottom = max(row.bottom, bbox.y + bbox.height)
            row.left = min(row.left, bbox.x)
        else:
            rows.append(
                _RegionRow(
                    entries=[entry],
                    top=bbox.y,
                    bottom=bbox.y + bbox.height,
                    left=bbox.x,
                )
            )
    rows.sort(key=lambda value: (value.top, value.left, value.bottom))
    return tuple(rows)


def _adjacency_decision(
    left: tuple[str, RegionCandidate],
    right: tuple[str, RegionCandidate],
    *,
    row_index: int,
    separators: Sequence[tuple[str, LineCandidate]],
    config: OcrRegionGroupingConfig,
) -> dict[str, object]:
    left_bbox = left[1].bbox
    right_bbox = right[1].bbox
    raw_gap = right_bbox.x - (left_bbox.x + left_bbox.width)
    horizontal_gap = max(0, raw_gap)
    maximum_gap = max(left_bbox.height, right_bbox.height) * (
        config.maximum_horizontal_gap_height_ratio
    )
    vertical_overlap = max(
        0,
        min(left_bbox.y + left_bbox.height, right_bbox.y + right_bbox.height)
        - max(left_bbox.y, right_bbox.y),
    )
    overlap_ratio = vertical_overlap / min(left_bbox.height, right_bbox.height)
    blocking_separators = tuple(
        separator_ref
        for separator_ref, separator in separators
        if config.block_vertical_separators
        and _separator_crosses_gap(separator, left_bbox, right_bbox)
    )
    reasons: list[str] = []
    if overlap_ratio < config.minimum_vertical_overlap_ratio:
        reasons.append("vertical_overlap_below_minimum")
    if horizontal_gap > maximum_gap:
        reasons.append("horizontal_gap_above_maximum")
    if blocking_separators:
        reasons.append("vertical_separator_crosses_gap")
    grouped = not reasons
    return {
        "row_index": row_index,
        "left_region_ref": left[0],
        "right_region_ref": right[0],
        "left_bbox": _bbox_dict(left_bbox),
        "right_bbox": _bbox_dict(right_bbox),
        "horizontal_gap_px": horizontal_gap,
        "maximum_horizontal_gap_px": round(maximum_gap, 6),
        "vertical_overlap_ratio": round(overlap_ratio, 6),
        "blocking_separator_refs": list(blocking_separators),
        "grouped": grouped,
        "reasons": reasons or ["fixed_geometry_rule_passed"],
    }


def _separator_crosses_gap(
    separator: LineCandidate,
    left: PixelBoundingBox,
    right: PixelBoundingBox,
) -> bool:
    gap_left = left.x + left.width
    gap_right = right.x
    if gap_right < gap_left:
        return False
    separator_x = separator.start.x
    if not gap_left <= separator_x <= gap_right:
        return False
    overlap_top = max(left.y, right.y)
    overlap_bottom = min(left.y + left.height, right.y + right.height)
    separator_top = min(separator.start.y, separator.end.y)
    separator_bottom = max(separator.start.y, separator.end.y)
    return separator_top <= overlap_top and separator_bottom >= overlap_bottom


def _union_region(
    component: Sequence[tuple[str, RegionCandidate]],
) -> RegionCandidate:
    regions = tuple(value[1] for value in component)
    left = min(value.bbox.x for value in regions)
    top = min(value.bbox.y for value in regions)
    right = max(value.bbox.x + value.bbox.width for value in regions)
    bottom = max(value.bbox.y + value.bbox.height for value in regions)
    return RegionCandidate(
        kind=RegionKind.TEXT,
        bbox=PixelBoundingBox(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        ),
        confidence=Confidence(
            overall=min(value.confidence.overall for value in regions),
            detection=_minimum_optional(
                value.confidence.detection for value in regions
            ),
            recognition=_minimum_optional(
                value.confidence.recognition for value in regions
            ),
        ),
        provenance=_unique_provenance(regions),
    )


def _minimum_optional(values: Sequence[float | None]) -> float | None:
    present = tuple(value for value in values if value is not None)
    return min(present) if present else None


def _unique_provenance(regions: Sequence[RegionCandidate]) -> tuple[Provenance, ...]:
    result: list[Provenance] = []
    for region in regions:
        for record in region.provenance:
            if record not in result:
                result.append(record)
    return tuple(result)


def _component_position_key(
    component: Sequence[tuple[str, RegionCandidate]],
) -> tuple[object, ...]:
    bboxes = tuple(value[1].bbox for value in component)
    return (
        min(value.y for value in bboxes),
        min(value.x for value in bboxes),
        max(value.y + value.height for value in bboxes),
        max(value.x + value.width for value in bboxes),
        tuple(value[0] for value in component),
    )


def _entry_position_key(
    entry: tuple[str, RegionCandidate],
) -> tuple[object, ...]:
    bbox = entry[1].bbox
    return (bbox.y, bbox.x, bbox.width, bbox.height, entry[0])


def _entry_horizontal_key(
    entry: tuple[str, RegionCandidate],
) -> tuple[object, ...]:
    bbox = entry[1].bbox
    return (bbox.x, bbox.y, bbox.width, bbox.height, entry[0])


def _line_position_key(line: LineCandidate) -> tuple[object, ...]:
    return (
        line.start.x,
        line.start.y,
        line.end.y,
        line.bbox.x,
        line.bbox.y,
        line.bbox.width,
        line.bbox.height,
    )


def _bbox_dict(value: PixelBoundingBox) -> dict[str, int]:
    return {
        "x": value.x,
        "y": value.y,
        "width": value.width,
        "height": value.height,
    }


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "OCR_REGION_GROUPING_ALGORITHM",
    "OCR_REGION_GROUPING_ALGORITHM_VERSION",
    "OcrRegionGroupingPlan",
    "PlannedOcrRegion",
    "plan_ocr_regions",
]
