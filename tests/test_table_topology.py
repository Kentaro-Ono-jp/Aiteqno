import base64
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from aiteqno.adapters import (
    BundleAssetResolver,
    FakeOcrBackend,
    FakeOcrObservation,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    PillowPreviewRenderer,
)
from aiteqno.application import extract_png, infer_table_topology, render_preview
from aiteqno.domain import (
    TABLE_TOPOLOGY_EXTENSION_KEY,
    BoundingBox,
    DocumentIR,
    DocumentIRValidationError,
    Page,
    PageTableTopology,
    PixelBoundingBox,
    TablePrimitiveRole,
    TextElement,
    read_page_table_topology,
)


STRUCTURE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "structure" / "structured-page.png.b64"
)
CANONICAL_IR = (
    Path(__file__).parent / "fixtures" / "document_ir" / "canonical.document.ir.json"
)
DENSE_SOURCE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "baseline"
    / "synthetic-dense-japanese-form-v1"
    / "source.png.b64"
)


class TableTopologyInferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png_data = base64.b64decode(STRUCTURE_FIXTURE.read_text(encoding="ascii"))
        cls.observations = (
            FakeOcrObservation(
                text="PATIENT",
                bbox=PixelBoundingBox(x=42, y=47, width=72, height=13),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="FORM",
                bbox=PixelBoundingBox(x=120, y=47, width=48, height=13),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="NAME",
                bbox=PixelBoundingBox(x=42, y=129, width=37, height=10),
                confidence=0.96,
            ),
            FakeOcrObservation(
                text="JOHN",
                bbox=PixelBoundingBox(x=87, y=129, width=37, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="DOE",
                bbox=PixelBoundingBox(x=130, y=129, width=28, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="DATE",
                bbox=PixelBoundingBox(x=42, y=169, width=35, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="2026-08-16",
                bbox=PixelBoundingBox(x=85, y=169, width=71, height=10),
                confidence=0.90,
            ),
        )

    def test_closed_grid_is_inferred_without_mutating_raw_ir(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
            raw_data = raw.to_dict()
            raw_elements_digest = _digest(raw_data["pages"][0]["elements"])

            enriched = infer_table_topology(raw)
            topology = read_page_table_topology(enriched.pages[0])

        self.assertIsNotNone(topology)
        assert topology is not None
        self.assertEqual(len(topology.tables), 1)
        table = topology.tables[0]
        self.assertEqual((table.logical_rows, table.logical_columns), (2, 2))
        self.assertEqual(len(table.cells), 4)
        self.assertEqual(
            sum(len(cell.text_element_ids) for cell in table.cells),
            7,
        )
        self.assertEqual(
            _digest(enriched.to_dict()["pages"][0]["elements"]),
            raw_elements_digest,
        )
        self.assertEqual(enriched.pages[0].elements, raw.pages[0].elements)
        self.assertIs(enriched.pages[0].elements, raw.pages[0].elements)
        self.assertEqual(enriched.pages[0].source, raw.pages[0].source)
        self.assertEqual(enriched.pages[0].size, raw.pages[0].size)
        self.assertEqual(enriched.document_id, raw.document_id)
        self.assertEqual(enriched.assets, raw.assets)
        self.assertIs(infer_table_topology(enriched), enriched)

    def test_fixed_dense_source_builds_five_tables_and_45_cells_without_ocr(self):
        source_data = base64.b64decode(
            DENSE_SOURCE_FIXTURE.read_bytes().strip(),
            validate=True,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = extract_png(
                source_data,
                Path(temporary_directory) / "raw",
                decoder=PillowPngDecoder(),
                structure_extractor=OpenCvStructureExtractor(),
                ocr_backend=FakeOcrBackend(()),
                asset_encoder=PillowPngAssetEncoder(),
                validator=JsonSchemaDocumentIRValidator(),
                bundle_writer=FilesystemDocumentBundleWriter(),
            )
        topology = read_page_table_topology(
            infer_table_topology(result.document).pages[0]
        )

        self.assertIsNotNone(topology)
        assert topology is not None
        self.assertEqual(
            tuple(
                (table.logical_rows, table.logical_columns, len(table.cells))
                for table in topology.tables
            ),
            ((4, 4, 14), (7, 2, 14), (4, 2, 8), (2, 3, 6), (3, 1, 3)),
        )
        self.assertEqual(sum(len(table.cells) for table in topology.tables), 45)
        self.assertEqual(
            sum(
                assignment.role is TablePrimitiveRole.CELL_RECTANGLE
                for assignment in topology.primitive_roles
            ),
            45,
        )
        self.assertEqual(
            sum(
                assignment.role is TablePrimitiveRole.PAGE_FRAME
                for assignment in topology.primitive_roles
            ),
            5,
        )
        self.assertEqual(topology.diagnostics.ambiguous_primitive_element_ids, ())
        self.assertEqual(topology.diagnostics.unassigned_primitive_element_ids, ())
        self.assertEqual(topology.diagnostics.rejected_table_outer_element_ids, ())

    def test_primitive_shuffle_does_not_change_topology_json_or_ids(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        page = raw.pages[0]
        texts = tuple(
            element for element in page.elements if isinstance(element, TextElement)
        )
        primitives = tuple(
            element for element in page.elements if not isinstance(element, TextElement)
        )
        shuffled_page = Page(
            id=page.id,
            number=page.number,
            size=page.size,
            source=page.source,
            elements=(*texts, *reversed(primitives)),
            extensions=page.extensions,
        )
        shuffled = DocumentIR(
            ir_version=raw.ir_version,
            document_id=raw.document_id,
            generator=raw.generator,
            pages=(shuffled_page,),
            assets=raw.assets,
            metadata=raw.metadata,
            extensions=raw.extensions,
        )

        first = read_page_table_topology(infer_table_topology(raw).pages[0])
        second = read_page_table_topology(infer_table_topology(shuffled).pages[0])

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(_topology_bytes(first), _topology_bytes(second))
        self.assertEqual(
            tuple(table.id for table in first.tables),
            tuple(table.id for table in second.tables),
        )
        self.assertEqual(
            tuple(cell.id for table in first.tables for cell in table.cells),
            tuple(cell.id for table in second.tables for cell in table.cells),
        )

    def test_extension_rejects_unknown_duplicate_span_and_geometry_drift(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        valid = infer_table_topology(raw).to_dict()
        mutations = {
            "unknown_field": lambda extension: extension["tables"][0].__setitem__(
                "unreviewed_guess", True
            ),
            "duplicate_id": lambda extension: extension["tables"][0]["cells"][
                1
            ].__setitem__("id", extension["tables"][0]["cells"][0]["id"]),
            "invalid_cell_span": lambda extension: extension["tables"][0]["cells"][
                0
            ].__setitem__("colspan", 99),
            "out_of_table_geometry": lambda extension: extension["tables"][0]["cells"][
                0
            ]["bbox"].__setitem__("x", 1000.0),
            "unknown_element": lambda extension: extension["tables"][0]["cells"][
                0
            ].__setitem__("text_element_ids", ["missing-text-element"]),
        }
        for expected_code, mutate in mutations.items():
            with self.subTest(expected_code=expected_code):
                data = copy.deepcopy(valid)
                extension = data["pages"][0]["extensions"][TABLE_TOPOLOGY_EXTENSION_KEY]
                mutate(extension)
                with self.assertRaises(DocumentIRValidationError) as raised:
                    DocumentIR.from_dict(data)
                self.assertIn(
                    expected_code,
                    {issue.code for issue in raised.exception.issues},
                )

    def test_preview_bytes_and_element_report_ignore_semantic_extension(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw, bundle_root = self._extract(root / "bundle")
            enriched = infer_table_topology(raw)
            renderer = PillowPreviewRenderer(
                asset_resolver=BundleAssetResolver(bundle_root),
                font_paths={},
                fallback_families=(),
            )
            raw_path = root / "raw.png"
            enriched_path = root / "enriched.png"
            raw_result = render_preview(raw, raw_path, renderer=renderer)
            enriched_result = render_preview(
                enriched,
                enriched_path,
                renderer=renderer,
            )

            self.assertEqual(raw_path.read_bytes(), enriched_path.read_bytes())
            self.assertEqual(
                raw_result.report.rendered_element_ids,
                enriched_result.report.rendered_element_ids,
            )
            self.assertEqual(raw_result.report.omitted_element_ids, ())
            self.assertEqual(enriched_result.report.omitted_element_ids, ())
            self.assertEqual(
                raw_result.report.fallback_element_ids,
                enriched_result.report.fallback_element_ids,
            )
            self.assertEqual(
                raw_result.report.warnings,
                enriched_result.report.warnings,
            )

    def test_non_grid_golden_document_remains_exactly_unchanged(self):
        document = DocumentIR.from_json(CANONICAL_IR.read_bytes())

        result = infer_table_topology(document)

        self.assertIs(result, document)
        self.assertNotIn(TABLE_TOPOLOGY_EXTENSION_KEY, result.pages[0].extensions)

    def test_rejected_partial_grid_is_retained_as_machine_readable_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        valid_topology = read_page_table_topology(infer_table_topology(raw).pages[0])
        assert valid_topology is not None
        removed_cell_id = next(
            assignment.element_id
            for assignment in valid_topology.primitive_roles
            if assignment.role is TablePrimitiveRole.CELL_RECTANGLE
        )
        outer_id = next(
            assignment.element_id
            for assignment in valid_topology.primitive_roles
            if assignment.role is TablePrimitiveRole.TABLE_OUTER_BORDER
        )
        page = raw.pages[0]
        incomplete_page = Page(
            id=page.id,
            number=page.number,
            size=page.size,
            source=page.source,
            elements=tuple(
                element for element in page.elements if element.id != removed_cell_id
            ),
            extensions=page.extensions,
        )
        incomplete = DocumentIR(
            ir_version=raw.ir_version,
            document_id=raw.document_id,
            generator=raw.generator,
            pages=(incomplete_page,),
            assets=raw.assets,
            metadata=raw.metadata,
            extensions=raw.extensions,
        )

        topology = read_page_table_topology(infer_table_topology(incomplete).pages[0])

        self.assertIsNotNone(topology)
        assert topology is not None
        self.assertEqual(topology.tables, ())
        self.assertEqual(
            topology.diagnostics.rejected_table_outer_element_ids,
            (outer_id,),
        )
        self.assertTrue(topology.diagnostics.unassigned_primitive_element_ids)
        self.assertEqual(
            len(topology.diagnostics.unassigned_text_element_ids),
            7,
        )

    def test_embedded_closed_control_does_not_invalidate_parent_grid(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        valid_topology = read_page_table_topology(infer_table_topology(raw).pages[0])
        assert valid_topology is not None
        cell_assignment = next(
            assignment
            for assignment in valid_topology.primitive_roles
            if assignment.role is TablePrimitiveRole.CELL_RECTANGLE
        )
        source_cell = next(
            element
            for element in raw.pages[0].elements
            if element.id == cell_assignment.element_id
        )
        embedded_control = replace(
            source_cell,
            id="embedded-closed-control",
            bbox=BoundingBox(
                x=source_cell.bbox.x + 2.0,
                y=source_cell.bbox.y + 2.0,
                width=4.0,
                height=4.0,
            ),
        )
        page = raw.pages[0]
        with_control = DocumentIR(
            ir_version=raw.ir_version,
            document_id=raw.document_id,
            generator=raw.generator,
            pages=(
                Page(
                    id=page.id,
                    number=page.number,
                    size=page.size,
                    source=page.source,
                    elements=(*page.elements, embedded_control),
                    extensions=page.extensions,
                ),
            ),
            assets=raw.assets,
            metadata=raw.metadata,
            extensions=raw.extensions,
        )

        topology = read_page_table_topology(
            infer_table_topology(with_control).pages[0]
        )

        self.assertIsNotNone(topology)
        assert topology is not None
        self.assertEqual(
            tuple(
                (table.logical_rows, table.logical_columns, len(table.cells))
                for table in topology.tables
            ),
            tuple(
                (table.logical_rows, table.logical_columns, len(table.cells))
                for table in valid_topology.tables
            ),
        )
        self.assertIn(
            embedded_control.id,
            topology.diagnostics.unassigned_primitive_element_ids,
        )

    def test_text_center_on_overlapping_cell_borders_is_explicitly_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        valid_topology = read_page_table_topology(infer_table_topology(raw).pages[0])
        assert valid_topology is not None
        first_table = valid_topology.tables[0]
        left = next(
            cell
            for cell in first_table.cells
            if cell.row_index == 0 and cell.column_index == 0
        )
        right = next(
            cell
            for cell in first_table.cells
            if cell.row_index == 0 and cell.column_index == 1
        )
        overlap_left = max(left.bbox.x, right.bbox.x)
        overlap_right = min(left.bbox.right, right.bbox.right)
        self.assertGreater(overlap_right, overlap_left)
        center_x = (overlap_left + overlap_right) / 2
        center_y = max(left.bbox.y, right.bbox.y) + 1.0
        page = raw.pages[0]
        target = next(
            element for element in page.elements if isinstance(element, TextElement)
        )
        moved = replace(
            target,
            bbox=BoundingBox(
                x=center_x - 0.05,
                y=center_y - 0.05,
                width=0.1,
                height=0.1,
            ),
        )
        ambiguous_page = Page(
            id=page.id,
            number=page.number,
            size=page.size,
            source=page.source,
            elements=tuple(
                moved if element.id == target.id else element
                for element in page.elements
            ),
            extensions=page.extensions,
        )
        ambiguous = DocumentIR(
            ir_version=raw.ir_version,
            document_id=raw.document_id,
            generator=raw.generator,
            pages=(ambiguous_page,),
            assets=raw.assets,
            metadata=raw.metadata,
            extensions=raw.extensions,
        )

        topology = read_page_table_topology(infer_table_topology(ambiguous).pages[0])

        assert topology is not None
        self.assertEqual(
            topology.diagnostics.ambiguous_text_element_ids,
            (target.id,),
        )
        self.assertFalse(
            any(
                target.id in cell.text_element_ids
                for table in topology.tables
                for cell in table.cells
            )
        )

    def test_roles_and_diagnostics_cover_every_primitive_and_text(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw, _ = self._extract(Path(temporary_directory) / "raw")
        topology = read_page_table_topology(infer_table_topology(raw).pages[0])
        assert topology is not None
        roles = {assignment.role for assignment in topology.primitive_roles}
        self.assertIn(TablePrimitiveRole.TABLE_OUTER_BORDER, roles)
        self.assertIn(TablePrimitiveRole.CELL_RECTANGLE, roles)
        self.assertIn(TablePrimitiveRole.ROW_BOUNDARY, roles)
        self.assertIn(TablePrimitiveRole.COLUMN_BOUNDARY, roles)
        self.assertIn(TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE, roles)
        self.assertEqual(topology.diagnostics.ambiguous_text_element_ids, ())
        self.assertEqual(topology.diagnostics.ambiguous_primitive_element_ids, ())
        self.assertEqual(topology.diagnostics.unassigned_primitive_element_ids, ())
        self.assertEqual(topology.diagnostics.rejected_table_outer_element_ids, ())

    def _extract(self, output_directory: Path) -> tuple[DocumentIR, Path]:
        result = extract_png(
            self.png_data,
            output_directory,
            decoder=PillowPngDecoder(),
            structure_extractor=OpenCvStructureExtractor(),
            ocr_backend=FakeOcrBackend(self.observations),
            asset_encoder=PillowPngAssetEncoder(),
            validator=JsonSchemaDocumentIRValidator(),
            bundle_writer=FilesystemDocumentBundleWriter(),
            languages=("eng",),
        )
        return result.document, result.bundle.bundle_root


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _topology_bytes(topology: PageTableTopology) -> bytes:
    return json.dumps(
        topology.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
