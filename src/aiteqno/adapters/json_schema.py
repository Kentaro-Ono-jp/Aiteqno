"""Draft 2020-12 validation adapter for the formal Document IR schema."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from aiteqno.domain import DocumentIR, DocumentIRValidationError, ValidationIssue
from aiteqno.ports.extraction import DocumentIRSchemaError


SCHEMA_FILENAME = "document-ir-v0.1.schema.json"
_INSTALLED_SCHEMA_SUFFIX = f"share/aiteqno/schemas/{SCHEMA_FILENAME}"


@lru_cache(maxsize=1)
def document_ir_schema_path() -> Path:
    """Locate the canonical schema in a source checkout or installed wheel."""

    source_checkout = Path(__file__).resolve().parents[3] / "schemas" / SCHEMA_FILENAME
    if source_checkout.is_file():
        return source_checkout

    for package_path in metadata.files("aiteqno") or ():
        normalized = str(package_path).replace("\\", "/")
        if normalized.endswith(_INSTALLED_SCHEMA_SUFFIX):
            located = Path(package_path.locate()).resolve()
            if located.is_file():
                return located

    raise FileNotFoundError(
        f"{SCHEMA_FILENAME} is missing from both the source checkout and installed distribution"
    )


@lru_cache(maxsize=1)
def load_document_ir_schema() -> dict[str, Any]:
    """Load and meta-validate the canonical Draft 2020-12 schema."""

    with document_ir_schema_path().open("r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_document_ir_schema())


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and part.isidentifier():
            path += f".{part}"
        else:
            path += f"[{json.dumps(part, ensure_ascii=False)}]"
    return path


def _issue_from_schema_error(error: ValidationError) -> ValidationIssue:
    path = _json_path(error.absolute_path)
    message = error.message
    code = f"schema_{error.validator}"

    if tuple(error.absolute_path) == ("ir_version",):
        message = "unsupported IR version; expected '0.1.0'"
        code = "unsupported_version"
    elif error.validator == "pattern" and tuple(error.absolute_path)[-1:] == ("path",):
        message = (
            "asset path must be bundle-relative and content-addressed as "
            "assets/sha256-<digest>.<png|jpg|jpeg>"
        )
        code = "invalid_asset_path"

    return ValidationIssue(path=path, message=message, code=code)


def validate_document_ir_data(data: Any) -> None:
    """Validate plain data against the formal schema with actionable paths."""

    errors = sorted(
        _validator().iter_errors(data),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        raise DocumentIRValidationError(
            _issue_from_schema_error(error) for error in errors
        )


def document_ir_from_data(data: Mapping[str, Any]) -> DocumentIR:
    """Schema-validate plain data, then build the semantic domain model."""

    validate_document_ir_data(data)
    return DocumentIR.from_dict(data)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is forbidden")


def document_ir_from_json(text: str | bytes | bytearray) -> DocumentIR:
    """Load UTF-8 JSON, validate its schema, and build a domain model."""

    try:
        data = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            message = f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        else:
            message = str(exc)
        raise DocumentIRValidationError.single("$", message, "invalid_json") from exc

    if not isinstance(data, Mapping):
        raise DocumentIRValidationError.single(
            "$", "Document IR root must be an object", "invalid_type"
        )
    return document_ir_from_data(data)


def document_ir_from_file(path: str | Path) -> DocumentIR:
    """Read and validate a UTF-8 Document IR JSON file."""

    return document_ir_from_json(Path(path).read_bytes())


def validate_document_ir(document: DocumentIR) -> None:
    """Validate an existing semantic model against the formal JSON Schema."""

    validate_document_ir_data(document.to_dict())


class JsonSchemaDocumentIRValidator:
    """Injected application boundary for the canonical Draft 2020-12 schema."""

    def validate(self, document: DocumentIR) -> None:
        """Validate a model while separating schema-runtime failures from bad IR."""

        if not isinstance(document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        try:
            validate_document_ir(document)
        except DocumentIRValidationError:
            raise
        except (FileNotFoundError, json.JSONDecodeError, SchemaError) as exc:
            raise DocumentIRSchemaError(
                "document_ir_schema_unavailable",
                f"canonical Document IR schema could not be loaded: {exc}",
            ) from exc


__all__ = [
    "JsonSchemaDocumentIRValidator",
    "SchemaError",
    "document_ir_from_data",
    "document_ir_from_file",
    "document_ir_from_json",
    "document_ir_schema_path",
    "load_document_ir_schema",
    "validate_document_ir",
    "validate_document_ir_data",
]
