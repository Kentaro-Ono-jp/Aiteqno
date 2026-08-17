import copy
import unittest
from dataclasses import replace
from pathlib import Path

from aiteqno.application import (
    SourceBaselineConfig,
    evaluate_source_baseline,
    normalize_source_text,
    source_character_accuracy,
)
from aiteqno.domain import DocumentIR, ElementType, TextElement
from aiteqno.ports import (
    EvaluationState,
    ManualCheckEvidence,
    ManualCheckStatus,
    NormalizedBoundingBox,
    RelationshipKind,
    SourceBaselineObservation,
    SourceBaselineReference,
    SourceRelationship,
    SourceStructuralItem,
    SourceTextRegion,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "document_ir"
    / "canonical.document.ir.json"
)
SOURCE_DIGEST = "b" * 64


def load_document() -> DocumentIR:
    return DocumentIR.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def normalized_bbox(element, page) -> NormalizedBoundingBox:
    x = element.bbox.x / page.size.width
    y = element.bbox.y / page.size.height
    width = max(element.bbox.width / page.size.width, 1 / page.size.width)
    height = max(element.bbox.height / page.size.height, 1 / page.size.height)
    return NormalizedBoundingBox(x=x, y=y, width=width, height=height)


def source_reference(*, reviewed: bool = True) -> SourceBaselineReference:
    document = load_document()
    page = document.pages[0]
    text = next(item for item in page.elements if isinstance(item, TextElement))
    structures = tuple(
        SourceStructuralItem(
            id=f"reviewed-structure-{index}",
            element_type=item.type,
            bbox=normalized_bbox(item, page),
            essential=item.type is ElementType.RECTANGLE,
        )
        for index, item in enumerate(
            (item for item in page.elements if not isinstance(item, TextElement))
        )
    )
    return SourceBaselineReference(
        reference_id="source-ground-truth-v1",
        source_sha256=SOURCE_DIGEST,
        reviewed=reviewed,
        text_regions=(
            SourceTextRegion(
                id="reviewed-title-block",
                text="問 診 票",
                bbox=normalized_bbox(text, page),
                essential=True,
            ),
        ),
        structural_items=structures,
        essential_text_anchors=("問 診 票",),
        expected_page_count=1,
        required_manual_checks=("visual_readability",),
    )


def source_observation(
    *,
    final_docx_text: str = "問診票",
    visible_rendered_text: str | None = "問 診 票",
    rendered_page_count: int | None = 1,
    manual_status: ManualCheckStatus = ManualCheckStatus.PASSED,
) -> SourceBaselineObservation:
    return SourceBaselineObservation(
        source_sha256=SOURCE_DIGEST,
        candidate_ir=load_document(),
        final_docx_text=final_docx_text,
        visible_rendered_text=visible_rendered_text,
        rendered_page_count=rendered_page_count,
        manual_checks=(
            ManualCheckEvidence(
                name="visual_readability",
                status=manual_status,
                note="reviewed rendered page",
            ),
        ),
    )


def relationship_document() -> DocumentIR:
    data = load_document().to_dict()
    page = data["pages"][0]
    title = page["elements"][0]
    second = copy.deepcopy(title)
    second["id"] = "candidate-second-text"
    second["bbox"] = {"x": 48, "y": 72, "width": 210, "height": 24}
    second["text"] = "受付番号"
    second["reading_order"] = 1
    contained = copy.deepcopy(title)
    contained["id"] = "candidate-contained-text"
    contained["bbox"] = {"x": 60, "y": 140, "width": 100, "height": 20}
    contained["text"] = "住所"
    contained["reading_order"] = 2
    page["elements"].extend((second, contained))
    return DocumentIR.from_dict(data)


def relationship_reference(document: DocumentIR) -> SourceBaselineReference:
    page = document.pages[0]
    text_elements = tuple(
        item for item in page.elements if isinstance(item, TextElement)
    )
    text_regions = tuple(
        SourceTextRegion(
            id=reference_id,
            text=element.text,
            bbox=normalized_bbox(element, page),
            essential=reference_id == "reviewed-title",
        )
        for reference_id, element in zip(
            ("reviewed-title", "reviewed-second", "reviewed-contained"),
            text_elements,
            strict=True,
        )
    )
    structural_items = tuple(
        SourceStructuralItem(
            id=f"reviewed-structure-{index}",
            element_type=item.type,
            bbox=normalized_bbox(item, page),
            essential=False,
        )
        for index, item in enumerate(
            (item for item in page.elements if not isinstance(item, TextElement))
        )
    )
    rectangle_id = next(
        item.id
        for item in structural_items
        if item.element_type is ElementType.RECTANGLE
    )
    return SourceBaselineReference(
        reference_id="relationship-source-ground-truth-v1",
        source_sha256=SOURCE_DIGEST,
        reviewed=True,
        text_regions=text_regions,
        structural_items=structural_items,
        relationships=(
            SourceRelationship(
                kind=RelationshipKind.READING_ORDER,
                source="reviewed-title",
                target="reviewed-second",
                essential=True,
            ),
            SourceRelationship(
                kind=RelationshipKind.ADJACENCY,
                source="reviewed-title",
                target="reviewed-second",
                essential=True,
            ),
            SourceRelationship(
                kind=RelationshipKind.CONTAINMENT,
                source=rectangle_id,
                target="reviewed-contained",
                essential=True,
            ),
        ),
        essential_text_anchors=("問診票",),
        required_manual_checks=("visual_readability",),
    )


def relationship_observation(document: DocumentIR) -> SourceBaselineObservation:
    return SourceBaselineObservation(
        source_sha256=SOURCE_DIGEST,
        candidate_ir=document,
        final_docx_text="問診票受付番号住所",
        visible_rendered_text="問診票 受付番号 住所",
        rendered_page_count=1,
        manual_checks=(
            ManualCheckEvidence(
                name="visual_readability",
                status=ManualCheckStatus.PASSED,
            ),
        ),
    )


class SourceBaselineTest(unittest.TestCase):
    def test_nfkc_whitespace_normalization_and_edit_distance_accuracy(self):
        self.assertEqual(normalize_source_text("Ａ B\nC\u3000"), "ABC")
        self.assertEqual(source_character_accuracy("Ａ B C", "ABC"), 100.0)
        self.assertEqual(source_character_accuracy("abc", "adc"), 66.666667)

    def test_independent_reference_ids_can_pass_on_content_and_geometry(self):
        result = evaluate_source_baseline(source_reference(), source_observation())

        self.assertEqual(result.state, EvaluationState.PASS)
        self.assertEqual(result.overall_score, 100.0)
        self.assertEqual(result.text_evidence, "rendered_visible")
        self.assertEqual(
            result.logical_blocks[0].candidate_element_ids,
            ("p001-text-0000",),
        )
        self.assertNotEqual(
            result.logical_blocks[0].reference_id,
            result.logical_blocks[0].candidate_element_ids[0],
        )
        self.assertTrue(all(item.matched for item in result.structural_items))
        self.assertTrue(all(component.passed for component in result.components))

    def test_visible_text_is_preferred_over_non_visual_docx_readback(self):
        result = evaluate_source_baseline(
            source_reference(),
            source_observation(
                final_docx_text="問診票",
                visible_rendered_text="fi hy qe He",
            ),
        )

        self.assertEqual(result.text_evidence, "rendered_visible")
        self.assertEqual(result.state, EvaluationState.FAIL)
        gates = {item.name: item for item in result.hard_gates}
        self.assertFalse(gates["essential_text_anchors"].passed)

    def test_captured_bad_output_is_a_truthful_failing_baseline(self):
        result = evaluate_source_baseline(
            source_reference(),
            source_observation(
                final_docx_text="fi hy qe He",
                visible_rendered_text="fi hy qe He",
                rendered_page_count=8,
                manual_status=ManualCheckStatus.PENDING,
            ),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertLess(result.overall_score, 70)
        self.assertIn(
            "component_below_minimum:text_accuracy:0<70",
            result.reasons,
        )
        gates = {item.name: item for item in result.hard_gates}
        self.assertFalse(gates["rendered_docx_page_count"].passed)
        self.assertIsNone(gates["manual_checks"].passed)

    def test_pending_manual_check_requires_review_only_when_other_checks_pass(self):
        result = evaluate_source_baseline(
            source_reference(),
            source_observation(manual_status=ManualCheckStatus.PENDING),
        )

        self.assertEqual(result.state, EvaluationState.REQUIRES_HUMAN_REVIEW)
        self.assertIn("hard_gate_unknown:manual_checks", result.reasons)

    def test_failed_manual_check_is_a_hard_failure(self):
        result = evaluate_source_baseline(
            source_reference(),
            source_observation(manual_status=ManualCheckStatus.FAILED),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn("hard_gate_failed:manual_checks", result.reasons)

    def test_unreviewed_reference_and_missing_page_evidence_require_review(self):
        result = evaluate_source_baseline(
            source_reference(reviewed=False),
            source_observation(rendered_page_count=None),
        )

        self.assertEqual(result.state, EvaluationState.REQUIRES_HUMAN_REVIEW)
        self.assertIn("hard_gate_unknown:reference_reviewed", result.reasons)
        self.assertIn("hard_gate_unknown:rendered_docx_page_count", result.reasons)

    def test_contracts_and_result_serialize_deterministically(self):
        reference = source_reference()
        observation = source_observation()
        reloaded_reference = SourceBaselineReference.from_json(reference.to_json())
        reloaded_observation = SourceBaselineObservation.from_json(
            observation.to_json()
        )

        self.assertEqual(reloaded_reference, reference)
        self.assertEqual(reloaded_observation, observation)
        result = evaluate_source_baseline(reference, observation)
        self.assertEqual(result.to_json(), result.to_json())
        self.assertEqual(
            SourceBaselineConfig().to_json(),
            SourceBaselineConfig().to_json(),
        )

    def test_source_digest_mismatch_fails_even_when_scores_are_perfect(self):
        observation = replace(source_observation(), source_sha256="c" * 64)

        result = evaluate_source_baseline(source_reference(), observation)

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn("hard_gate_failed:source_digest_matches", result.reasons)

    def test_source_relationships_use_candidate_order_and_geometry_not_ids(self):
        document = relationship_document()
        reference = relationship_reference(document)

        result = evaluate_source_baseline(
            reference,
            relationship_observation(document),
        )

        self.assertEqual(result.state, EvaluationState.PASS)
        self.assertEqual(len(result.relationships), 3)
        self.assertTrue(all(item.passed for item in result.relationships))
        self.assertTrue(
            all(
                item.source.startswith("reviewed-")
                and item.target.startswith("reviewed-")
                for item in result.relationships
            )
        )
        gates = {item.name: item for item in result.hard_gates}
        self.assertTrue(gates["essential_relationships"].passed)
        self.assertEqual(
            SourceBaselineReference.from_json(reference.to_json()),
            reference,
        )

    def test_missing_relationship_evidence_is_a_hard_failure(self):
        full_document = relationship_document()
        reference = relationship_reference(full_document)
        data = full_document.to_dict()
        elements = data["pages"][0]["elements"]
        data["pages"][0]["elements"] = [
            item for item in elements if item["id"] != "candidate-second-text"
        ]
        next(
            item
            for item in data["pages"][0]["elements"]
            if item["id"] == "candidate-contained-text"
        )["reading_order"] = 1
        incomplete_document = DocumentIR.from_dict(data)

        result = evaluate_source_baseline(
            reference,
            relationship_observation(incomplete_document),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn("hard_gate_failed:essential_relationships", result.reasons)
        failed_kinds = {item.kind for item in result.relationships if not item.passed}
        self.assertEqual(
            failed_kinds,
            {RelationshipKind.READING_ORDER, RelationshipKind.ADJACENCY},
        )

    def test_relationship_endpoints_must_exist_and_have_valid_roles(self):
        document = relationship_document()
        reference = relationship_reference(document)

        with self.assertRaisesRegex(ValueError, "endpoints are absent"):
            replace(
                reference,
                relationships=(
                    SourceRelationship(
                        kind=RelationshipKind.READING_ORDER,
                        source="reviewed-title",
                        target="missing-region",
                    ),
                ),
            )

    def test_relationship_endpoints_must_share_a_page(self):
        document = relationship_document()
        reference = relationship_reference(document)
        moved_second = replace(reference.text_regions[1], page_number=2)

        with self.assertRaisesRegex(ValueError, "must be on the same page"):
            replace(
                reference,
                text_regions=(
                    reference.text_regions[0],
                    moved_second,
                    reference.text_regions[2],
                ),
                expected_page_count=2,
            )
        with self.assertRaisesRegex(ValueError, "require two text regions"):
            replace(
                reference,
                relationships=(
                    SourceRelationship(
                        kind=RelationshipKind.ADJACENCY,
                        source=reference.structural_items[0].id,
                        target="reviewed-title",
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
