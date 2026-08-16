import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from aiteqno.adapters import (
    BundleAssetResolver,
    FilesystemEvaluationWriter,
    PythonDocxObserver,
    PythonDocxRenderer,
)
from aiteqno.application import (
    EvaluationConfig,
    build_evaluation_reference,
    evaluate_restoration,
    evaluate_restoration_input,
    render_docx,
)
from aiteqno.domain import DocumentIR, ElementType
from aiteqno.ports import (
    DocxObservation,
    DocxRenderReport,
    EvaluationReference,
    EvaluationState,
    EvaluationWriteError,
    ObservedElement,
    RenderWarning,
    RestorationEvaluationInput,
    SnapshotObservation,
    SnapshotRegion,
    StructuralRelationship,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "evaluation"
IR_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "document_ir"
CANONICAL_IR_PATH = IR_FIXTURE_ROOT / "canonical.document.ir.json"
CANONICAL_ASSET_B64 = IR_FIXTURE_ROOT / "canonical-logo.png.b64"
FIXTURE_DIGEST = "a" * 64


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_reference() -> EvaluationReference:
    return EvaluationReference.from_dict(
        load_json(FIXTURE_ROOT / "reviewed-reference.json")
    )


def fixture_input() -> RestorationEvaluationInput:
    reference = load_reference()
    observation = DocxObservation.from_dict(
        load_json(FIXTURE_ROOT / "near-70-observation.json")
    )
    snapshot = SnapshotObservation.from_dict(
        load_json(FIXTURE_ROOT / "near-70-snapshot.json")
    )
    return RestorationEvaluationInput(
        ir_version=reference.ir_version,
        ir_schema_valid=True,
        reference=reference,
        observation=observation,
        render_report=render_report(reference),
        snapshot=snapshot,
    )


def render_report(
    reference: EvaluationReference,
    *,
    digest: str = FIXTURE_DIGEST,
    warnings: tuple[RenderWarning, ...] = (),
    rendered_ids: tuple[str, ...] | None = None,
) -> DocxRenderReport:
    return DocxRenderReport(
        renderer_name="fixture-renderer",
        renderer_version="1.0",
        ir_version=reference.ir_version,
        output_path="fixture.docx",
        output_sha256=digest,
        rendered_element_ids=(
            tuple(element.id for element in reference.elements)
            if rendered_ids is None
            else rendered_ids
        ),
        fallback_element_ids=(),
        omitted_element_ids=(),
        warnings=warnings,
        errors=(),
        font_substitutions=(),
    )


def perfect_input(
    *,
    reviewed: bool = True,
    snapshot_available: bool = True,
) -> RestorationEvaluationInput:
    reference = replace(load_reference(), reviewed=reviewed)
    observed: list[ObservedElement] = []
    observed_ids: dict[str, str] = {}
    for element in reference.elements:
        observed_id = f"observed-{element.id}"
        observed_ids[element.id] = observed_id
        observed.append(
            ObservedElement(
                id=observed_id,
                element_type=element.element_type,
                page_number=element.page_number,
                text=element.text,
                bbox=element.bbox,
                reading_order=element.reading_order,
                source_element_id=element.id,
                content_sha256=element.content_sha256,
            )
        )
    relationships = tuple(
        StructuralRelationship(
            kind=relationship.kind,
            source=observed_ids.get(relationship.source, relationship.source),
            target=observed_ids.get(relationship.target, relationship.target),
        )
        for relationship in reference.relationships
    )
    observation = DocxObservation(
        observer_name="perfect-observer",
        observer_version="1.0",
        source_sha256=FIXTURE_DIGEST,
        package_readable=True,
        python_docx_reopenable=True,
        elements=tuple(observed),
        relationships=relationships,
    )
    snapshot = (
        SnapshotObservation(
            renderer_name="fixture-libreoffice",
            renderer_version="1.0",
            available=True,
            opened_without_repair=True,
            regions=tuple(
                SnapshotRegion(
                    id=f"region-{element.id}",
                    element_type=element.element_type,
                    bbox=element.bbox,
                    source_element_id=element.id,
                    observed_element_id=observed_ids[element.id],
                )
                for element in reference.elements
                if element.bbox is not None
            ),
        )
        if snapshot_available
        else None
    )
    return RestorationEvaluationInput(
        ir_version=reference.ir_version,
        ir_schema_valid=True,
        reference=reference,
        observation=observation,
        render_report=render_report(reference),
        snapshot=snapshot,
    )


class RestorationEvaluationTest(unittest.TestCase):
    def test_reviewed_readable_fixture_near_70_passes_default_threshold(self):
        result = evaluate_restoration_input(fixture_input())

        self.assertEqual(result.state, EvaluationState.PASS)
        self.assertGreaterEqual(result.overall_score, 70)
        self.assertLess(result.overall_score, 75)
        self.assertEqual(
            [component.name for component in result.components],
            [
                "text_similarity",
                "element_coverage",
                "structure_similarity",
                "geometry_similarity",
            ],
        )
        self.assertTrue(all(gate.passed is True for gate in result.hard_gates))

    def test_threshold_is_inclusive_and_configurable_at_boundary(self):
        evaluation_input = fixture_input()
        baseline = evaluate_restoration_input(evaluation_input)
        at_boundary = evaluate_restoration_input(
            evaluation_input,
            config=EvaluationConfig(threshold=baseline.overall_score),
        )
        above_boundary = evaluate_restoration_input(
            evaluation_input,
            config=EvaluationConfig(threshold=baseline.overall_score + 0.01),
        )

        self.assertEqual(at_boundary.state, EvaluationState.PASS)
        self.assertEqual(above_boundary.state, EvaluationState.FAIL)
        self.assertTrue(
            any(
                reason.startswith("score_below_threshold")
                for reason in above_boundary.reasons
            )
        )

    def test_missing_essential_text_fails_even_with_score_above_70(self):
        evaluation_input = perfect_input()
        changed = tuple(
            replace(element, text="番号")
            if element.source_element_id == "name-label"
            else element
            for element in evaluation_input.observation.elements
        )
        broken = replace(
            evaluation_input,
            observation=replace(evaluation_input.observation, elements=changed),
        )

        result = evaluate_restoration_input(broken)

        self.assertGreaterEqual(result.overall_score, 70)
        self.assertEqual(result.state, EvaluationState.FAIL)
        gates = {gate.name: gate for gate in result.hard_gates}
        self.assertFalse(gates["essential_text_readable"].passed)
        self.assertIn("氏名", gates["essential_text_readable"].reason)

    def test_unreviewed_or_incomplete_machine_evidence_never_auto_passes(self):
        unreviewed = evaluate_restoration_input(perfect_input(reviewed=False))
        missing_snapshot = evaluate_restoration_input(
            perfect_input(snapshot_available=False)
        )

        self.assertEqual(unreviewed.overall_score, 100)
        self.assertEqual(unreviewed.state, EvaluationState.REQUIRES_HUMAN_REVIEW)
        self.assertIn("reference_not_reviewed", unreviewed.reasons)
        self.assertEqual(
            missing_snapshot.state,
            EvaluationState.REQUIRES_HUMAN_REVIEW,
        )
        self.assertIn(
            "hard_gate_unknown:libreoffice_open_without_repair",
            missing_snapshot.reasons,
        )

    def test_declared_human_check_remains_visible_until_completed(self):
        evaluation_input = perfect_input()
        reference = replace(
            evaluation_input.reference,
            required_human_checks=("manual_word_smoke",),
        )
        pending_input = replace(evaluation_input, reference=reference)
        completed_input = replace(
            pending_input,
            completed_human_checks=("manual_word_smoke",),
        )

        pending = evaluate_restoration_input(pending_input)
        completed = evaluate_restoration_input(completed_input)

        self.assertEqual(pending.state, EvaluationState.REQUIRES_HUMAN_REVIEW)
        self.assertEqual(pending.required_human_checks, ("manual_word_smoke",))
        self.assertIn(
            "human_check_required:manual_word_smoke",
            pending.reasons,
        )
        self.assertEqual(completed.state, EvaluationState.PASS)
        self.assertEqual(completed.required_human_checks, ())

    def test_source_page_background_is_a_hard_failure(self):
        evaluation_input = perfect_input()
        warning = RenderWarning(
            code="source_page_background_rejected",
            message="whole source page background was rejected",
            page_id="page-001",
            element_id="clinic-logo",
        )
        broken = replace(
            evaluation_input,
            render_report=render_report(
                evaluation_input.reference,
                warnings=(warning,),
            ),
        )

        result = evaluate_restoration_input(broken)

        self.assertEqual(result.overall_score, 100)
        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn(
            "hard_gate_failed:source_page_background_prohibited",
            result.reasons,
        )

    def test_missing_required_asset_is_a_hard_failure(self):
        evaluation_input = perfect_input()
        essential_elements = tuple(
            replace(element, essential=True) if element.id == "clinic-logo" else element
            for element in evaluation_input.reference.elements
        )
        reference = replace(
            evaluation_input.reference,
            elements=essential_elements,
        )
        warning = RenderWarning(
            code="asset_missing",
            message="required logo asset is missing",
            page_id="page-001",
            element_id="clinic-logo",
        )
        broken = replace(
            evaluation_input,
            reference=reference,
            render_report=render_report(reference, warnings=(warning,)),
        )

        result = evaluate_restoration_input(broken)

        self.assertEqual(result.overall_score, 100)
        self.assertEqual(result.state, EvaluationState.FAIL)
        self.assertIn(
            "hard_gate_failed:fatal_render_issues_absent",
            result.reasons,
        )

    def test_result_is_deterministic_and_writer_is_create_only(self):
        first = evaluate_restoration_input(fixture_input())
        second = evaluate_restoration_input(fixture_input())
        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_path = root / "evaluation.json"
            writer = FilesystemEvaluationWriter()
            published = writer.write(first, output_path)
            decoded = json.loads(output_path.read_text(encoding="utf-8"))
            with self.assertRaises(EvaluationWriteError) as raised:
                writer.write(second, output_path)

            self.assertEqual(published, output_path.resolve())
            self.assertEqual(decoded["state"], "pass")
            self.assertIn("text_similarity", decoded["components"])
            self.assertEqual(raised.exception.code, "output_exists")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_python_docx_observer_reads_actual_generated_docx(self):
        document = DocumentIR.from_json(CANONICAL_IR_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            asset_path = root / document.assets[0].path
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(
                base64.b64decode(CANONICAL_ASSET_B64.read_text(encoding="ascii"))
            )
            output_path = root / "reconstructed.docx"
            render_result = render_docx(
                document,
                output_path,
                renderer=PythonDocxRenderer(asset_resolver=BundleAssetResolver(root)),
            )
            observer = PythonDocxObserver()
            observation = observer.observe(output_path)
            reference = build_evaluation_reference(
                document,
                reference_id="canonical-docx-readback",
                reviewed=True,
                essential_element_ids=("p001-text-0000",),
                essential_text_anchors=("問診票",),
            )
            snapshot = SnapshotObservation(
                renderer_name="test-snapshot",
                renderer_version="1.0",
                available=True,
                opened_without_repair=True,
                regions=tuple(
                    SnapshotRegion(
                        id=f"region-{element.id}",
                        element_type=element.element_type,
                        bbox=element.bbox,
                        source_element_id=element.id,
                    )
                    for element in reference.elements
                    if element.bbox is not None
                ),
            )
            evaluation = evaluate_restoration(
                document,
                reference,
                output_path,
                render_result.report,
                observer=observer,
                snapshot=snapshot,
            )

        observed_types = [element.element_type for element in observation.elements]
        observed_text = [
            element.text
            for element in observation.elements
            if element.element_type is ElementType.TEXT
        ]
        self.assertTrue(observation.package_readable)
        self.assertTrue(observation.python_docx_reopenable)
        self.assertEqual(observation.external_relationships, ())
        self.assertEqual(observation.errors, ())
        self.assertEqual(observed_text, ["問診票"])
        self.assertIn(ElementType.LINE, observed_types)
        self.assertIn(ElementType.RECTANGLE, observed_types)
        self.assertIn(ElementType.IMAGE, observed_types)
        self.assertEqual(evaluation.state, EvaluationState.PASS)

    def test_corrupt_docx_becomes_failed_observation_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "corrupt.docx"
            path.write_bytes(b"not a docx")
            observation = PythonDocxObserver().observe(path)

        self.assertFalse(observation.package_readable)
        self.assertFalse(observation.python_docx_reopenable)
        self.assertTrue(observation.errors)


if __name__ == "__main__":
    unittest.main()
