from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_real_baseline_fixture


class BuildRealBaselineFixtureTest(unittest.TestCase):
    def test_publish_new_directory_makes_every_payload_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_directory = root / "fixture"
            payloads = {
                "source.png": b"png",
                "source.png.b64": b"cG5n\n",
                "reference.json": b"{}\n",
                "generation.json": b"{}\n",
            }

            build_real_baseline_fixture._publish_new_directory(
                output_directory, payloads
            )

            self.assertEqual(
                {path.name for path in output_directory.iterdir()}, set(payloads)
            )
            for filename, payload in payloads.items():
                self.assertEqual((output_directory / filename).read_bytes(), payload)
            self.assertEqual(list(root.glob(".fixture.*.tmp")), [])

    def test_existing_directory_is_unchanged_even_on_late_file_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_directory = root / "fixture"
            output_directory.mkdir()
            sentinel = output_directory / "source.png"
            sentinel.write_bytes(b"reviewed fixture")
            generation = output_directory / "generation.json"
            generation.write_bytes(b'{"reviewed": true}\n')
            before = {
                path.name: path.read_bytes() for path in output_directory.iterdir()
            }

            with self.assertRaises(FileExistsError):
                build_real_baseline_fixture._publish_new_directory(
                    output_directory,
                    {
                        "source.png": b"replacement",
                        "source.png.b64": b"replacement",
                        "reference.json": b"replacement",
                        "generation.json": b"late collision",
                    },
                )

            after = {
                path.name: path.read_bytes() for path in output_directory.iterdir()
            }
            self.assertEqual(after, before)
            self.assertEqual(list(root.glob(".fixture.*.tmp")), [])

    def test_staging_write_failure_leaves_no_output_or_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_directory = root / "fixture"
            original_write = build_real_baseline_fixture._write_new
            call_count = 0

            def fail_on_third_write(path: Path, payload: bytes) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 3:
                    raise OSError("simulated write failure")
                original_write(path, payload)

            with (
                patch.object(
                    build_real_baseline_fixture,
                    "_write_new",
                    side_effect=fail_on_third_write,
                ),
                self.assertRaisesRegex(OSError, "simulated write failure"),
            ):
                build_real_baseline_fixture._publish_new_directory(
                    output_directory,
                    {
                        "source.png": b"png",
                        "source.png.b64": b"cG5n\n",
                        "reference.json": b"{}\n",
                        "generation.json": b"{}\n",
                    },
                )

            self.assertFalse(output_directory.exists())
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
