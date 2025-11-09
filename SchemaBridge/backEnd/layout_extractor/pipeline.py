import os
import datetime
from reportlab.lib.pagesizes import A4
from typing import Optional, Tuple
"""
layout.json/schema.jsonの入出力について（将来の設計方針）
いまは pipeline.run_pipeline() が analyze_image()（analyze）の返すディクショナリをそのまま draw_*() に渡している。
将来はここで
layout_data, schema_data = detect_and_ocr(image)
save_layout_json(layout_data, path) / save_schema_json(schema_data, path)
loaded = load_layout_json(path) / load_schema_json(path)
draw_* (loaded, ...)
に差し替えるだけで良いように設計してある。I/Oと描画は既に分離済み。
"""
# ★ 相対 → 同階層の絶対インポートに統一
from io_paths import (
    find_repo_root, resolve_input_dir, choose_target_image,
    DEFAULT_PDF_OUTPUT
)
from renderers import draw_layout_on_pdf, draw_layout_on_png

# 既存の画像→レイアウト抽出（同階層）
from analyze import analyze_image  # 将来: layout.jsonとschema.json保存へ差し替え可

def run_pipeline(image_path: Optional[str] = None,
                 save_pdf: bool = True,
                 save_png: bool = True,
                 page_size=None) -> Tuple[str, Optional[str], Optional[str]]:
    """
    入力画像サイズをそのままページサイズに採用して可視化する（縮小・余白なし）。
    Returns: (target_image, pdf_path or None, png_path or None)
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = find_repo_root(script_dir)
    input_dir = resolve_input_dir(script_dir, repo_root)

    target_image = image_path or choose_target_image(input_dir)
    layout_data = analyze_image(target_image)

    # 出力は Aiteqno 直下へ（= SchemaBridge の親ディレクトリ）
    parent_root = os.path.dirname(repo_root)  # C:\work\Aiteqno
    base_root = parent_root
    pdf_path = os.path.join(base_root, "output", "layout_preview.pdf") if save_pdf else None
    png_path = None

    # 新設：個別フラグ（明示的に使う）
    layout_data.setdefault("debug_overlay_lines", True)
    layout_data.setdefault("debug_overlay_boxes", True)

    # layout_data を描画（page_size=None で入力画像寸法が使われる / margin=0 は関数既定値）
    if save_pdf:
        try:
            draw_layout_on_pdf(layout_data, pdf_path, debug_image=target_image, page_size=page_size)
        except PermissionError:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_path = os.path.join(os.path.dirname(pdf_path), f"layout_preview_{ts}.pdf")
            draw_layout_on_pdf(layout_data, pdf_path, debug_image=target_image, page_size=page_size)

    if save_png:
        png_path = (os.path.splitext(pdf_path)[0] + ".png") if pdf_path else os.path.join(base_root, "output", "layout_preview.png")
        try:
            draw_layout_on_png(layout_data, png_path, debug_image=target_image, page_size=page_size)
        except PermissionError:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = os.path.join(os.path.dirname(png_path), f"layout_preview_{ts}.png")
            draw_layout_on_png(layout_data, png_path, debug_image=target_image, page_size=page_size)

    return target_image, pdf_path, png_path