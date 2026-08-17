from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from aiteqno.application import (
    OcrQualityConfig,
    evaluate_ocr_quality,
    normalize_ocr_text,
    ocr_character_accuracy,
)
from aiteqno.domain import DocumentIR
from aiteqno.ports import (
    EvaluationState,
    NormalizedBoundingBox,
    OcrQualityObservation,
    OcrRuntimeEvidence,
    OcrTrainedDataEvidence,
    SourceBaselineReference,
    SourceTextRegion,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "document_ir"
    / "canonical.document.ir.json"
)
SOURCE_SHA256 = "a" * 64


def runtime_evidence() -> OcrRuntimeEvidence:
    return OcrRuntimeEvidence(
        provider="tesseract",
        provider_version="5.5.0-test",
        executable="/synthetic/bin/tesseract",
        languages=("jpn", "eng"),
        page_segmentation_mode=6,
        engine_mode=3,
        effective_ocr_dpi=96,
        source_dpi_x=96,
        source_dpi_y=96,
        traineddata=(
            OcrTrainedDataEvidence(
                language="jpn",
                size_bytes=100,
                sha256="1" * 64,
            ),
            OcrTrainedDataEvidence(
                language="eng",
                size_bytes=200,
                sha256="2" * 64,
            ),
        ),
        operating_system="SyntheticOS 1.0",
        python_version="3.14.0-test",
    )


def reference(
    *,
    reviewed: bool = True,
    essential_blocks: bool = True,
) -> SourceBaselineReference:
    return SourceBaselineReference(
        reference_id="ocr-source-v1",
        source_sha256=SOURCE_SHA256,
        reviewed=reviewed,
        text_regions=(
            SourceTextRegion(
                id="heading",
                text="文 書 解 析",
                bbox=NormalizedBoundingBox(
                    x=0.05,
                    y=0.03,
                    width=0.50,
                    height=0.08,
                ),
                essential=essential_blocks,
            ),
            SourceTextRegion(
                id="format",
                text="対象形式",
                bbox=NormalizedBoundingBox(
                    x=0.05,
                    y=0.15,
                    width=0.50,
                    height=0.08,
                ),
                essential=essential_blocks,
            ),
        ),
        essential_text_anchors=("文書解析", "対象形式"),
        expected_page_count=1,
    )


def document(
    tokens: tuple[tuple[str, str, float, float, float | None], ...],
) -> DocumentIR:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    page = data["pages"][0]
    template = next(
        element for element in page["elements"] if element["type"] == "text"
    )
    elements = []
    for reading_order, (identifier, text, x, y, confidence) in enumerate(tokens):
        element = copy.deepcopy(template)
        element["id"] = identifier
        element["text"] = text
        element["reading_order"] = reading_order
        element["bbox"] = {"x": x, "y": y, "width": 55, "height": 20}
        element["confidence"] = (
            None
            if confidence is None
            else {
                "overall": confidence,
                "detection": min(1.0, confidence + 0.05),
                "recognition": confidence,
            }
        )
        elements.append(element)
    page["elements"] = elements
    return DocumentIR.from_dict(data)


def observation(
    candidate: DocumentIR,
    *,
    source_sha256: str = SOURCE_SHA256,
) -> OcrQualityObservation:
    return OcrQualityObservation(
        source_sha256=source_sha256,
        candidate_ir=candidate,
        runtime=runtime_evidence(),
    )


def score_tuple(result) -> tuple[float, float, float, EvaluationState]:
    return (
        result.text_character_accuracy.score,
        result.logical_block_coverage.score,
        result.essential_anchor_recall.score,
        result.state,
    )


class OcrQualityTest(unittest.TestCase):
    def test_nfkc_whitespace_normalization_and_character_accuracy(self) -> None:
        self.assertEqual(normalize_ocr_text("Ａ B\nC\u3000"), "ABC")
        self.assertEqual(ocr_character_accuracy("Ａ B C", "ABC"), 100.0)
        self.assertEqual(ocr_character_accuracy("abc", "adc"), 66.666667)

    def test_split_and_merged_tokens_have_identical_quality_scores(self) -> None:
        split = document(
            (
                ("split-a", "文書", 40, 40, 0.9),
                ("split-b", "解析", 100, 40, 0.9),
                ("split-c", "対象形式", 40, 140, 0.9),
            )
        )
        merged = document(
            (
                ("merged-heading", "文書解析", 40, 40, 0.9),
                ("merged-format", "対象形式", 40, 140, 0.9),
            )
        )

        split_result = evaluate_ocr_quality(reference(), observation(split))
        merged_result = evaluate_ocr_quality(reference(), observation(merged))

        self.assertEqual(score_tuple(split_result), score_tuple(merged_result))
        self.assertEqual(
            score_tuple(split_result), (100.0, 100.0, 100.0, EvaluationState.PASS)
        )
        self.assertEqual(
            tuple(item.observed_text for item in split_result.blocks),
            tuple(item.observed_text for item in merged_result.blocks),
        )

    def test_nfkc_composition_across_token_boundary_is_segmentation_independent(
        self,
    ) -> None:
        composition_reference = SourceBaselineReference(
            reference_id="ocr-nfkc-composition-v1",
            source_sha256=SOURCE_SHA256,
            reviewed=True,
            text_regions=(
                SourceTextRegion(
                    id="composed-block",
                    text="ガ",
                    bbox=NormalizedBoundingBox(
                        x=0.05,
                        y=0.03,
                        width=0.50,
                        height=0.08,
                    ),
                    essential=True,
                ),
            ),
            essential_text_anchors=("ガ",),
            expected_page_count=1,
        )
        merged = document((("merged", "ガ", 40, 40, 0.9),))
        split = document(
            (
                ("split-base", "カ", 40, 40, 0.9),
                ("split-mark", "\u3099", 80, 40, 0.9),
            )
        )

        merged_result = evaluate_ocr_quality(
            composition_reference,
            observation(merged),
        )
        split_result = evaluate_ocr_quality(
            composition_reference,
            observation(split),
        )

        self.assertEqual(score_tuple(merged_result), score_tuple(split_result))
        self.assertEqual(merged_result.observed_text, "ガ")
        self.assertEqual(split_result.observed_text, "ガ")
        self.assertEqual(merged_result.blocks[0].observed_text, "ガ")
        self.assertEqual(split_result.blocks[0].observed_text, "ガ")
        self.assertEqual(merged_result.state, EvaluationState.PASS)
        self.assertEqual(split_result.state, EvaluationState.PASS)

    def test_candidate_element_ids_do_not_change_quality_scores(self) -> None:
        first = document(
            (
                ("first-heading", "文書解析", 40, 40, 0.8),
                ("first-format", "対象形式", 40, 140, 0.8),
            )
        )
        second = document(
            (
                ("unrelated-001", "文書解析", 40, 40, 0.8),
                ("unrelated-999", "対象形式", 40, 140, 0.8),
            )
        )

        first_result = evaluate_ocr_quality(reference(), observation(first))
        second_result = evaluate_ocr_quality(reference(), observation(second))

        self.assertEqual(score_tuple(first_result), score_tuple(second_result))
        self.assertEqual(
            tuple(item.character_accuracy for item in first_result.blocks),
            tuple(item.character_accuracy for item in second_result.blocks),
        )

    def test_source_digest_mismatch_is_a_hard_failure(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.9),
                ("format", "対象形式", 40, 140, 0.9),
            )
        )

        result = evaluate_ocr_quality(
            reference(),
            observation(candidate, source_sha256="b" * 64),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        digest_gate = next(
            gate
            for gate in result.hard_gates
            if gate.name == "source_digest_matches_reference"
        )
        self.assertFalse(digest_gate.passed)
        self.assertIn(
            "hard_gate_failed:source_digest_matches_reference",
            result.reasons,
        )

    def test_unreviewed_reference_is_a_hard_failure(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.9),
                ("format", "対象形式", 40, 140, 0.9),
            )
        )

        result = evaluate_ocr_quality(
            reference(reviewed=False),
            observation(candidate),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        reviewed_gate = next(
            gate for gate in result.hard_gates if gate.name == "reference_reviewed"
        )
        self.assertFalse(reviewed_gate.passed)

    def test_missing_anchor_hard_fails_despite_other_metric_minima(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.95),
                ("format", "対象形", 40, 140, 0.95),
            )
        )

        result = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertGreaterEqual(result.text_character_accuracy.score, 70)
        self.assertEqual(result.logical_block_coverage.score, 100.0)
        self.assertEqual(result.essential_anchor_recall.score, 50.0)
        self.assertEqual(result.state, EvaluationState.FAIL)
        anchor = next(item for item in result.anchors if item.anchor == "対象形式")
        self.assertFalse(anchor.recovered)

    def test_essential_anchor_hard_gate_cannot_be_configured_away(self) -> None:
        candidate = document((("wrong", "完全な誤読", 40, 40, 0.9),))
        config = OcrQualityConfig(
            minimum_text_accuracy=0,
            minimum_logical_block_coverage=0,
            required_anchor_recall=0,
            logical_block_accuracy_threshold=0,
        )

        result = evaluate_ocr_quality(
            reference(essential_blocks=False),
            observation(candidate),
            config=config,
        )

        anchor_gate = next(
            gate for gate in result.hard_gates if gate.name == "essential_anchor_recall"
        )
        self.assertFalse(anchor_gate.passed)
        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn(
            "hard_gate_failed:essential_anchor_recall",
            result.reasons,
        )

    def test_missing_essential_block_is_a_hard_failure(self) -> None:
        candidate = document((("heading", "文書解析対象形式", 40, 40, 0.9),))
        config = OcrQualityConfig(
            minimum_text_accuracy=0,
            minimum_logical_block_coverage=0,
            required_anchor_recall=0,
        )

        result = evaluate_ocr_quality(
            reference(),
            observation(candidate),
            config=config,
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn("format", result.unrecovered_blocks)
        essential_gate = next(
            gate
            for gate in result.hard_gates
            if gate.name == "essential_logical_blocks"
        )
        self.assertFalse(essential_gate.passed)

    def test_candidate_page_count_is_a_hard_gate(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.9),
                ("format", "対象形式", 40, 140, 0.9),
            )
        )
        data = candidate.to_dict()
        second_page = copy.deepcopy(data["pages"][0])
        second_page["id"] = "page-002"
        second_page["number"] = 2
        for element in second_page["elements"]:
            element["id"] += "-page-2"
        data["pages"].append(second_page)

        result = evaluate_ocr_quality(
            reference(essential_blocks=False),
            observation(DocumentIR.from_dict(data)),
            config=OcrQualityConfig(
                minimum_text_accuracy=0,
                minimum_logical_block_coverage=0,
                required_anchor_recall=0,
                logical_block_accuracy_threshold=0,
            ),
        )

        self.assertEqual(result.state, EvaluationState.FAIL)
        page_gate = next(
            gate for gate in result.hard_gates if gate.name == "candidate_page_count"
        )
        self.assertFalse(page_gate.passed)

    def test_text_below_seventy_fails_without_an_overall_compensation(self) -> None:
        candidate = document(
            (
                ("heading", "完全な誤読", 40, 40, 0.99),
                ("format", "別の誤読", 40, 140, 0.99),
            )
        )
        config = OcrQualityConfig(
            minimum_logical_block_coverage=0,
            required_anchor_recall=0,
            logical_block_accuracy_threshold=0,
        )

        result = evaluate_ocr_quality(
            reference(essential_blocks=False),
            observation(candidate),
            config=config,
        )

        self.assertLess(result.text_character_accuracy.score, 70)
        self.assertEqual(result.logical_block_coverage.score, 100.0)
        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertNotIn("overall_score", result.to_dict())

    def test_high_confidence_misread_is_not_treated_as_truth(self) -> None:
        candidate = document(
            (
                ("heading", "誤読誤読", 40, 40, 0.99),
                ("format", "誤読誤読", 40, 140, 0.99),
            )
        )

        result = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertEqual(result.confidence_distribution.maximum, 0.99)
        self.assertLess(result.text_character_accuracy.score, 70)

    def test_low_confidence_correct_text_passes_and_is_diagnostic_only(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.1),
                ("format", "対象形式", 40, 140, 0.2),
            )
        )

        result = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertEqual(result.state, EvaluationState.PASS)
        self.assertEqual(len(result.low_confidence_tokens), 2)
        self.assertEqual(result.confidence_distribution.mean, 0.15)
        report = result.to_dict()
        self.assertFalse(report["diagnostics"]["confidence_is_scoring_input"])

    def test_missing_extra_and_unrecovered_diagnostics_are_explicit(self) -> None:
        candidate = document(
            (
                ("heading", "文書誤析", 40, 40, None),
                ("format", "対象形式余剰", 40, 140, None),
            )
        )

        result = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertTrue(result.missing_strings)
        self.assertTrue(result.extra_strings)
        self.assertEqual(result.confidence_distribution.available_count, 0)
        self.assertEqual(result.confidence_distribution.missing_count, 2)

    def test_overall_without_recognition_is_missing_ocr_confidence(self) -> None:
        data = document((("heading", "文書解析", 40, 40, 0.9),)).to_dict()
        confidence = data["pages"][0]["elements"][0]["confidence"]
        del confidence["recognition"]
        candidate = DocumentIR.from_dict(data)

        result = evaluate_ocr_quality(
            reference(essential_blocks=False),
            observation(candidate),
            config=OcrQualityConfig(
                minimum_text_accuracy=0,
                minimum_logical_block_coverage=0,
                required_anchor_recall=0,
            ),
        )

        self.assertEqual(result.confidence_distribution.available_count, 0)
        self.assertEqual(result.confidence_distribution.missing_count, 1)
        self.assertEqual(result.low_confidence_tokens, ())

    def test_unassigned_extra_token_still_reduces_full_text_accuracy(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.9),
                ("format", "対象形式", 40, 140, 0.9),
                ("outside", "余剰", 400, 400, 0.9),
            )
        )

        result = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertEqual(result.logical_block_coverage.score, 100.0)
        self.assertLess(result.text_character_accuracy.score, 100.0)
        self.assertIn("余剰", result.extra_strings)

    def test_report_is_deterministic_and_explicitly_candidate_ir_only(self) -> None:
        candidate = document(
            (
                ("heading", "文書解析", 40, 40, 0.7),
                ("format", "対象形式", 40, 140, 0.8),
            )
        )

        first = evaluate_ocr_quality(reference(), observation(candidate))
        second = evaluate_ocr_quality(reference(), observation(candidate))

        self.assertEqual(first.to_json(), second.to_json())
        report = json.loads(first.to_json())
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["scope"]["text_source"], "candidate_ir")
        self.assertNotIn("docx", report["full_text"])
        self.assertEqual(
            report["runtime"]["configuration"]["languages"], ["jpn", "eng"]
        )
        self.assertEqual(
            report["runtime"]["diagnostics"]["executable"],
            "/synthetic/bin/tesseract",
        )
        self.assertEqual(report["runtime"]["traineddata"][0]["sha256"], "1" * 64)

    def test_runtime_language_and_traineddata_order_must_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "same order"):
            OcrRuntimeEvidence(
                provider="tesseract",
                provider_version="5.5.0-test",
                executable="/synthetic/bin/tesseract",
                languages=("jpn", "eng"),
                page_segmentation_mode=6,
                engine_mode=3,
                effective_ocr_dpi=96,
                source_dpi_x=96,
                source_dpi_y=96,
                traineddata=(
                    OcrTrainedDataEvidence(
                        language="eng", size_bytes=1, sha256="1" * 64
                    ),
                    OcrTrainedDataEvidence(
                        language="jpn", size_bytes=1, sha256="2" * 64
                    ),
                ),
                operating_system="SyntheticOS 1.0",
                python_version="3.14.0-test",
            )


if __name__ == "__main__":
    unittest.main()
