from __future__ import annotations

import unittest

from aiteqno.application import plan_ocr_regions
from aiteqno.domain import (
    Confidence,
    PixelBoundingBox,
    Provenance,
    ProvenanceStage,
)
from aiteqno.ports import (
    LineCandidate,
    LineOrientation,
    OcrRegionGroupingConfig,
    PixelPoint,
    RegionCandidate,
    RegionKind,
)


def region(x: int, y: int, width: int, height: int) -> RegionCandidate:
    bbox = PixelBoundingBox(x=x, y=y, width=width, height=height)
    return RegionCandidate(
        kind=RegionKind.TEXT,
        bbox=bbox,
        confidence=Confidence(overall=0.9, detection=0.9),
        provenance=(
            Provenance(
                stage=ProvenanceStage.STRUCTURE,
                provider="test-structure",
                provider_version="1.0",
                source_bbox_px=bbox,
            ),
        ),
    )


def vertical_line(x: int, top: int, bottom: int) -> LineCandidate:
    bbox = PixelBoundingBox(x=x, y=top, width=1, height=bottom - top + 1)
    return LineCandidate(
        orientation=LineOrientation.VERTICAL,
        start=PixelPoint(x=x, y=top),
        end=PixelPoint(x=x, y=bottom),
        bbox=bbox,
        confidence=Confidence(overall=0.95, detection=0.95),
        provenance=(
            Provenance(
                stage=ProvenanceStage.STRUCTURE,
                provider="test-structure",
                provider_version="1.0",
                source_bbox_px=bbox,
            ),
        ),
    )


class OcrRegionGroupingPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = (
            ("title-1", region(10, 10, 10, 10)),
            ("title-2", region(25, 10, 10, 10)),
            ("far", region(60, 10, 10, 10)),
            ("label", region(10, 40, 10, 10)),
            ("value", region(30, 40, 10, 10)),
            ("phone", region(10, 70, 10, 10)),
        )
        self.lines = (vertical_line(25, 35, 55),)
        self.config = OcrRegionGroupingConfig(enabled=True)

    def test_fixed_geometry_rule_groups_adjacent_fragments_only(self) -> None:
        plan = plan_ocr_regions(self.entries, self.lines, config=self.config)

        grouped = tuple(value for value in plan.regions if len(value.member_refs) > 1)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].member_refs, ("title-1", "title-2"))
        self.assertEqual(
            grouped[0].region.bbox,
            PixelBoundingBox(x=10, y=10, width=25, height=10),
        )
        self.assertEqual(
            plan.evidence.singleton_region_refs,
            ("far", "label", "value", "phone"),
        )
        blocked = next(
            value
            for value in plan.evidence.adjacency_decisions
            if value["left_region_ref"] == "label"
        )
        self.assertFalse(blocked["grouped"])
        self.assertEqual(
            blocked["reasons"],
            ["vertical_separator_crosses_gap"],
        )
        self.assertEqual(
            blocked["blocking_separator_refs"],
            ["p001-vertical-separator-0000"],
        )

    def test_plan_and_evidence_are_deterministic_for_shuffled_inputs(self) -> None:
        first = plan_ocr_regions(self.entries, self.lines, config=self.config)
        second = plan_ocr_regions(
            tuple(reversed(self.entries)),
            tuple(reversed(self.lines)),
            config=self.config,
        )

        self.assertEqual(first.regions, second.regions)
        self.assertEqual(first.evidence.to_dict(), second.evidence.to_dict())
        self.assertEqual(first.evidence.plan_digest, second.evidence.plan_digest)

    def test_disabled_plan_preserves_every_original_region(self) -> None:
        plan = plan_ocr_regions(
            self.entries,
            self.lines,
            config=OcrRegionGroupingConfig(enabled=False),
        )

        self.assertEqual(
            tuple(value.region_ref for value in plan.regions),
            tuple(value[0] for value in self.entries),
        )
        self.assertEqual(
            tuple(value.region for value in plan.regions),
            tuple(value[1] for value in self.entries),
        )
        self.assertEqual(plan.evidence.groups, ())
        self.assertEqual(plan.evidence.adjacency_decisions, ())
        self.assertEqual(
            plan.evidence.singleton_region_refs,
            tuple(value[0] for value in self.entries),
        )

    def test_configuration_rejects_non_fixed_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            OcrRegionGroupingConfig(minimum_vertical_overlap_ratio=0)
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            OcrRegionGroupingConfig(maximum_horizontal_gap_height_ratio=-1)
        with self.assertRaisesRegex(TypeError, "must be a boolean"):
            OcrRegionGroupingConfig(enabled=1)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
