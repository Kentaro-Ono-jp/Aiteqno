"""Strict typed contract for the table-topology Document IR extension."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import DocumentIRValidationError, ValidationIssue
from .model import (
    BoundingBox,
    LineElement,
    Page,
    RectangleElement,
    TextElement,
)


TABLE_TOPOLOGY_EXTENSION_KEY = "jp.reactorfront.aiteqno.table_topology"
TABLE_TOPOLOGY_SCHEMA_VERSION = "1.0"
TABLE_TOPOLOGY_PROVIDER = "aiteqno.table-topology"
TABLE_TOPOLOGY_PROVIDER_VERSION = "1.0.0"
TABLE_TOPOLOGY_COORDINATE_SPACE = "document-ir-points"


class TablePrimitiveRole(str, Enum):
    """The single semantic role assigned to each raw line/rectangle primitive."""

    PAGE_FRAME = "page_frame"
    PAGE_DECORATION = "page_decoration"
    TABLE_OUTER_BORDER = "table_outer_border"
    ROW_BOUNDARY = "row_boundary"
    COLUMN_BOUNDARY = "column_boundary"
    CELL_RECTANGLE = "cell_rectangle"
    DUPLICATED_SUPPORTING_PRIMITIVE = "duplicated_supporting_primitive"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class TopologyAxis:
    id: str
    index: int
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True, slots=True)
class TopologyProvenance:
    stage: str
    provider: str
    provider_version: str
    source_element_ids: tuple[str, ...]
    parameters_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "source_element_ids": list(self.source_element_ids),
            "parameters_digest": self.parameters_digest,
        }


@dataclass(frozen=True, slots=True)
class TableCellTopology:
    id: str
    row_index: int
    column_index: int
    rowspan: int
    colspan: int
    bbox: BoundingBox
    supporting_element_ids: tuple[str, ...]
    text_element_ids: tuple[str, ...]
    confidence: float
    provenance: TopologyProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "row_index": self.row_index,
            "column_index": self.column_index,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "bbox": _bbox_to_dict(self.bbox),
            "supporting_element_ids": list(self.supporting_element_ids),
            "text_element_ids": list(self.text_element_ids),
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TableTopology:
    id: str
    bbox: BoundingBox
    logical_rows: int
    logical_columns: int
    rows: tuple[TopologyAxis, ...]
    columns: tuple[TopologyAxis, ...]
    cells: tuple[TableCellTopology, ...]
    supporting_element_ids: tuple[str, ...]
    confidence: float
    provenance: TopologyProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bbox": _bbox_to_dict(self.bbox),
            "logical_grid": {
                "rows": self.logical_rows,
                "columns": self.logical_columns,
            },
            "rows": [row.to_dict() for row in self.rows],
            "columns": [column.to_dict() for column in self.columns],
            "cells": [cell.to_dict() for cell in self.cells],
            "supporting_element_ids": list(self.supporting_element_ids),
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PrimitiveRoleAssignment:
    element_id: str
    role: TablePrimitiveRole
    table_id: str | None = None
    cell_id: str | None = None
    duplicate_of_element_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "element_id": self.element_id,
            "role": self.role.value,
        }
        if self.table_id is not None:
            result["table_id"] = self.table_id
        if self.cell_id is not None:
            result["cell_id"] = self.cell_id
        if self.duplicate_of_element_id is not None:
            result["duplicate_of_element_id"] = self.duplicate_of_element_id
        return result


@dataclass(frozen=True, slots=True)
class TableTopologyDiagnostics:
    ambiguous_text_element_ids: tuple[str, ...] = ()
    unassigned_text_element_ids: tuple[str, ...] = ()
    ambiguous_primitive_element_ids: tuple[str, ...] = ()
    unassigned_primitive_element_ids: tuple[str, ...] = ()
    rejected_table_outer_element_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ambiguous_text_element_ids": list(self.ambiguous_text_element_ids),
            "unassigned_text_element_ids": list(self.unassigned_text_element_ids),
            "ambiguous_primitive_element_ids": list(
                self.ambiguous_primitive_element_ids
            ),
            "unassigned_primitive_element_ids": list(
                self.unassigned_primitive_element_ids
            ),
            "rejected_table_outer_element_ids": list(
                self.rejected_table_outer_element_ids
            ),
        }


@dataclass(frozen=True, slots=True)
class PageTableTopology:
    schema_version: str
    provider: str
    provider_version: str
    coordinate_space: str
    boundary_tolerance_pt: float
    tables: tuple[TableTopology, ...]
    primitive_roles: tuple[PrimitiveRoleAssignment, ...]
    diagnostics: TableTopologyDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "coordinate_space": self.coordinate_space,
            "boundary_tolerance_pt": self.boundary_tolerance_pt,
            "tables": [table.to_dict() for table in self.tables],
            "primitive_roles": [item.to_dict() for item in self.primitive_roles],
            "diagnostics": self.diagnostics.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        path: str = "$",
    ) -> PageTableTopology:
        obj = _object(
            value,
            path,
            required={
                "schema_version",
                "provider",
                "provider_version",
                "coordinate_space",
                "boundary_tolerance_pt",
                "tables",
                "primitive_roles",
                "diagnostics",
            },
        )
        schema_version = _constant_string(
            obj["schema_version"],
            f"{path}.schema_version",
            TABLE_TOPOLOGY_SCHEMA_VERSION,
        )
        provider = _constant_string(
            obj["provider"], f"{path}.provider", TABLE_TOPOLOGY_PROVIDER
        )
        provider_version = _constant_string(
            obj["provider_version"],
            f"{path}.provider_version",
            TABLE_TOPOLOGY_PROVIDER_VERSION,
        )
        coordinate_space = _constant_string(
            obj["coordinate_space"],
            f"{path}.coordinate_space",
            TABLE_TOPOLOGY_COORDINATE_SPACE,
        )
        tolerance = _number(
            obj["boundary_tolerance_pt"],
            f"{path}.boundary_tolerance_pt",
            minimum=0,
            exclusive_minimum=True,
        )
        tables = tuple(
            _parse_table(item, f"{path}.tables[{index}]")
            for index, item in enumerate(_array(obj["tables"], f"{path}.tables"))
        )
        primitive_roles = tuple(
            _parse_primitive_role(item, f"{path}.primitive_roles[{index}]")
            for index, item in enumerate(
                _array(obj["primitive_roles"], f"{path}.primitive_roles")
            )
        )
        diagnostics = _parse_diagnostics(obj["diagnostics"], f"{path}.diagnostics")
        return cls(
            schema_version=schema_version,
            provider=provider,
            provider_version=provider_version,
            coordinate_space=coordinate_space,
            boundary_tolerance_pt=tolerance,
            tables=tables,
            primitive_roles=primitive_roles,
            diagnostics=diagnostics,
        )


def read_page_table_topology(page: Page) -> PageTableTopology | None:
    """Return a validated typed topology extension from ``page`` when present."""

    if not isinstance(page, Page):
        raise TypeError("page must be a Document IR Page")
    value = page.extensions.get(TABLE_TOPOLOGY_EXTENSION_KEY)
    if value is None:
        return None
    path = f'$.extensions["{TABLE_TOPOLOGY_EXTENSION_KEY}"]'
    topology = PageTableTopology.from_dict(value, path=path)
    issues = validate_page_table_topology(page, topology, path=path)
    if issues:
        raise DocumentIRValidationError(issues)
    return topology


def validate_table_topology_extension(
    page: Page,
    value: Any,
    *,
    path: str,
) -> tuple[ValidationIssue, ...]:
    """Validate one raw extension value without aborting other IR checks."""

    try:
        topology = PageTableTopology.from_dict(value, path=path)
    except DocumentIRValidationError as exc:
        return exc.issues
    return validate_page_table_topology(page, topology, path=path)


def validate_page_table_topology(
    page: Page,
    topology: PageTableTopology,
    *,
    path: str,
) -> tuple[ValidationIssue, ...]:
    """Enforce cross-reference, ordering, geometry, and coverage invariants."""

    issues: list[ValidationIssue] = []
    tolerance = topology.boundary_tolerance_pt
    elements = {element.id: element for element in page.elements}
    primitive_ids = {
        element.id
        for element in page.elements
        if isinstance(element, (LineElement, RectangleElement))
    }
    text_ids = {
        element.id for element in page.elements if isinstance(element, TextElement)
    }
    used_ids = {page.id, *elements}
    table_ids: set[str] = set()
    cell_ids: set[str] = set()
    tables_by_id: dict[str, TableTopology] = {}
    cells_by_id: dict[str, TableCellTopology] = {}
    table_support_by_id: dict[str, set[str]] = {}
    cell_support_by_id: dict[str, set[str]] = {}
    assigned_text_ids: set[str] = set()
    classified_primitive_ids: set[str] = set()

    ordered_tables = sorted(
        topology.tables,
        key=lambda item: (
            item.bbox.y,
            item.bbox.x,
            item.bbox.height,
            item.bbox.width,
            item.id,
        ),
    )
    if list(topology.tables) != ordered_tables:
        _append(
            issues,
            f"{path}.tables",
            "tables must be in geometry order",
            "invalid_order",
        )

    for table_index, table in enumerate(topology.tables):
        table_path = f"{path}.tables[{table_index}]"
        expected_table_id = f"{page.id}-table-{table_index:04d}"
        if table.id != expected_table_id:
            _append(
                issues,
                f"{table_path}.id",
                f"table ID must be {expected_table_id!r}",
                "unstable_topology_id",
            )
        _register_extension_id(issues, used_ids, table.id, f"{table_path}.id")
        table_ids.add(table.id)
        tables_by_id[table.id] = table
        if not _bbox_inside_page(table.bbox, page, tolerance):
            _append(
                issues,
                f"{table_path}.bbox",
                "table bbox must remain within page bounds",
                "out_of_page_geometry",
            )
        if table.logical_rows != len(table.rows) or table.logical_columns != len(
            table.columns
        ):
            _append(
                issues,
                f"{table_path}.logical_grid",
                "logical grid dimensions must equal row and column arrays",
                "invalid_logical_grid",
            )
        _validate_axes(
            issues,
            table.rows,
            table_id=table.id,
            axis_name="row",
            expected_start=table.bbox.y,
            expected_end=table.bbox.bottom,
            path=f"{table_path}.rows",
            used_ids=used_ids,
            tolerance=tolerance,
        )
        _validate_axes(
            issues,
            table.columns,
            table_id=table.id,
            axis_name="column",
            expected_start=table.bbox.x,
            expected_end=table.bbox.right,
            path=f"{table_path}.columns",
            used_ids=used_ids,
            tolerance=tolerance,
        )

        table_support = _validate_element_id_array(
            issues,
            table.supporting_element_ids,
            primitive_ids,
            f"{table_path}.supporting_element_ids",
        )
        table_support_by_id[table.id] = table_support
        _validate_provenance_sources(
            issues,
            table.provenance,
            table.supporting_element_ids,
            f"{table_path}.provenance",
        )
        occupied: dict[tuple[int, int], str] = {}
        ordered_cells = sorted(
            table.cells,
            key=lambda item: (item.row_index, item.column_index, item.id),
        )
        if list(table.cells) != ordered_cells:
            _append(
                issues,
                f"{table_path}.cells",
                "cells must be in logical row/column order",
                "invalid_order",
            )
        for cell_index, cell in enumerate(table.cells):
            cell_path = f"{table_path}.cells[{cell_index}]"
            expected_cell_id = (
                f"{table.id}-cell-r{cell.row_index:03d}-c{cell.column_index:03d}"
            )
            if cell.id != expected_cell_id:
                _append(
                    issues,
                    f"{cell_path}.id",
                    f"cell ID must be {expected_cell_id!r}",
                    "unstable_topology_id",
                )
            _register_extension_id(issues, used_ids, cell.id, f"{cell_path}.id")
            cell_ids.add(cell.id)
            cells_by_id[cell.id] = cell
            if (
                cell.row_index + cell.rowspan > table.logical_rows
                or cell.column_index + cell.colspan > table.logical_columns
            ):
                _append(
                    issues,
                    cell_path,
                    "cell span must remain within the logical grid",
                    "invalid_cell_span",
                )
            if not _bbox_contains(table.bbox, cell.bbox, tolerance):
                _append(
                    issues,
                    f"{cell_path}.bbox",
                    "cell bbox must remain within its table bbox",
                    "out_of_table_geometry",
                )
            if (
                cell.row_index < len(table.rows)
                and cell.column_index < len(table.columns)
                and cell.row_index + cell.rowspan <= len(table.rows)
                and cell.column_index + cell.colspan <= len(table.columns)
            ):
                expected_top = table.rows[cell.row_index].start
                expected_bottom = table.rows[cell.row_index + cell.rowspan - 1].end
                expected_left = table.columns[cell.column_index].start
                expected_right = table.columns[cell.column_index + cell.colspan - 1].end
                if any(
                    difference > tolerance
                    for difference in (
                        abs(cell.bbox.y - expected_top),
                        abs(cell.bbox.bottom - expected_bottom),
                        abs(cell.bbox.x - expected_left),
                        abs(cell.bbox.right - expected_right),
                    )
                ):
                    _append(
                        issues,
                        f"{cell_path}.bbox",
                        "cell bbox edges must match its logical row/column span",
                        "invalid_cell_geometry",
                    )
            for row_index in range(cell.row_index, cell.row_index + cell.rowspan):
                for column_index in range(
                    cell.column_index, cell.column_index + cell.colspan
                ):
                    slot = (row_index, column_index)
                    if slot in occupied:
                        _append(
                            issues,
                            cell_path,
                            f"logical slot {slot} duplicates cell {occupied[slot]!r}",
                            "duplicate_logical_slot",
                        )
                    else:
                        occupied[slot] = cell.id
            cell_support = _validate_element_id_array(
                issues,
                cell.supporting_element_ids,
                primitive_ids,
                f"{cell_path}.supporting_element_ids",
            )
            cell_support_by_id[cell.id] = cell_support
            if not cell_support.issubset(table_support):
                _append(
                    issues,
                    f"{cell_path}.supporting_element_ids",
                    "cell support must be included in table support",
                    "invalid_support_reference",
                )
            _validate_provenance_sources(
                issues,
                cell.provenance,
                cell.supporting_element_ids,
                f"{cell_path}.provenance",
            )
            _validate_element_id_array(
                issues,
                cell.text_element_ids,
                text_ids,
                f"{cell_path}.text_element_ids",
            )
            valid_cell_text_ids = tuple(
                text_id for text_id in cell.text_element_ids if text_id in text_ids
            )
            for text_id in valid_cell_text_ids:
                if text_id in assigned_text_ids:
                    _append(
                        issues,
                        f"{cell_path}.text_element_ids",
                        f"text element {text_id!r} is assigned to multiple cells",
                        "duplicate_text_assignment",
                    )
                assigned_text_ids.add(text_id)
                element = elements[text_id]
                center_x = element.bbox.x + element.bbox.width / 2
                center_y = element.bbox.y + element.bbox.height / 2
                if not (
                    cell.bbox.x - tolerance <= center_x <= cell.bbox.right + tolerance
                    and cell.bbox.y - tolerance
                    <= center_y
                    <= cell.bbox.bottom + tolerance
                ):
                    _append(
                        issues,
                        f"{cell_path}.text_element_ids",
                        f"text element {text_id!r} center lies outside the cell bbox",
                        "text_outside_cell",
                    )
            reading_orders = [
                elements[text_id].reading_order for text_id in valid_cell_text_ids
            ]
            if reading_orders != sorted(reading_orders):
                _append(
                    issues,
                    f"{cell_path}.text_element_ids",
                    "cell text IDs must follow Document IR reading order",
                    "invalid_order",
                )

        expected_slots = {
            (row_index, column_index)
            for row_index in range(table.logical_rows)
            for column_index in range(table.logical_columns)
        }
        if set(occupied) != expected_slots:
            _append(
                issues,
                f"{table_path}.cells",
                "cells must cover every logical grid slot exactly once",
                "incomplete_logical_grid",
            )

    for role_index, assignment in enumerate(topology.primitive_roles):
        role_path = f"{path}.primitive_roles[{role_index}]"
        if assignment.element_id in classified_primitive_ids:
            _append(
                issues,
                f"{role_path}.element_id",
                f"primitive {assignment.element_id!r} has multiple roles",
                "duplicate_primitive_role",
            )
        classified_primitive_ids.add(assignment.element_id)
        if assignment.element_id not in primitive_ids:
            _append(
                issues,
                f"{role_path}.element_id",
                f"unknown line/rectangle element {assignment.element_id!r}",
                "unknown_element",
            )
        _validate_role_references(
            issues,
            assignment,
            table_ids=table_ids,
            cell_ids=cell_ids,
            primitive_ids=primitive_ids,
            path=role_path,
        )
        element = elements.get(assignment.element_id)
        if assignment.role in {
            TablePrimitiveRole.ROW_BOUNDARY,
            TablePrimitiveRole.COLUMN_BOUNDARY,
            TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE,
        } and not isinstance(element, LineElement):
            _append(
                issues,
                f"{role_path}.element_id",
                "boundary and duplicate roles require a line element",
                "invalid_primitive_type",
            )
        if assignment.role in {
            TablePrimitiveRole.TABLE_OUTER_BORDER,
            TablePrimitiveRole.CELL_RECTANGLE,
        } and not isinstance(element, RectangleElement):
            _append(
                issues,
                f"{role_path}.element_id",
                "table and cell border roles require a rectangle element",
                "invalid_primitive_type",
            )
        if (
            assignment.role is TablePrimitiveRole.TABLE_OUTER_BORDER
            and assignment.table_id in tables_by_id
            and isinstance(element, RectangleElement)
        ):
            table = tables_by_id[assignment.table_id]
            if element.bbox != table.bbox:
                _append(
                    issues,
                    f"{role_path}.element_id",
                    "table outer rectangle bbox must equal the table bbox",
                    "invalid_role_geometry",
                )
        if (
            assignment.role is TablePrimitiveRole.CELL_RECTANGLE
            and assignment.cell_id in cells_by_id
            and isinstance(element, RectangleElement)
        ):
            cell = cells_by_id[assignment.cell_id]
            if element.bbox != cell.bbox:
                _append(
                    issues,
                    f"{role_path}.element_id",
                    "cell rectangle bbox must equal the cell bbox",
                    "invalid_role_geometry",
                )
            if assignment.table_id is not None and not assignment.cell_id.startswith(
                f"{assignment.table_id}-cell-"
            ):
                _append(
                    issues,
                    f"{role_path}.cell_id",
                    "cell role must reference a cell owned by table_id",
                    "invalid_role_reference",
                )
        if (
            assignment.role is TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE
            and assignment.table_id in tables_by_id
            and assignment.duplicate_of_element_id
            != next(
                (
                    item.element_id
                    for item in topology.primitive_roles
                    if item.role is TablePrimitiveRole.TABLE_OUTER_BORDER
                    and item.table_id == assignment.table_id
                ),
                None,
            )
        ):
            _append(
                issues,
                f"{role_path}.duplicate_of_element_id",
                "duplicate table edge must reference its table outer rectangle",
                "invalid_role_reference",
            )
    if classified_primitive_ids != primitive_ids:
        _append(
            issues,
            f"{path}.primitive_roles",
            "every line and rectangle primitive must have exactly one role",
            "incomplete_primitive_classification",
        )

    outer_roles = [
        item
        for item in topology.primitive_roles
        if item.role is TablePrimitiveRole.TABLE_OUTER_BORDER
    ]
    cell_roles = [
        item
        for item in topology.primitive_roles
        if item.role is TablePrimitiveRole.CELL_RECTANGLE
    ]
    for table_id in table_ids:
        matches = [item for item in outer_roles if item.table_id == table_id]
        if len(matches) != 1:
            _append(
                issues,
                f"{path}.primitive_roles",
                f"table {table_id!r} must have exactly one outer rectangle role",
                "invalid_table_support",
            )
        elif matches[0].element_id not in table_support_by_id[table_id]:
            _append(
                issues,
                f"{path}.primitive_roles",
                f"table {table_id!r} outer rectangle must be in table support",
                "invalid_table_support",
            )
    for cell_id in cell_ids:
        matches = [item for item in cell_roles if item.cell_id == cell_id]
        if len(matches) != 1:
            _append(
                issues,
                f"{path}.primitive_roles",
                f"cell {cell_id!r} must have exactly one rectangle role",
                "invalid_cell_support",
            )
        elif matches[0].element_id not in cell_support_by_id[cell_id]:
            _append(
                issues,
                f"{path}.primitive_roles",
                f"cell {cell_id!r} rectangle must be in cell support",
                "invalid_cell_support",
            )

    diagnostics = topology.diagnostics
    ambiguous_text = _validate_element_id_array(
        issues,
        diagnostics.ambiguous_text_element_ids,
        text_ids,
        f"{path}.diagnostics.ambiguous_text_element_ids",
    )
    unassigned_text = _validate_element_id_array(
        issues,
        diagnostics.unassigned_text_element_ids,
        text_ids,
        f"{path}.diagnostics.unassigned_text_element_ids",
    )
    if assigned_text_ids & ambiguous_text or assigned_text_ids & unassigned_text:
        _append(
            issues,
            f"{path}.diagnostics",
            "assigned, ambiguous, and unassigned text sets must be disjoint",
            "duplicate_text_classification",
        )
    text_order = {
        element.id: element.reading_order
        for element in page.elements
        if isinstance(element, TextElement)
    }
    for diagnostic_name, values in (
        ("ambiguous_text_element_ids", diagnostics.ambiguous_text_element_ids),
        ("unassigned_text_element_ids", diagnostics.unassigned_text_element_ids),
    ):
        known_values = [identifier for identifier in values if identifier in text_order]
        if known_values != sorted(known_values, key=text_order.__getitem__):
            _append(
                issues,
                f"{path}.diagnostics.{diagnostic_name}",
                "text diagnostics must follow Document IR reading order",
                "invalid_order",
            )
    if assigned_text_ids | ambiguous_text | unassigned_text != text_ids:
        _append(
            issues,
            f"{path}.diagnostics",
            "every text element must be assigned, ambiguous, or unassigned",
            "incomplete_text_classification",
        )
    _validate_element_id_array(
        issues,
        diagnostics.ambiguous_primitive_element_ids,
        primitive_ids,
        f"{path}.diagnostics.ambiguous_primitive_element_ids",
    )
    unassigned_primitives = _validate_element_id_array(
        issues,
        diagnostics.unassigned_primitive_element_ids,
        primitive_ids,
        f"{path}.diagnostics.unassigned_primitive_element_ids",
    )
    role_unassigned = {
        item.element_id
        for item in topology.primitive_roles
        if item.role is TablePrimitiveRole.UNASSIGNED
    }
    if unassigned_primitives != role_unassigned:
        _append(
            issues,
            f"{path}.diagnostics.unassigned_primitive_element_ids",
            "unassigned diagnostic IDs must equal primitives with the unassigned role",
            "invalid_diagnostic",
        )
    _validate_element_id_array(
        issues,
        diagnostics.rejected_table_outer_element_ids,
        {
            element.id
            for element in page.elements
            if isinstance(element, RectangleElement)
        },
        f"{path}.diagnostics.rejected_table_outer_element_ids",
    )
    return tuple(issues)


def _parse_table(value: Any, path: str) -> TableTopology:
    obj = _object(
        value,
        path,
        required={
            "id",
            "bbox",
            "logical_grid",
            "rows",
            "columns",
            "cells",
            "supporting_element_ids",
            "confidence",
            "provenance",
        },
    )
    grid = _object(
        obj["logical_grid"],
        f"{path}.logical_grid",
        required={"rows", "columns"},
    )
    return TableTopology(
        id=_string(obj["id"], f"{path}.id"),
        bbox=_parse_bbox(obj["bbox"], f"{path}.bbox"),
        logical_rows=_integer(grid["rows"], f"{path}.logical_grid.rows", minimum=1),
        logical_columns=_integer(
            grid["columns"], f"{path}.logical_grid.columns", minimum=1
        ),
        rows=tuple(
            _parse_axis(item, f"{path}.rows[{index}]")
            for index, item in enumerate(_array(obj["rows"], f"{path}.rows"))
        ),
        columns=tuple(
            _parse_axis(item, f"{path}.columns[{index}]")
            for index, item in enumerate(_array(obj["columns"], f"{path}.columns"))
        ),
        cells=tuple(
            _parse_cell(item, f"{path}.cells[{index}]")
            for index, item in enumerate(_array(obj["cells"], f"{path}.cells"))
        ),
        supporting_element_ids=_string_array(
            obj["supporting_element_ids"], f"{path}.supporting_element_ids"
        ),
        confidence=_number(
            obj["confidence"], f"{path}.confidence", minimum=0, maximum=1
        ),
        provenance=_parse_provenance(obj["provenance"], f"{path}.provenance"),
    )


def _parse_axis(value: Any, path: str) -> TopologyAxis:
    obj = _object(value, path, required={"id", "index", "start", "end"})
    start = _number(obj["start"], f"{path}.start", minimum=0)
    end = _number(obj["end"], f"{path}.end", minimum=0)
    if end <= start:
        _fail(path, "axis end must be greater than start", "invalid_axis")
    return TopologyAxis(
        id=_string(obj["id"], f"{path}.id"),
        index=_integer(obj["index"], f"{path}.index", minimum=0),
        start=start,
        end=end,
    )


def _parse_cell(value: Any, path: str) -> TableCellTopology:
    obj = _object(
        value,
        path,
        required={
            "id",
            "row_index",
            "column_index",
            "rowspan",
            "colspan",
            "bbox",
            "supporting_element_ids",
            "text_element_ids",
            "confidence",
            "provenance",
        },
    )
    return TableCellTopology(
        id=_string(obj["id"], f"{path}.id"),
        row_index=_integer(obj["row_index"], f"{path}.row_index", minimum=0),
        column_index=_integer(obj["column_index"], f"{path}.column_index", minimum=0),
        rowspan=_integer(obj["rowspan"], f"{path}.rowspan", minimum=1),
        colspan=_integer(obj["colspan"], f"{path}.colspan", minimum=1),
        bbox=_parse_bbox(obj["bbox"], f"{path}.bbox"),
        supporting_element_ids=_string_array(
            obj["supporting_element_ids"], f"{path}.supporting_element_ids"
        ),
        text_element_ids=_string_array(
            obj["text_element_ids"], f"{path}.text_element_ids"
        ),
        confidence=_number(
            obj["confidence"], f"{path}.confidence", minimum=0, maximum=1
        ),
        provenance=_parse_provenance(obj["provenance"], f"{path}.provenance"),
    )


def _parse_provenance(value: Any, path: str) -> TopologyProvenance:
    obj = _object(
        value,
        path,
        required={
            "stage",
            "provider",
            "provider_version",
            "source_element_ids",
            "parameters_digest",
        },
    )
    stage = _constant_string(obj["stage"], f"{path}.stage", "derived")
    provider = _constant_string(
        obj["provider"], f"{path}.provider", TABLE_TOPOLOGY_PROVIDER
    )
    provider_version = _constant_string(
        obj["provider_version"],
        f"{path}.provider_version",
        TABLE_TOPOLOGY_PROVIDER_VERSION,
    )
    digest = _string(obj["parameters_digest"], f"{path}.parameters_digest")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        _fail(
            f"{path}.parameters_digest",
            "parameters digest must be 64 lower-case hex digits",
            "invalid_digest",
        )
    return TopologyProvenance(
        stage=stage,
        provider=provider,
        provider_version=provider_version,
        source_element_ids=_string_array(
            obj["source_element_ids"], f"{path}.source_element_ids"
        ),
        parameters_digest=digest,
    )


def _parse_primitive_role(value: Any, path: str) -> PrimitiveRoleAssignment:
    obj = _object(
        value,
        path,
        required={"element_id", "role"},
        optional={"table_id", "cell_id", "duplicate_of_element_id"},
    )
    raw_role = _string(obj["role"], f"{path}.role")
    try:
        role = TablePrimitiveRole(raw_role)
    except ValueError as exc:
        _fail(f"{path}.role", f"unknown primitive role {raw_role!r}", "invalid_role")
        raise AssertionError from exc
    return PrimitiveRoleAssignment(
        element_id=_string(obj["element_id"], f"{path}.element_id"),
        role=role,
        table_id=_optional_string(obj.get("table_id"), f"{path}.table_id"),
        cell_id=_optional_string(obj.get("cell_id"), f"{path}.cell_id"),
        duplicate_of_element_id=_optional_string(
            obj.get("duplicate_of_element_id"),
            f"{path}.duplicate_of_element_id",
        ),
    )


def _parse_diagnostics(value: Any, path: str) -> TableTopologyDiagnostics:
    fields = {
        "ambiguous_text_element_ids",
        "unassigned_text_element_ids",
        "ambiguous_primitive_element_ids",
        "unassigned_primitive_element_ids",
        "rejected_table_outer_element_ids",
    }
    obj = _object(value, path, required=fields)
    return TableTopologyDiagnostics(
        ambiguous_text_element_ids=_string_array(
            obj["ambiguous_text_element_ids"],
            f"{path}.ambiguous_text_element_ids",
        ),
        unassigned_text_element_ids=_string_array(
            obj["unassigned_text_element_ids"],
            f"{path}.unassigned_text_element_ids",
        ),
        ambiguous_primitive_element_ids=_string_array(
            obj["ambiguous_primitive_element_ids"],
            f"{path}.ambiguous_primitive_element_ids",
        ),
        unassigned_primitive_element_ids=_string_array(
            obj["unassigned_primitive_element_ids"],
            f"{path}.unassigned_primitive_element_ids",
        ),
        rejected_table_outer_element_ids=_string_array(
            obj["rejected_table_outer_element_ids"],
            f"{path}.rejected_table_outer_element_ids",
        ),
    )


def _validate_axes(
    issues: list[ValidationIssue],
    axes: tuple[TopologyAxis, ...],
    *,
    table_id: str,
    axis_name: str,
    expected_start: float,
    expected_end: float,
    path: str,
    used_ids: set[str],
    tolerance: float,
) -> None:
    for index, axis in enumerate(axes):
        axis_path = f"{path}[{index}]"
        expected_id = f"{table_id}-{axis_name}-{index:03d}"
        if axis.index != index:
            _append(
                issues,
                f"{axis_path}.index",
                "axis indexes must be contiguous and match array order",
                "invalid_axis_order",
            )
        if axis.id != expected_id:
            _append(
                issues,
                f"{axis_path}.id",
                f"axis ID must be {expected_id!r}",
                "unstable_topology_id",
            )
        _register_extension_id(issues, used_ids, axis.id, f"{axis_path}.id")
        if index and abs(axes[index - 1].end - axis.start) > tolerance:
            _append(
                issues,
                axis_path,
                "adjacent axes must share one boundary",
                "invalid_axis_geometry",
            )
    if axes and (
        abs(axes[0].start - expected_start) > tolerance
        or abs(axes[-1].end - expected_end) > tolerance
    ):
        _append(
            issues,
            path,
            "axes must span the full table bbox",
            "invalid_axis_geometry",
        )


def _validate_role_references(
    issues: list[ValidationIssue],
    assignment: PrimitiveRoleAssignment,
    *,
    table_ids: set[str],
    cell_ids: set[str],
    primitive_ids: set[str],
    path: str,
) -> None:
    table_roles = {
        TablePrimitiveRole.TABLE_OUTER_BORDER,
        TablePrimitiveRole.ROW_BOUNDARY,
        TablePrimitiveRole.COLUMN_BOUNDARY,
        TablePrimitiveRole.CELL_RECTANGLE,
        TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE,
    }
    if assignment.role in table_roles:
        if assignment.table_id not in table_ids:
            _append(
                issues,
                f"{path}.table_id",
                "table role must reference an existing table",
                "unknown_table",
            )
    elif assignment.table_id is not None:
        _append(
            issues,
            f"{path}.table_id",
            "non-table role must not carry table_id",
            "invalid_role_reference",
        )
    if assignment.role is TablePrimitiveRole.CELL_RECTANGLE:
        if assignment.cell_id not in cell_ids:
            _append(
                issues,
                f"{path}.cell_id",
                "cell rectangle must reference an existing cell",
                "unknown_cell",
            )
    elif assignment.cell_id is not None:
        _append(
            issues,
            f"{path}.cell_id",
            "non-cell role must not carry cell_id",
            "invalid_role_reference",
        )
    if assignment.role is TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE:
        if assignment.duplicate_of_element_id not in primitive_ids:
            _append(
                issues,
                f"{path}.duplicate_of_element_id",
                "duplicate role must reference an existing primitive",
                "unknown_element",
            )
    elif assignment.duplicate_of_element_id is not None:
        _append(
            issues,
            f"{path}.duplicate_of_element_id",
            "only duplicate roles may carry duplicate_of_element_id",
            "invalid_role_reference",
        )


def _validate_element_id_array(
    issues: list[ValidationIssue],
    values: tuple[str, ...],
    allowed: set[str],
    path: str,
) -> set[str]:
    result: set[str] = set()
    for index, identifier in enumerate(values):
        if identifier in result:
            _append(
                issues,
                f"{path}[{index}]",
                f"ID {identifier!r} is duplicated",
                "duplicate_id",
            )
        result.add(identifier)
        if identifier not in allowed:
            _append(
                issues,
                f"{path}[{index}]",
                f"unknown element ID {identifier!r}",
                "unknown_element",
            )
    return result


def _validate_provenance_sources(
    issues: list[ValidationIssue],
    provenance: TopologyProvenance,
    supporting_ids: tuple[str, ...],
    path: str,
) -> None:
    if provenance.source_element_ids != supporting_ids:
        _append(
            issues,
            f"{path}.source_element_ids",
            "provenance sources must exactly equal supporting element IDs",
            "invalid_provenance",
        )


def _register_extension_id(
    issues: list[ValidationIssue],
    used_ids: set[str],
    identifier: str,
    path: str,
) -> None:
    if identifier in used_ids:
        _append(issues, path, f"ID {identifier!r} is duplicated", "duplicate_id")
    used_ids.add(identifier)


def _bbox_inside_page(bbox: BoundingBox, page: Page, tolerance: float) -> bool:
    return (
        bbox.x >= -tolerance
        and bbox.y >= -tolerance
        and bbox.right <= page.size.width + tolerance
        and bbox.bottom <= page.size.height + tolerance
    )


def _bbox_contains(container: BoundingBox, item: BoundingBox, tolerance: float) -> bool:
    return (
        item.x >= container.x - tolerance
        and item.y >= container.y - tolerance
        and item.right <= container.right + tolerance
        and item.bottom <= container.bottom + tolerance
    )


def _bbox_to_dict(bbox: BoundingBox) -> dict[str, float]:
    return {
        "x": bbox.x,
        "y": bbox.y,
        "width": bbox.width,
        "height": bbox.height,
    }


def _parse_bbox(value: Any, path: str) -> BoundingBox:
    obj = _object(value, path, required={"x", "y", "width", "height"})
    width = _number(obj["width"], f"{path}.width", minimum=0, exclusive_minimum=True)
    height = _number(obj["height"], f"{path}.height", minimum=0, exclusive_minimum=True)
    return BoundingBox(
        x=_number(obj["x"], f"{path}.x", minimum=0),
        y=_number(obj["y"], f"{path}.y", minimum=0),
        width=width,
        height=height,
    )


def _object(
    value: Any,
    path: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "value must be an object", "invalid_type")
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        _fail(path, f"missing required fields: {', '.join(missing)}", "missing_field")
    if unknown:
        _fail(path, f"unknown fields: {', '.join(unknown)}", "unknown_field")
    return value


def _array(value: Any, path: str) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(path, "value must be an array", "invalid_type")
    return tuple(value)


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "value must be a non-empty string", "invalid_type")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _constant_string(value: Any, path: str, expected: str) -> str:
    actual = _string(value, path)
    if actual != expected:
        _fail(path, f"value must be {expected!r}", "invalid_constant")
    return actual


def _string_array(value: Any, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )


def _integer(value: Any, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, f"value must be an integer >= {minimum}", "invalid_type")
    return value


def _number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "value must be a finite number", "invalid_type")
    number = float(value)
    if not math.isfinite(number):
        _fail(path, "value must be a finite number", "invalid_type")
    if minimum is not None and (
        number <= minimum if exclusive_minimum else number < minimum
    ):
        comparator = ">" if exclusive_minimum else ">="
        _fail(path, f"value must be {comparator} {minimum:g}", "invalid_number")
    if maximum is not None and number > maximum:
        _fail(path, f"value must be <= {maximum:g}", "invalid_number")
    return number


def _append(
    issues: list[ValidationIssue],
    path: str,
    message: str,
    code: str,
) -> None:
    issues.append(ValidationIssue(path=path, message=message, code=code))


def _fail(path: str, message: str, code: str) -> None:
    raise DocumentIRValidationError.single(path, message, code)


__all__ = [
    "TABLE_TOPOLOGY_COORDINATE_SPACE",
    "TABLE_TOPOLOGY_EXTENSION_KEY",
    "TABLE_TOPOLOGY_PROVIDER",
    "TABLE_TOPOLOGY_PROVIDER_VERSION",
    "TABLE_TOPOLOGY_SCHEMA_VERSION",
    "PageTableTopology",
    "PrimitiveRoleAssignment",
    "TableCellTopology",
    "TablePrimitiveRole",
    "TableTopology",
    "TableTopologyDiagnostics",
    "TopologyAxis",
    "TopologyProvenance",
    "read_page_table_topology",
    "validate_page_table_topology",
    "validate_table_topology_extension",
]
