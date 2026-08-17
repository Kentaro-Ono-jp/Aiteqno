"""DOCX read-back observations and filesystem evaluation artifacts."""

from __future__ import annotations

import hashlib
import os
import posixpath
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from os import PathLike
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docx import Document as open_docx

from aiteqno._version import __version__
from aiteqno.domain import ElementType
from aiteqno.ports.evaluation import (
    DocxObservation,
    DocxObservationError,
    EvaluationWriteError,
    ObservedElement,
    RelationshipKind,
    RestorationEvaluationResult,
    StructuralRelationship,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = f"{{{_W_NS}}}"
_A = f"{{{_A_NS}}}"
_R = f"{{{_R_NS}}}"
_REL = f"{{{_REL_NS}}}"
_REQUIRED_PACKAGE_PARTS = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
)
_BORDER_SIDES = ("top", "left", "bottom", "right")
_SOURCE_TAG_PREFIX = "aiteqno-source:"
_TABLE_CAPTION_PREFIX = "aiteqno-table:"


@dataclass(slots=True)
class _ObservationCollector:
    elements: list[ObservedElement] = field(default_factory=list)
    relationships: list[StructuralRelationship] = field(default_factory=list)
    relationship_seen: set[tuple[str, str, str]] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    error_seen: set[str] = field(default_factory=set)
    text_ids: list[str] = field(default_factory=list)
    text_count: int = 0
    image_count: int = 0
    line_count: int = 0
    rectangle_count: int = 0
    paragraph_count: int = 0
    table_count: int = 0
    source_element_ids: set[str] = field(default_factory=set)

    def relationship(
        self,
        kind: RelationshipKind,
        source: str,
        target: str,
    ) -> None:
        identity = (kind.value, source, target)
        if source != target and identity not in self.relationship_seen:
            self.relationship_seen.add(identity)
            self.relationships.append(
                StructuralRelationship(kind=kind, source=source, target=target)
            )

    def error(self, message: str) -> None:
        if message not in self.error_seen:
            self.error_seen.add(message)
            self.errors.append(message)


class PythonDocxObserver:
    """Read text, structure, media, and border elements from generated DOCX."""

    observer_name = "aiteqno-python-docx-observer"
    observer_version = __version__

    def observe(self, docx_path: str | PathLike[str]) -> DocxObservation:
        target = Path(docx_path)
        if target.suffix.lower() != ".docx":
            raise ValueError("docx_path must use the .docx extension")
        if not target.is_file():
            raise DocxObservationError(f"DOCX input does not exist: {target}")
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise DocxObservationError(f"DOCX input is unreadable: {target}") from exc
        digest = hashlib.sha256(payload).hexdigest()

        try:
            with ZipFile(BytesIO(payload)) as package:
                bad_part = package.testzip()
                if bad_part is not None:
                    return self._failed(
                        digest,
                        package_readable=False,
                        error=f"DOCX package has a corrupt part: {bad_part}",
                    )
                names = set(package.namelist())
                missing_parts = sorted(_REQUIRED_PACKAGE_PARTS - names)
                if missing_parts:
                    return self._failed(
                        digest,
                        package_readable=False,
                        error="DOCX package is missing required parts: "
                        + ", ".join(missing_parts),
                    )
                document_xml = package.read("word/document.xml")
                relationships_xml = (
                    package.read("word/_rels/document.xml.rels")
                    if "word/_rels/document.xml.rels" in names
                    else None
                )
                media_payloads = {
                    name: package.read(name)
                    for name in sorted(names)
                    if name.startswith("word/media/") and not name.endswith("/")
                }
        except BadZipFile:
            return self._failed(
                digest,
                package_readable=False,
                error="DOCX input is not a readable OPC ZIP package",
            )
        except OSError as exc:
            return self._failed(
                digest,
                package_readable=False,
                error=f"DOCX package could not be read: {exc}",
            )

        try:
            open_docx(BytesIO(payload))
        except Exception as exc:  # python-docx exposes several parser exceptions
            return self._failed(
                digest,
                package_readable=True,
                error=f"python-docx could not reopen the document: {type(exc).__name__}",
            )

        try:
            root = ElementTree.fromstring(document_xml)
            relationship_targets, external_relationships = _relationships(
                relationships_xml
            )
        except ElementTree.ParseError as exc:
            return self._failed(
                digest,
                package_readable=True,
                error=f"DOCX XML is malformed: {exc}",
            )

        collector = _ObservationCollector()
        body = root.find(f"{_W}body")
        if body is None:
            return self._failed(
                digest,
                package_readable=True,
                error="DOCX document.xml does not contain a body",
            )
        self._observe_container(
            body,
            parent_key="body",
            collector=collector,
            relationship_targets=relationship_targets,
            media_payloads=media_payloads,
        )
        for source, target_key in zip(
            collector.text_ids,
            collector.text_ids[1:],
            strict=False,
        ):
            collector.relationship(
                RelationshipKind.READING_ORDER,
                source,
                target_key,
            )

        return DocxObservation(
            observer_name=self.observer_name,
            observer_version=self.observer_version,
            source_sha256=digest,
            package_readable=True,
            python_docx_reopenable=True,
            elements=tuple(collector.elements),
            relationships=tuple(collector.relationships),
            external_relationships=tuple(external_relationships),
            errors=tuple(collector.errors),
        )

    def _observe_container(
        self,
        container: ElementTree.Element,
        *,
        parent_key: str,
        collector: _ObservationCollector,
        relationship_targets: dict[str, str],
        media_payloads: dict[str, bytes],
    ) -> None:
        previous_block: str | None = None
        for child in container:
            if child.tag == f"{_W}p":
                block_key = f"docx-paragraph-{collector.paragraph_count:04d}"
                collector.paragraph_count += 1
                self._observe_paragraph(
                    child,
                    block_key=block_key,
                    collector=collector,
                    relationship_targets=relationship_targets,
                    media_payloads=media_payloads,
                )
            elif child.tag == f"{_W}tbl":
                fallback_key = f"docx-table-{collector.table_count:04d}"
                collector.table_count += 1
                source_table_key = _source_table_key(child)
                block_key = source_table_key or fallback_key
                self._observe_table(
                    child,
                    table_key=block_key,
                    source_addressable=source_table_key is not None,
                    collector=collector,
                    relationship_targets=relationship_targets,
                    media_payloads=media_payloads,
                )
            else:
                continue
            collector.relationship(
                RelationshipKind.CONTAINMENT,
                parent_key,
                block_key,
            )
            if previous_block is not None:
                collector.relationship(
                    RelationshipKind.ADJACENCY,
                    previous_block,
                    block_key,
                )
            previous_block = block_key

    def _observe_paragraph(
        self,
        paragraph: ElementTree.Element,
        *,
        block_key: str,
        collector: _ObservationCollector,
        relationship_targets: dict[str, str],
        media_payloads: dict[str, bytes],
    ) -> None:
        # The renderer uses U+200B only as an OOXML layout marker in otherwise
        # empty bordered cells. It is not visible document content.
        tagged_texts = _source_tagged_texts(paragraph)
        if tagged_texts:
            for source_element_id, text in tagged_texts:
                if source_element_id in collector.source_element_ids:
                    collector.error(
                        f"DOCX repeats source-tagged text element {source_element_id!r}"
                    )
                collector.source_element_ids.add(source_element_id)
                self._record_text(
                    text,
                    block_key=block_key,
                    source_element_id=source_element_id,
                    collector=collector,
                )
        else:
            text = _paragraph_text(paragraph).replace("\u200b", "")
            if text.strip():
                self._record_text(
                    text,
                    block_key=block_key,
                    source_element_id=None,
                    collector=collector,
                )

        paragraph_properties = paragraph.find(f"{_W}pPr")
        paragraph_borders = (
            None
            if paragraph_properties is None
            else paragraph_properties.find(f"{_W}pBdr")
        )
        for _side in _visible_border_sides(paragraph_borders):
            line_id = f"docx-line-{collector.line_count:04d}"
            collector.line_count += 1
            collector.elements.append(
                ObservedElement(id=line_id, element_type=ElementType.LINE)
            )
            collector.relationship(
                RelationshipKind.CONTAINMENT,
                block_key,
                line_id,
            )

        self._observe_images(
            paragraph,
            parent_key=block_key,
            collector=collector,
            relationship_targets=relationship_targets,
            media_payloads=media_payloads,
        )

    @staticmethod
    def _record_text(
        text: str,
        *,
        block_key: str,
        source_element_id: str | None,
        collector: _ObservationCollector,
    ) -> None:
        element_id = f"docx-text-{collector.text_count:04d}"
        collector.elements.append(
            ObservedElement(
                id=element_id,
                element_type=ElementType.TEXT,
                text=text,
                reading_order=collector.text_count,
                source_element_id=source_element_id,
            )
        )
        collector.text_ids.append(element_id)
        collector.text_count += 1
        collector.relationship(
            RelationshipKind.CONTAINMENT,
            block_key,
            element_id,
        )

    def _observe_table(
        self,
        table: ElementTree.Element,
        *,
        table_key: str,
        source_addressable: bool,
        collector: _ObservationCollector,
        relationship_targets: dict[str, str],
        media_payloads: dict[str, bytes],
    ) -> None:
        active_vertical_merges: dict[int, str] = {}
        for row_index, row in enumerate(table.findall(f"{_W}tr")):
            previous_cell: str | None = None
            logical_column = 0
            for cell in row.findall(f"{_W}tc"):
                span = _cell_grid_span(cell)
                vertical_merge = _cell_vertical_merge(cell)
                if vertical_merge == "continue":
                    if logical_column not in active_vertical_merges:
                        collector.error(
                            "vertical table merge continuation has no restart cell"
                        )
                    logical_column += span
                    continue
                cell_key = f"{table_key}-cell-r{row_index:03d}-c{logical_column:03d}"
                collector.relationship(
                    RelationshipKind.CONTAINMENT,
                    table_key,
                    cell_key,
                )
                if previous_cell is not None:
                    collector.relationship(
                        RelationshipKind.ADJACENCY,
                        previous_cell,
                        cell_key,
                    )
                previous_cell = cell_key

                if vertical_merge == "restart":
                    for column in range(logical_column, logical_column + span):
                        active_vertical_merges[column] = cell_key
                else:
                    for column in range(logical_column, logical_column + span):
                        active_vertical_merges.pop(column, None)

                cell_properties = cell.find(f"{_W}tcPr")
                cell_borders = (
                    None
                    if cell_properties is None
                    else cell_properties.find(f"{_W}tcBorders")
                )
                visible_sides = _visible_border_sides(cell_borders)
                if all(side in visible_sides for side in _BORDER_SIDES):
                    rectangle_id = f"docx-rectangle-{collector.rectangle_count:04d}"
                    collector.rectangle_count += 1
                    collector.elements.append(
                        ObservedElement(
                            id=rectangle_id,
                            element_type=ElementType.RECTANGLE,
                        )
                    )
                    collector.relationship(
                        RelationshipKind.CONTAINMENT,
                        cell_key,
                        rectangle_id,
                    )
                else:
                    for _side in visible_sides:
                        line_id = f"docx-line-{collector.line_count:04d}"
                        collector.line_count += 1
                        collector.elements.append(
                            ObservedElement(
                                id=line_id,
                                element_type=ElementType.LINE,
                            )
                        )
                        collector.relationship(
                            RelationshipKind.CONTAINMENT,
                            cell_key,
                            line_id,
                        )

                text_start = len(collector.text_ids)
                self._observe_container(
                    cell,
                    parent_key=cell_key,
                    collector=collector,
                    relationship_targets=relationship_targets,
                    media_payloads=media_payloads,
                )
                if source_addressable:
                    for text_id in collector.text_ids[text_start:]:
                        collector.relationship(
                            RelationshipKind.CONTAINMENT,
                            cell_key,
                            text_id,
                        )
                logical_column += span

    @staticmethod
    def _observe_images(
        paragraph: ElementTree.Element,
        *,
        parent_key: str,
        collector: _ObservationCollector,
        relationship_targets: dict[str, str],
        media_payloads: dict[str, bytes],
    ) -> None:
        for blip in paragraph.iter(f"{_A}blip"):
            relationship_id = blip.get(f"{_R}embed")
            target = (
                None
                if relationship_id is None
                else relationship_targets.get(relationship_id)
            )
            payload = (
                None if target is None else media_payloads.get(_package_path(target))
            )
            if relationship_id is None or target is None:
                collector.error("inline image has no resolvable package relationship")
            elif payload is None:
                collector.error(
                    f"inline image relationship {relationship_id} has no media payload"
                )
            image_id = f"docx-image-{collector.image_count:04d}"
            collector.image_count += 1
            collector.elements.append(
                ObservedElement(
                    id=image_id,
                    element_type=ElementType.IMAGE,
                    content_sha256=(
                        None if payload is None else hashlib.sha256(payload).hexdigest()
                    ),
                )
            )
            collector.relationship(
                RelationshipKind.CONTAINMENT,
                parent_key,
                image_id,
            )

    def _failed(
        self,
        digest: str,
        *,
        package_readable: bool,
        error: str,
    ) -> DocxObservation:
        return DocxObservation(
            observer_name=self.observer_name,
            observer_version=self.observer_version,
            source_sha256=digest,
            package_readable=package_readable,
            python_docx_reopenable=False,
            errors=(error,),
        )


class FilesystemEvaluationWriter:
    """Publish deterministic UTF-8 evaluation.json through an atomic hard link."""

    def write(
        self,
        result: RestorationEvaluationResult,
        output_path: str | PathLike[str],
    ) -> Path:
        if not isinstance(result, RestorationEvaluationResult):
            raise TypeError("result must be a RestorationEvaluationResult")
        target = Path(output_path).resolve()
        if target.suffix.lower() != ".json":
            raise ValueError("output_path must use the .json extension")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise EvaluationWriteError(
                "output_exists",
                f"evaluation output already exists: {target}",
            )
        payload = (result.to_json(indent=2) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise EvaluationWriteError(
                    "output_exists",
                    f"evaluation output already exists: {target}",
                ) from exc
            except OSError as exc:
                raise EvaluationWriteError(
                    "publish_failed",
                    f"evaluation output could not be published: {target}",
                ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return target


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag == f"{_W}br":
            parts.append("\n")
    return "".join(parts)


def _source_tagged_texts(
    paragraph: ElementTree.Element,
) -> tuple[tuple[str, str], ...]:
    tagged: list[tuple[str, str]] = []
    for control in paragraph.iter(f"{_W}sdt"):
        properties = control.find(f"{_W}sdtPr")
        tag = None if properties is None else properties.find(f"{_W}tag")
        value = None if tag is None else tag.get(f"{_W}val")
        if value is None or not value.startswith(_SOURCE_TAG_PREFIX):
            continue
        source_element_id = value.removeprefix(_SOURCE_TAG_PREFIX)
        content = control.find(f"{_W}sdtContent")
        if not source_element_id or content is None:
            continue
        text = _paragraph_text(content).replace("\u200b", "")
        if text:
            tagged.append((source_element_id, text))
    return tuple(tagged)


def _source_table_key(table: ElementTree.Element) -> str | None:
    properties = table.find(f"{_W}tblPr")
    caption = None if properties is None else properties.find(f"{_W}tblCaption")
    value = None if caption is None else caption.get(f"{_W}val")
    if value is None or not value.startswith(_TABLE_CAPTION_PREFIX):
        return None
    table_id = value.removeprefix(_TABLE_CAPTION_PREFIX)
    return table_id or None


def _cell_grid_span(cell: ElementTree.Element) -> int:
    properties = cell.find(f"{_W}tcPr")
    grid_span = None if properties is None else properties.find(f"{_W}gridSpan")
    value = None if grid_span is None else grid_span.get(f"{_W}val")
    if value is None:
        return 1
    try:
        return max(1, int(value))
    except ValueError:
        return 1


def _cell_vertical_merge(cell: ElementTree.Element) -> str | None:
    properties = cell.find(f"{_W}tcPr")
    merge = None if properties is None else properties.find(f"{_W}vMerge")
    if merge is None:
        return None
    return merge.get(f"{_W}val", "continue")


def _visible_border_sides(
    borders: ElementTree.Element | None,
) -> tuple[str, ...]:
    if borders is None:
        return ()
    visible: list[str] = []
    for side in _BORDER_SIDES:
        border = borders.find(f"{_W}{side}")
        if border is None:
            continue
        value = border.get(f"{_W}val", "single")
        if value not in {"nil", "none"}:
            visible.append(side)
    return tuple(visible)


def _relationships(
    relationships_xml: bytes | None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    if relationships_xml is None:
        return {}, ()
    root = ElementTree.fromstring(relationships_xml)
    internal: dict[str, str] = {}
    external: list[str] = []
    for relationship in root.findall(f"{_REL}Relationship"):
        relationship_id = relationship.get("Id")
        target = relationship.get("Target")
        if relationship_id is None or target is None:
            continue
        if relationship.get("TargetMode") == "External":
            external.append(f"{relationship_id}:{target}")
        else:
            internal[relationship_id] = target
    return internal, tuple(sorted(external))


def _package_path(target: str) -> str:
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("word", target))
    if normalized == "word" or not normalized.startswith("word/"):
        return ""
    return normalized


__all__ = ["FilesystemEvaluationWriter", "PythonDocxObserver"]
