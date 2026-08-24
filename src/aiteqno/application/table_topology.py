"""Deterministically derive table semantics from immutable IR primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from aiteqno.domain import (
    TABLE_TOPOLOGY_COORDINATE_SPACE,
    TABLE_TOPOLOGY_EXTENSION_KEY,
    TABLE_TOPOLOGY_PROVIDER,
    TABLE_TOPOLOGY_PROVIDER_VERSION,
    TABLE_TOPOLOGY_SCHEMA_VERSION,
    BoundingBox,
    DocumentIR,
    LineElement,
    Page,
    PageTableTopology,
    PrimitiveRoleAssignment,
    RectangleElement,
    TableCellTopology,
    TablePrimitiveRole,
    TableTopology,
    TableTopologyDiagnostics,
    TextElement,
    TopologyAxis,
    TopologyProvenance,
    read_page_table_topology,
)


TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT = 1.0
TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION = 0.9
TABLE_TOPOLOGY_MINIMUM_CELL_COUNT = 2
TABLE_TOPOLOGY_MINIMUM_TILING_AREA_FRACTION = 0.8
TABLE_TOPOLOGY_MAXIMUM_TILING_AREA_FRACTION = 1.2

_PARAMETERS = {
    "algorithm": "closed-rectangle-direct-child-grid-v2",
    "boundary_tolerance_pt": TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT,
    "cell_assignment": "text-bbox-center-in-one-physical-cell",
    "cell_selection": "maximal-direct-child-rectangles",
    "maximum_tiling_area_fraction": TABLE_TOPOLOGY_MAXIMUM_TILING_AREA_FRACTION,
    "minimum_cell_count": TABLE_TOPOLOGY_MINIMUM_CELL_COUNT,
    "minimum_tiling_area_fraction": TABLE_TOPOLOGY_MINIMUM_TILING_AREA_FRACTION,
    "page_frame_area_fraction": TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION,
}
TABLE_TOPOLOGY_PARAMETERS_DIGEST = hashlib.sha256(
    json.dumps(_PARAMETERS, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@dataclass(slots=True)
class _DetectedCell:
    rectangle: RectangleElement
    row_index: int
    column_index: int
    rowspan: int
    colspan: int
    id: str = ""
    supporting_elements: tuple[LineElement | RectangleElement, ...] = ()
    texts: list[TextElement] = field(default_factory=list)


@dataclass(slots=True)
class _DetectedTable:
    outer: RectangleElement
    row_boundaries: tuple[float, ...]
    column_boundaries: tuple[float, ...]
    cells: list[_DetectedCell]
    id: str = ""
    lines: tuple[LineElement, ...] = ()
    supporting_elements: tuple[LineElement | RectangleElement, ...] = ()


def infer_table_topology(document: DocumentIR) -> DocumentIR:
    """Return a new IR with validated per-page table-topology extensions.

    Existing page elements, source metadata, assets, and document metadata are
    reused verbatim. Pages without a supported closed grid remain byte-for-byte
    equivalent and do not receive an empty extension.
    """

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")

    changed = False
    pages: list[Page] = []
    for page in document.pages:
        if TABLE_TOPOLOGY_EXTENSION_KEY in page.extensions:
            read_page_table_topology(page)
            pages.append(page)
            continue
        topology = _infer_page_topology(page)
        if topology is None:
            pages.append(page)
            continue
        extensions = dict(page.extensions)
        extensions[TABLE_TOPOLOGY_EXTENSION_KEY] = topology.to_dict()
        pages.append(
            Page(
                id=page.id,
                number=page.number,
                size=page.size,
                source=page.source,
                elements=page.elements,
                extensions=extensions,
            )
        )
        changed = True

    if not changed:
        return document
    return DocumentIR(
        ir_version=document.ir_version,
        document_id=document.document_id,
        generator=document.generator,
        pages=tuple(pages),
        assets=document.assets,
        metadata=document.metadata,
        extensions=document.extensions,
    )


def _infer_page_topology(page: Page) -> PageTableTopology | None:
    rectangles = tuple(
        sorted(
            (
                element
                for element in page.elements
                if isinstance(element, RectangleElement)
            ),
            key=_element_key,
        )
    )
    lines = tuple(
        sorted(
            (element for element in page.elements if isinstance(element, LineElement)),
            key=_element_key,
        )
    )
    texts = tuple(
        sorted(
            (element for element in page.elements if isinstance(element, TextElement)),
            key=lambda element: element.reading_order,
        )
    )
    if not rectangles:
        return None

    page_area = page.size.width * page.size.height
    frame_rectangles = {
        rectangle.id
        for rectangle in rectangles
        if _area(rectangle.bbox) / page_area >= TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION
    }
    usable_rectangles = tuple(
        rectangle for rectangle in rectangles if rectangle.id not in frame_rectangles
    )
    outer_candidates = tuple(
        rectangle
        for rectangle in usable_rectangles
        if sum(
            _strictly_contains(rectangle.bbox, other.bbox)
            for other in usable_rectangles
            if other is not rectangle
        )
        >= TABLE_TOPOLOGY_MINIMUM_CELL_COUNT
    )
    maximal_outers = tuple(
        candidate
        for candidate in outer_candidates
        if not any(
            other is not candidate and _strictly_contains(other.bbox, candidate.bbox)
            for other in outer_candidates
        )
    )

    grouped_cells: dict[str, list[RectangleElement]] = {
        outer.id: [] for outer in maximal_outers
    }
    ambiguous_primitives: set[str] = set()
    outer_ids = {outer.id for outer in maximal_outers}
    for rectangle in usable_rectangles:
        if rectangle.id in outer_ids:
            continue
        containers = tuple(
            outer
            for outer in maximal_outers
            if _strictly_contains(outer.bbox, rectangle.bbox)
        )
        if len(containers) == 1:
            grouped_cells[containers[0].id].append(rectangle)
        elif len(containers) > 1:
            ambiguous_primitives.add(rectangle.id)

    detected: list[_DetectedTable] = []
    rejected_outer_ids: list[str] = []
    for outer in sorted(maximal_outers, key=_element_key):
        table = _build_grid(outer, tuple(grouped_cells[outer.id]))
        if table is None:
            rejected_outer_ids.append(outer.id)
        else:
            detected.append(table)
    if not detected and not maximal_outers:
        return None

    detected.sort(key=lambda item: _element_key(item.outer))
    for table_index, table in enumerate(detected):
        table.id = f"{page.id}-table-{table_index:04d}"
        table.cells.sort(
            key=lambda item: (
                item.row_index,
                item.column_index,
                _element_key(item.rectangle),
            )
        )
        for cell in table.cells:
            cell.id = f"{table.id}-cell-r{cell.row_index:03d}-c{cell.column_index:03d}"

    ambiguous_text_ids: list[str] = []
    unassigned_text_ids: list[str] = []
    all_cells = tuple(cell for table in detected for cell in table.cells)
    for text in texts:
        center_x = text.bbox.x + text.bbox.width / 2
        center_y = text.bbox.y + text.bbox.height / 2
        matches = tuple(
            cell
            for cell in all_cells
            if _contains_point(cell.rectangle.bbox, center_x, center_y)
        )
        if len(matches) == 1:
            matches[0].texts.append(text)
        elif matches:
            ambiguous_text_ids.append(text.id)
        else:
            unassigned_text_ids.append(text.id)

    roles: dict[str, PrimitiveRoleAssignment] = {}
    for rectangle in rectangles:
        if rectangle.id in frame_rectangles:
            roles[rectangle.id] = PrimitiveRoleAssignment(
                element_id=rectangle.id,
                role=TablePrimitiveRole.PAGE_FRAME,
            )
    outer_to_table = {table.outer.id: table for table in detected}
    cell_to_table = {
        cell.rectangle.id: (table, cell) for table in detected for cell in table.cells
    }
    for rectangle in usable_rectangles:
        if rectangle.id in outer_to_table:
            table = outer_to_table[rectangle.id]
            roles[rectangle.id] = PrimitiveRoleAssignment(
                element_id=rectangle.id,
                role=TablePrimitiveRole.TABLE_OUTER_BORDER,
                table_id=table.id,
            )
        elif rectangle.id in cell_to_table:
            table, cell = cell_to_table[rectangle.id]
            roles[rectangle.id] = PrimitiveRoleAssignment(
                element_id=rectangle.id,
                role=TablePrimitiveRole.CELL_RECTANGLE,
                table_id=table.id,
                cell_id=cell.id,
            )
        else:
            roles[rectangle.id] = PrimitiveRoleAssignment(
                element_id=rectangle.id,
                role=TablePrimitiveRole.UNASSIGNED,
            )

    table_lines: dict[str, list[LineElement]] = {table.id: [] for table in detected}
    for line in lines:
        if _is_page_frame_line(line, page):
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=TablePrimitiveRole.PAGE_FRAME,
            )
            continue
        matching_tables = tuple(
            table
            for table in detected
            if _line_intersects_table(line, table.outer.bbox)
        )
        if len(matching_tables) > 1:
            ambiguous_primitives.add(line.id)
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=TablePrimitiveRole.UNASSIGNED,
            )
            continue
        if not matching_tables:
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=TablePrimitiveRole.PAGE_DECORATION,
            )
            continue
        table = matching_tables[0]
        role = _table_line_role(line, table)
        if role is None:
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=TablePrimitiveRole.UNASSIGNED,
            )
            continue
        table_lines[table.id].append(line)
        if role is TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE:
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=role,
                table_id=table.id,
                duplicate_of_element_id=table.outer.id,
            )
        else:
            roles[line.id] = PrimitiveRoleAssignment(
                element_id=line.id,
                role=role,
                table_id=table.id,
            )

    public_tables: list[TableTopology] = []
    for table in detected:
        table.lines = tuple(sorted(table_lines[table.id], key=_element_key))
        table.supporting_elements = tuple(
            sorted(
                (
                    table.outer,
                    *(cell.rectangle for cell in table.cells),
                    *table.lines,
                ),
                key=_element_key,
            )
        )
        public_cells: list[TableCellTopology] = []
        for cell in table.cells:
            edge_lines = tuple(
                line
                for line in table.lines
                if _line_supports_cell_edge(line, cell.rectangle.bbox)
            )
            cell.supporting_elements = tuple(
                sorted((cell.rectangle, *edge_lines), key=_element_key)
            )
            support_ids = tuple(element.id for element in cell.supporting_elements)
            public_cells.append(
                TableCellTopology(
                    id=cell.id,
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    rowspan=cell.rowspan,
                    colspan=cell.colspan,
                    bbox=cell.rectangle.bbox,
                    supporting_element_ids=support_ids,
                    text_element_ids=tuple(text.id for text in cell.texts),
                    confidence=_derived_confidence(cell.supporting_elements),
                    provenance=_provenance(support_ids),
                )
            )
        support_ids = tuple(element.id for element in table.supporting_elements)
        public_tables.append(
            TableTopology(
                id=table.id,
                bbox=table.outer.bbox,
                logical_rows=len(table.row_boundaries) - 1,
                logical_columns=len(table.column_boundaries) - 1,
                rows=tuple(
                    TopologyAxis(
                        id=f"{table.id}-row-{index:03d}",
                        index=index,
                        start=table.row_boundaries[index],
                        end=table.row_boundaries[index + 1],
                    )
                    for index in range(len(table.row_boundaries) - 1)
                ),
                columns=tuple(
                    TopologyAxis(
                        id=f"{table.id}-column-{index:03d}",
                        index=index,
                        start=table.column_boundaries[index],
                        end=table.column_boundaries[index + 1],
                    )
                    for index in range(len(table.column_boundaries) - 1)
                ),
                cells=tuple(public_cells),
                supporting_element_ids=support_ids,
                confidence=_derived_confidence(table.supporting_elements),
                provenance=_provenance(support_ids),
            )
        )

    primitive_roles = tuple(
        roles[element.id] for element in sorted((*rectangles, *lines), key=_element_key)
    )
    unassigned_primitive_ids = tuple(
        item.element_id
        for item in primitive_roles
        if item.role is TablePrimitiveRole.UNASSIGNED
    )
    return PageTableTopology(
        schema_version=TABLE_TOPOLOGY_SCHEMA_VERSION,
        provider=TABLE_TOPOLOGY_PROVIDER,
        provider_version=TABLE_TOPOLOGY_PROVIDER_VERSION,
        coordinate_space=TABLE_TOPOLOGY_COORDINATE_SPACE,
        boundary_tolerance_pt=TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT,
        tables=tuple(public_tables),
        primitive_roles=primitive_roles,
        diagnostics=TableTopologyDiagnostics(
            ambiguous_text_element_ids=tuple(ambiguous_text_ids),
            unassigned_text_element_ids=tuple(unassigned_text_ids),
            ambiguous_primitive_element_ids=tuple(
                element.id
                for element in sorted(
                    (
                        element
                        for element in (*rectangles, *lines)
                        if element.id in ambiguous_primitives
                    ),
                    key=_element_key,
                )
            ),
            unassigned_primitive_element_ids=unassigned_primitive_ids,
            rejected_table_outer_element_ids=tuple(rejected_outer_ids),
        ),
    )


def _build_grid(
    outer: RectangleElement,
    cells: tuple[RectangleElement, ...],
) -> _DetectedTable | None:
    # A physical table cell can contain other closed rectangles such as
    # checkboxes, response circles, or nested writing boxes.  Those embedded
    # controls are not peer cells and must not invalidate an otherwise complete
    # tiling.  Keep only the maximal rectangles directly below the outer border;
    # nested rectangles remain in the IR and are reported as unassigned
    # primitives by the topology layer.
    direct_cells = tuple(
        cell
        for cell in cells
        if not any(
            other is not cell and _strictly_contains(other.bbox, cell.bbox)
            for other in cells
        )
    )
    if len(direct_cells) < TABLE_TOPOLOGY_MINIMUM_CELL_COUNT:
        return None
    area_fraction = sum(_area(cell.bbox) for cell in direct_cells) / _area(outer.bbox)
    if not (
        TABLE_TOPOLOGY_MINIMUM_TILING_AREA_FRACTION
        <= area_fraction
        <= TABLE_TOPOLOGY_MAXIMUM_TILING_AREA_FRACTION
    ):
        return None

    x_values = [outer.bbox.x, outer.bbox.right]
    y_values = [outer.bbox.y, outer.bbox.bottom]
    for cell in direct_cells:
        x_values.extend((cell.bbox.x, cell.bbox.right))
        y_values.extend((cell.bbox.y, cell.bbox.bottom))
    columns = _cluster_boundaries(x_values, outer.bbox.x, outer.bbox.right)
    rows = _cluster_boundaries(y_values, outer.bbox.y, outer.bbox.bottom)
    if len(columns) < 2 or len(rows) < 2:
        return None

    detected_cells: list[_DetectedCell] = []
    occupied: set[tuple[int, int]] = set()
    for rectangle in sorted(direct_cells, key=_element_key):
        column_start = _nearest_boundary(columns, rectangle.bbox.x)
        column_end = _nearest_boundary(columns, rectangle.bbox.right)
        row_start = _nearest_boundary(rows, rectangle.bbox.y)
        row_end = _nearest_boundary(rows, rectangle.bbox.bottom)
        if None in {column_start, column_end, row_start, row_end}:
            return None
        assert column_start is not None
        assert column_end is not None
        assert row_start is not None
        assert row_end is not None
        if column_end <= column_start or row_end <= row_start:
            return None
        slots = {
            (row_index, column_index)
            for row_index in range(row_start, row_end)
            for column_index in range(column_start, column_end)
        }
        if occupied & slots:
            return None
        occupied.update(slots)
        detected_cells.append(
            _DetectedCell(
                rectangle=rectangle,
                row_index=row_start,
                column_index=column_start,
                rowspan=row_end - row_start,
                colspan=column_end - column_start,
            )
        )
    expected_slots = {
        (row_index, column_index)
        for row_index in range(len(rows) - 1)
        for column_index in range(len(columns) - 1)
    }
    if occupied != expected_slots:
        return None
    return _DetectedTable(
        outer=outer,
        row_boundaries=rows,
        column_boundaries=columns,
        cells=detected_cells,
    )


def _cluster_boundaries(
    values: list[float],
    outer_start: float,
    outer_end: float,
) -> tuple[float, ...]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT:
            groups.append([value])
        else:
            groups[-1].append(value)
    boundaries: list[float] = []
    for group in groups:
        if any(abs(value - outer_start) < 1e-9 for value in group):
            boundaries.append(outer_start)
        elif any(abs(value - outer_end) < 1e-9 for value in group):
            boundaries.append(outer_end)
        else:
            boundaries.append(round(sum(group) / len(group), 6))
    return tuple(boundaries)


def _nearest_boundary(boundaries: tuple[float, ...], value: float) -> int | None:
    index = min(range(len(boundaries)), key=lambda item: abs(boundaries[item] - value))
    if abs(boundaries[index] - value) > TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT:
        return None
    return index


def _table_line_role(
    line: LineElement,
    table: _DetectedTable,
) -> TablePrimitiveRole | None:
    horizontal = _is_horizontal(line)
    if horizontal:
        coordinate = (line.start.y + line.end.y) / 2
        boundaries = table.row_boundaries
        span_start, span_end = sorted((line.start.x, line.end.x))
        table_start, table_end = table.outer.bbox.x, table.outer.bbox.right
    else:
        coordinate = (line.start.x + line.end.x) / 2
        boundaries = table.column_boundaries
        span_start, span_end = sorted((line.start.y, line.end.y))
        table_start, table_end = table.outer.bbox.y, table.outer.bbox.bottom
    nearest = min(
        range(len(boundaries)), key=lambda index: abs(boundaries[index] - coordinate)
    )
    if abs(boundaries[nearest] - coordinate) > TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT:
        return None
    covers_table_edge = (
        span_start <= table_start + TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
        and span_end >= table_end - TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
    )
    if nearest in {0, len(boundaries) - 1} and covers_table_edge:
        return TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE
    return (
        TablePrimitiveRole.ROW_BOUNDARY
        if horizontal
        else TablePrimitiveRole.COLUMN_BOUNDARY
    )


def _line_supports_cell_edge(line: LineElement, cell: BoundingBox) -> bool:
    if _is_horizontal(line):
        coordinate = (line.start.y + line.end.y) / 2
        if (
            min(abs(coordinate - cell.y), abs(coordinate - cell.bottom))
            > TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
        ):
            return False
        start, end = sorted((line.start.x, line.end.x))
        return _overlap(start, end, cell.x, cell.right) > 0
    coordinate = (line.start.x + line.end.x) / 2
    if (
        min(abs(coordinate - cell.x), abs(coordinate - cell.right))
        > TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
    ):
        return False
    start, end = sorted((line.start.y, line.end.y))
    return _overlap(start, end, cell.y, cell.bottom) > 0


def _line_intersects_table(line: LineElement, table: BoundingBox) -> bool:
    tolerance = TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
    if _is_horizontal(line):
        coordinate = (line.start.y + line.end.y) / 2
        start, end = sorted((line.start.x, line.end.x))
        return (
            table.y - tolerance <= coordinate <= table.bottom + tolerance
            and _overlap(start, end, table.x, table.right) > 0
        )
    coordinate = (line.start.x + line.end.x) / 2
    start, end = sorted((line.start.y, line.end.y))
    return (
        table.x - tolerance <= coordinate <= table.right + tolerance
        and _overlap(start, end, table.y, table.bottom) > 0
    )


def _is_page_frame_line(line: LineElement, page: Page) -> bool:
    tolerance = TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
    if _is_horizontal(line):
        length = abs(line.end.x - line.start.x)
        return (
            min(abs(line.bbox.y), abs(line.bbox.bottom - page.size.height)) <= tolerance
            and length / page.size.width >= TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION
        )
    length = abs(line.end.y - line.start.y)
    return (
        min(abs(line.bbox.x), abs(line.bbox.right - page.size.width)) <= tolerance
        and length / page.size.height >= TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION
    )


def _strictly_contains(container: BoundingBox, item: BoundingBox) -> bool:
    tolerance = TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT
    return (
        _area(item) < _area(container) - 0.01
        and item.x >= container.x - tolerance
        and item.y >= container.y - tolerance
        and item.right <= container.right + tolerance
        and item.bottom <= container.bottom + tolerance
    )


def _contains_point(bbox: BoundingBox, x: float, y: float) -> bool:
    return bbox.x <= x <= bbox.right and bbox.y <= y <= bbox.bottom


def _is_horizontal(line: LineElement) -> bool:
    return abs(line.end.x - line.start.x) >= abs(line.end.y - line.start.y)


def _overlap(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> float:
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _area(bbox: BoundingBox) -> float:
    return bbox.width * bbox.height


def _element_key(element: LineElement | RectangleElement) -> tuple[object, ...]:
    type_order = 0 if isinstance(element, RectangleElement) else 1
    return (
        element.bbox.y,
        element.bbox.x,
        element.bbox.height,
        element.bbox.width,
        type_order,
        element.id,
    )


def _derived_confidence(
    elements: tuple[LineElement | RectangleElement, ...],
) -> float:
    values = tuple(
        element.confidence.overall
        for element in elements
        if element.confidence is not None
    )
    return round(min(values), 6) if values else 0.0


def _provenance(source_ids: tuple[str, ...]) -> TopologyProvenance:
    return TopologyProvenance(
        stage="derived",
        provider=TABLE_TOPOLOGY_PROVIDER,
        provider_version=TABLE_TOPOLOGY_PROVIDER_VERSION,
        source_element_ids=source_ids,
        parameters_digest=TABLE_TOPOLOGY_PARAMETERS_DIGEST,
    )


__all__ = [
    "TABLE_TOPOLOGY_BOUNDARY_TOLERANCE_PT",
    "TABLE_TOPOLOGY_MAXIMUM_TILING_AREA_FRACTION",
    "TABLE_TOPOLOGY_MINIMUM_CELL_COUNT",
    "TABLE_TOPOLOGY_MINIMUM_TILING_AREA_FRACTION",
    "TABLE_TOPOLOGY_PAGE_FRAME_AREA_FRACTION",
    "TABLE_TOPOLOGY_PARAMETERS_DIGEST",
    "infer_table_topology",
]
