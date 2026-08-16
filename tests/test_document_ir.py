import ast
import copy
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from aiteqno.adapters.json_schema import (
    document_ir_from_data,
    document_ir_from_file,
    document_ir_from_json,
    document_ir_schema_path,
    load_document_ir_schema,
    validate_document_ir,
    validate_document_ir_data,
)
from aiteqno.domain import (
    DocumentIR,
    DocumentIRValidationError,
    ImageElement,
    LineElement,
    RectangleElement,
    TextElement,
)


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "document_ir"
CANONICAL_FIXTURE = FIXTURE_DIRECTORY / "canonical.document.ir.json"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))


class DocumentIRSchemaTest(unittest.TestCase):
    def test_schema_is_canonical_draft_2020_12(self):
        schema = load_document_ir_schema()

        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(document_ir_schema_path().resolve().parent.name, "schemas")
        Draft202012Validator.check_schema(schema)

    def test_canonical_fixture_matches_formal_schema(self):
        validate_document_ir_data(load_fixture("canonical.document.ir.json"))

    def test_unsupported_version_has_actionable_error(self):
        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_file(
                FIXTURE_DIRECTORY / "invalid-version.document.ir.json"
            )

        self.assertEqual(raised.exception.issues[0].path, "$.ir_version")
        self.assertEqual(raised.exception.issues[0].code, "unsupported_version")
        self.assertIn("0.1.0", str(raised.exception))

    def test_unsafe_asset_path_has_actionable_error(self):
        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_file(FIXTURE_DIRECTORY / "invalid-asset.document.ir.json")

        matching = [
            issue for issue in raised.exception.issues if issue.path == "$.assets[0].path"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].code, "invalid_asset_path")
        self.assertIn("bundle-relative", matching[0].message)

    def test_unknown_fields_are_rejected(self):
        data = load_fixture("canonical.document.ir.json")
        data["pages"][0]["elements"][0]["debug_overlay"] = True

        with self.assertRaises(DocumentIRValidationError) as raised:
            validate_document_ir_data(data)

        self.assertIn("debug_overlay", str(raised.exception))


class DocumentIRModelTest(unittest.TestCase):
    def test_canonical_fixture_loads_all_v1_element_types(self):
        document = document_ir_from_file(CANONICAL_FIXTURE)

        self.assertIsInstance(document, DocumentIR)
        self.assertEqual(document.ir_version, "0.1.0")
        self.assertEqual(len(document.pages), 1)
        self.assertEqual(
            tuple(type(element) for element in document.pages[0].elements),
            (TextElement, LineElement, RectangleElement, ImageElement),
        )

    def test_python_json_python_round_trip_preserves_meaning(self):
        original = document_ir_from_file(CANONICAL_FIXTURE)

        restored = document_ir_from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertEqual(restored.to_dict(), original.to_dict())
        validate_document_ir(restored)

    def test_out_of_page_geometry_passes_schema_but_fails_semantics(self):
        data = load_fixture("invalid-coordinate.document.ir.json")
        validate_document_ir_data(data)

        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_data(data)

        self.assertEqual(
            raised.exception.issues[0].path,
            "$.pages[0].elements[0].bbox",
        )
        self.assertEqual(
            raised.exception.issues[0].code,
            "out_of_page_geometry",
        )
        self.assertIn("page bounds", str(raised.exception))

    def test_image_must_reference_registered_asset(self):
        data = load_fixture("canonical.document.ir.json")
        data["pages"][0]["elements"][3]["asset_id"] = "asset-missing"
        validate_document_ir_data(data)

        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_data(data)

        self.assertIn("unknown_asset", {issue.code for issue in raised.exception.issues})
        self.assertIn("asset-missing", str(raised.exception))

    def test_asset_path_digest_must_match_declared_digest(self):
        data = load_fixture("canonical.document.ir.json")
        data["assets"][0]["sha256"] = "1" * 64
        validate_document_ir_data(data)

        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_data(data)

        self.assertIn("digest must match", str(raised.exception))

    def test_duplicate_ids_are_rejected(self):
        data = load_fixture("canonical.document.ir.json")
        data["pages"][0]["elements"][1]["id"] = data["pages"][0]["elements"][0][
            "id"
        ]
        validate_document_ir_data(data)

        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_data(data)

        self.assertIn("duplicate_id", {issue.code for issue in raised.exception.issues})

    def test_missing_version_is_not_guessed(self):
        data = load_fixture("canonical.document.ir.json")
        del data["ir_version"]

        with self.assertRaises(DocumentIRValidationError) as raised:
            document_ir_from_data(data)

        self.assertIn("ir_version", str(raised.exception))

    def test_non_empty_unicode_identifier_is_supported(self):
        data = load_fixture("canonical.document.ir.json")
        data["document_id"] = "問診票-001"

        document = document_ir_from_data(data)

        self.assertEqual(document.document_id, "問診票-001")

    def test_json_model_is_immutable_from_caller_mutation(self):
        data = load_fixture("canonical.document.ir.json")
        document = document_ir_from_data(data)

        data["metadata"]["title"] = "mutated"
        data["extensions"]["jp.reactorfront.aiteqno.fixture"]["reviewed"] = False

        self.assertEqual(
            document.metadata["title"], "Canonical Document IR v0.1 fixture"
        )
        self.assertTrue(
            document.extensions["jp.reactorfront.aiteqno.fixture"]["reviewed"]
        )

    def test_direct_codec_rejects_unknown_fields_with_path(self):
        data = copy.deepcopy(load_fixture("canonical.document.ir.json"))
        data["generator"]["build_path"] = "C:/unsafe"

        with self.assertRaises(DocumentIRValidationError) as raised:
            DocumentIR.from_dict(data)

        self.assertEqual(raised.exception.issues[0].path, "$.generator")
        self.assertIn("build_path", str(raised.exception))


class DomainBoundaryTest(unittest.TestCase):
    def test_domain_does_not_import_external_or_legacy_implementation(self):
        domain_directory = Path(__file__).resolve().parents[1] / "src" / "aiteqno" / "domain"
        imports: set[str] = set()

        for source_path in domain_directory.glob("*.py"):
            syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(syntax_tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])

        non_standard_library_imports = imports - sys.stdlib_module_names
        self.assertEqual(non_standard_library_imports, set(), imports)


if __name__ == "__main__":
    unittest.main()
