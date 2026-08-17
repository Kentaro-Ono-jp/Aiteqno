import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

from docx import Document as open_docx
from docx.oxml.ns import qn

from aiteqno.adapters import PythonDocxObserver, PythonDocxRenderer
from aiteqno.application import (
    build_docx_structure_relationships,
    build_evaluation_reference,
    evaluate_restoration,
    render_docx,
)
from aiteqno.domain import (
    TABLE_TOPOLOGY_COORDINATE_SPACE,
    TABLE_TOPOLOGY_EXTENSION_KEY,
    TABLE_TOPOLOGY_PROVIDER,
    TABLE_TOPOLOGY_PROVIDER_VERSION,
    TABLE_TOPOLOGY_SCHEMA_VERSION,
    BoundingBox,
    DocumentIR,
    PageSize,
    RectangleElement,
    TextElement,
    read_page_table_topology,
)
from aiteqno.ports import RenderPolicy


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "document_ir" / "canonical.document.ir.json"
)
TABLE_ID = "page-001-table-0000"
PARAMETERS_DIGEST = "0" * 64


def _provenance(source_ids: list[str]) -> dict[str, object]:
    return {
        "stage": "derived",
        "provider": TABLE_TOPOLOGY_PROVIDER,
        "provider_version": TABLE_TOPOLOGY_PROVIDER_VERSION,
        "source_element_ids": source_ids,
        "parameters_digest": PARAMETERS_DIGEST,
    }


def _topology_document(
    merge: str | None = None,
    *,
    include_outside_text: bool = True,
) -> DocumentIR:
    canonical = DocumentIR.from_json(FIXTURE_PATH.read_bytes())
    base_text = canonical.pages[0].elements[0]
    base_rectangle = canonical.pages[0].elements[2]
    assert isinstance(base_text, TextElement)
    assert isinstance(base_rectangle, RectangleElement)

    if merge == "horizontal":
        cell_specs = (
            (0, 0, 1, 2),
            (1, 0, 1, 1),
            (1, 1, 1, 1),
        )
    elif merge == "vertical":
        cell_specs = (
            (0, 0, 2, 1),
            (0, 1, 1, 1),
            (1, 1, 1, 1),
        )
    else:
        cell_specs = (
            (0, 0, 1, 1),
            (0, 1, 1, 1),
            (1, 0, 1, 1),
            (1, 1, 1, 1),
        )

    text_elements: list[TextElement] = []
    reading_order = 0
    if include_outside_text:
        text_elements.append(
            replace(
                base_text,
                id="p001-text-0000",
                bbox=BoundingBox(x=20, y=10, width=70, height=10),
                text="Before table",
                reading_order=reading_order,
                style=replace(
                    base_text.style,
                    font_family="Arial",
                    font_size_pt=8,
                    font_weight=400,
                ),
            )
        )
        reading_order += 1

    cell_text_ids: list[str] = []
    cell_bboxes: list[BoundingBox] = []
    for cell_number, (row, column, rowspan, colspan) in enumerate(cell_specs):
        bbox = BoundingBox(
            x=20 + column * 80,
            y=40 + row * 40,
            width=80 * colspan,
            height=40 * rowspan,
        )
        cell_bboxes.append(bbox)
        text_id = f"p001-text-{reading_order:04d}"
        cell_text_ids.append(text_id)
        text_elements.append(
            replace(
                base_text,
                id=text_id,
                bbox=BoundingBox(
                    x=bbox.x + 5,
                    y=bbox.y + 14,
                    width=min(55, bbox.width - 10),
                    height=10,
                ),
                text=f"Cell {cell_number + 1}",
                reading_order=reading_order,
                style=replace(
                    base_text.style,
                    font_family="Arial",
                    font_size_pt=8,
                    font_weight=400,
                ),
            )
        )
        reading_order += 1

    outside_ids: list[str] = []
    if include_outside_text:
        outside_id = f"p001-text-{reading_order:04d}"
        outside_ids.extend(("p001-text-0000", outside_id))
        text_elements.append(
            replace(
                base_text,
                id=outside_id,
                bbox=BoundingBox(x=20, y=135, width=65, height=10),
                text="After table",
                reading_order=reading_order,
                style=replace(
                    base_text.style,
                    font_family="Arial",
                    font_size_pt=8,
                    font_weight=400,
                ),
            )
        )

    outer_id = "p001-rectangle-0000"
    outer = replace(
        base_rectangle,
        id=outer_id,
        bbox=BoundingBox(x=20, y=40, width=160, height=80),
    )
    rectangles: list[RectangleElement] = [outer]
    rectangle_ids: list[str] = []
    cells: list[dict[str, object]] = []
    roles: list[dict[str, object]] = [
        {
            "element_id": outer_id,
            "role": "table_outer_border",
            "table_id": TABLE_ID,
        }
    ]
    for cell_number, ((row, column, rowspan, colspan), bbox, text_id) in enumerate(
        zip(cell_specs, cell_bboxes, cell_text_ids, strict=True),
        start=1,
    ):
        rectangle_id = f"p001-rectangle-{cell_number:04d}"
        rectangle_ids.append(rectangle_id)
        rectangles.append(replace(base_rectangle, id=rectangle_id, bbox=bbox))
        cell_id = f"{TABLE_ID}-cell-r{row:03d}-c{column:03d}"
        cells.append(
            {
                "id": cell_id,
                "row_index": row,
                "column_index": column,
                "rowspan": rowspan,
                "colspan": colspan,
                "bbox": {
                    "x": bbox.x,
                    "y": bbox.y,
                    "width": bbox.width,
                    "height": bbox.height,
                },
                "supporting_element_ids": [rectangle_id],
                "text_element_ids": [text_id],
                "confidence": 1.0,
                "provenance": _provenance([rectangle_id]),
            }
        )
        roles.append(
            {
                "element_id": rectangle_id,
                "role": "cell_rectangle",
                "table_id": TABLE_ID,
                "cell_id": cell_id,
            }
        )

    support_ids = [outer_id, *rectangle_ids]
    extension = {
        "schema_version": TABLE_TOPOLOGY_SCHEMA_VERSION,
        "provider": TABLE_TOPOLOGY_PROVIDER,
        "provider_version": TABLE_TOPOLOGY_PROVIDER_VERSION,
        "coordinate_space": TABLE_TOPOLOGY_COORDINATE_SPACE,
        "boundary_tolerance_pt": 1.0,
        "tables": [
            {
                "id": TABLE_ID,
                "bbox": {"x": 20, "y": 40, "width": 160, "height": 80},
                "logical_grid": {"rows": 2, "columns": 2},
                "rows": [
                    {
                        "id": f"{TABLE_ID}-row-000",
                        "index": 0,
                        "start": 40,
                        "end": 80,
                    },
                    {
                        "id": f"{TABLE_ID}-row-001",
                        "index": 1,
                        "start": 80,
                        "end": 120,
                    },
                ],
                "columns": [
                    {
                        "id": f"{TABLE_ID}-column-000",
                        "index": 0,
                        "start": 20,
                        "end": 100,
                    },
                    {
                        "id": f"{TABLE_ID}-column-001",
                        "index": 1,
                        "start": 100,
                        "end": 180,
                    },
                ],
                "cells": cells,
                "supporting_element_ids": support_ids,
                "confidence": 1.0,
                "provenance": _provenance(support_ids),
            }
        ],
        "primitive_roles": roles,
        "diagnostics": {
            "ambiguous_text_element_ids": [],
            "unassigned_text_element_ids": outside_ids,
            "ambiguous_primitive_element_ids": [],
            "unassigned_primitive_element_ids": [],
            "rejected_table_outer_element_ids": [],
        },
    }
    page = replace(
        canonical.pages[0],
        size=PageSize(width=200, height=160),
        source=None,
        elements=(*text_elements, *rectangles),
        extensions={TABLE_TOPOLOGY_EXTENSION_KEY: extension},
    )
    return replace(canonical, pages=(page,), assets=())


class NativeWordTableRendererTest(unittest.TestCase):
    def test_native_table_preserves_grid_geometry_text_and_report_accounting(self):
        document = _topology_document()
        topology = read_page_table_topology(document.pages[0])
        assert topology is not None
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "native.docx"
            result = render_docx(
                document,
                output,
                renderer=PythonDocxRenderer(),
                policy=RenderPolicy.STRICT,
            )
            reopened = open_docx(output)
            observation = PythonDocxObserver().observe(output)

        self.assertEqual(len(reopened.tables), 1)
        table = reopened.tables[0]
        caption = table._tbl.tblPr.find(qn("w:tblCaption"))
        self.assertIsNotNone(caption)
        assert caption is not None
        self.assertEqual(caption.get(qn("w:val")), f"aiteqno-table:{TABLE_ID}")
        self.assertEqual(
            [int(column.get(qn("w:w"))) for column in table._tbl.tblGrid],
            [1600, 1600],
        )
        self.assertEqual(
            [round(row.height.pt) for row in table.rows],
            [34, 34],
        )
        self.assertEqual(result.report.native_table_ids, (TABLE_ID,))
        self.assertEqual(
            len(result.report.native_table_consumed_element_ids),
            9,
        )
        self.assertEqual(
            len(result.report.native_table_consumed_element_ids),
            len(set(result.report.native_table_consumed_element_ids)),
        )
        self.assertEqual(result.report.fallback_element_ids, ())
        self.assertEqual(result.report.omitted_element_ids, ())
        self.assertEqual(observation.errors, ())
        self.assertEqual(
            [
                element.source_element_id
                for element in observation.elements
                if element.source_element_id is not None
            ],
            [
                element.id
                for element in document.pages[0].elements
                if isinstance(element, TextElement)
            ],
        )
        self.assertEqual(
            sum(
                relationship.source == TABLE_ID
                and relationship.kind.value == "containment"
                for relationship in observation.relationships
            ),
            4,
        )

    def test_horizontal_and_vertical_merges_are_native_and_observable(self):
        for merge, expected_xpath, expected_values in (
            ("horizontal", ".//w:gridSpan", ["2"]),
            ("vertical", ".//w:vMerge", ["restart", None]),
        ):
            with self.subTest(merge=merge):
                document = _topology_document(merge)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    output = Path(temporary_directory) / f"{merge}.docx"
                    render_docx(
                        document,
                        output,
                        renderer=PythonDocxRenderer(),
                        policy=RenderPolicy.STRICT,
                    )
                    reopened = open_docx(output)
                    observation = PythonDocxObserver().observe(output)

                values = [
                    node.get(qn("w:val"))
                    for node in reopened.tables[0]._tbl.xpath(expected_xpath)
                ]
                self.assertEqual(values, expected_values)
                table_cells = {
                    relationship.target
                    for relationship in observation.relationships
                    if relationship.kind.value == "containment"
                    and relationship.source == TABLE_ID
                }
                self.assertEqual(len(table_cells), 3)
                self.assertEqual(observation.errors, ())

    def test_structure_relationships_raise_restoration_above_threshold(self):
        document = _topology_document()
        relationships = build_docx_structure_relationships(document)
        reference = build_evaluation_reference(
            document,
            reference_id="native-table-unit",
            reviewed=True,
            relationships=relationships,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "evaluated.docx"
            render_result = render_docx(
                document,
                output,
                renderer=PythonDocxRenderer(),
                policy=RenderPolicy.STRICT,
            )
            evaluation = evaluate_restoration(
                document,
                reference,
                output,
                render_result.report,
                observer=PythonDocxObserver(),
            )

        components = {item.name: item.score for item in evaluation.components}
        self.assertEqual(components["text_similarity"], 100.0)
        self.assertGreater(components["structure_similarity"], 0.0)
        self.assertGreaterEqual(evaluation.overall_score, 70.0)

    def test_normalized_document_xml_is_deterministic(self):
        document = _topology_document("horizontal")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = (root / "first.docx", root / "second.docx")
            for path in paths:
                render_docx(
                    document,
                    path,
                    renderer=PythonDocxRenderer(),
                    policy=RenderPolicy.STRICT,
                )
            with ZipFile(paths[0]) as first, ZipFile(paths[1]) as second:
                first_xml = first.read("word/document.xml")
                second_xml = second.read("word/document.xml")

        self.assertEqual(first_xml, second_xml)


if __name__ == "__main__":
    unittest.main()
