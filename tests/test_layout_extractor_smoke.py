import importlib
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


LAYOUT_EXTRACTOR = (
    Path(__file__).resolve().parents[1]
    / "SchemaBridge"
    / "backEnd"
    / "layout_extractor"
)
sys.path.insert(0, str(LAYOUT_EXTRACTOR))

analyze_image = importlib.import_module("analyze").analyze_image
renderers = importlib.import_module("renderers")


class LayoutExtractorSmokeTest(unittest.TestCase):
    def test_image_analysis_and_preview_rendering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            source_path = work_dir / "source.png"
            png_path = work_dir / "preview.png"
            pdf_path = work_dir / "preview.pdf"

            source = Image.new("RGB", (320, 200), "white")
            draw = ImageDraw.Draw(source)
            draw.rectangle((20, 20, 300, 180), outline="black", width=2)
            draw.line((20, 80, 300, 80), fill="black", width=2)
            draw.line((130, 20, 130, 180), fill="black", width=2)
            source.save(source_path)

            with Image.open(source_path) as reopened:
                self.assertEqual(reopened.size, (320, 200))

            cv_image = cv2.imread(str(source_path), cv2.IMREAD_GRAYSCALE)
            self.assertIsInstance(cv_image, np.ndarray)
            self.assertEqual(cv_image.shape, (200, 320))

            layout = analyze_image(str(source_path))
            self.assertEqual(layout["size"], {"w": 320, "h": 200})
            self.assertIn("lines", layout)
            self.assertIn("boxes", layout)

            renderers.draw_layout_on_png(
                layout,
                str(png_path),
                debug_image=str(source_path),
            )
            renderers.draw_layout_on_pdf(
                layout,
                str(pdf_path),
                debug_image=str(source_path),
            )

            self.assertGreater(png_path.stat().st_size, 0)
            self.assertGreater(pdf_path.stat().st_size, 0)
            with Image.open(png_path) as preview:
                self.assertEqual(preview.size, (320, 200))


if __name__ == "__main__":
    unittest.main()
