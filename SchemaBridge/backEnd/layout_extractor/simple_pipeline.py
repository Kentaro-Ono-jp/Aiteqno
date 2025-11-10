# simple_pipeline.py
import os
from typing import Optional, Tuple

from io_paths import (
    find_repo_root,
    resolve_input_dir,
    choose_target_image,
    ensure_output_dir
)
from analyze import analyze_image
from renderers import draw_layout_on_pdf, draw_layout_on_png
from json_io import save_layout_json, save_schema_layout_json  # 罫線出力 / 文字領域出力

def _resolve_page_size(hint_image: Optional[str]) -> Optional[Tuple[float, float]]:
    # ページサイズ推定は必要に応じて拡張。現状は None のまま委譲でOK。
    return None

def run(
    image_path: Optional[str] = None,
    save_pdf: bool = True,
    save_png: bool = True,
    save_layout: bool = True,
    output_dir: Optional[str] = None,
    page_size: Optional[Tuple[float, float]] = None
):
    """
    単一画像に対して解析 → プレビュー（pdf/png） → JSON出力（layout/schema_layout）まで行う。
    """
    # 入力画像の決定（resolve_input_dir は script_dir と repo_root を要求）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = find_repo_root(script_dir, anchor="SchemaBridge")
    input_dir = resolve_input_dir(script_dir, repo_root)
    target_image = image_path or choose_target_image(input_dir)

    # ページサイズ（必要なら将来ヒント画像から決定）
    if page_size is None:
        page_size = _resolve_page_size(target_image)

    # 解析
    analyzed = analyze_image(target_image)

    pdf_path = None
    png_path = None
    layout_json_path = None
    schema_layout_json_path = None

    # 正規化済み layout.json を唯一の描画ソースとして再読込
    import json
    if layout_json_path and os.path.isfile(layout_json_path):
        with open(layout_json_path, "r", encoding="utf-8") as f:
            layout_data = json.load(f)
    else:
        layout_data = {"size": analyzed.get("size"), "lines": analyzed.get("lines", []), "boxes": analyzed.get("boxes", [])}

    # ★ ここで schema_layout.json を読み込み、boxes をマージ（赤枠の復活）
    if schema_layout_json_path and os.path.isfile(schema_layout_json_path):
        try:
            with open(schema_layout_json_path, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
            if isinstance(schema_data, dict) and "boxes" in schema_data:
                layout_data["boxes"] = schema_data.get("boxes") or []
        except Exception:
            # 読み込み失敗時はスルー（boxes未設定でも描画は継続）
            pass

    # プレビュー生成（描画は常に正規化済みlayout.jsonベース）
    if save_pdf:
        
        from viewport import Viewport
        try:
            pdf_path = os.path.join(ensure_output_dir(output_dir), "layout_preview.pdf")
            draw_layout_on_pdf(layout_data, pdf_path, debug_image=target_image, page_size=page_size)
        except Exception:
            pdf_path = None  # PDF依存不在/失敗でも継続

    if save_png:
        png_path = os.path.join(ensure_output_dir(output_dir), "layout_preview.png")
        try:
            draw_layout_on_png(layout_data, png_path, debug_image=target_image, page_size=page_size)
        except PermissionError:
            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = os.path.join(os.path.dirname(png_path), f"layout_preview_{ts}.png")
            os.makedirs(os.path.dirname(png_path), exist_ok=True)
            draw_layout_on_png(layout_data, png_path, debug_image=target_image, page_size=page_size)

    # JSON 出力（罫線のみ／文字領域のみ）
    if save_layout:
        layout_json_path = save_layout_json(layout_data, output_dir=output_dir, filename="layout.json")
        schema_layout_json_path = save_schema_layout_json(layout_data, output_dir=output_dir, filename="schema_layout.json")

    return {
        "image": target_image,
        "pdf": pdf_path,
        "png": png_path,
        "layout_json": layout_json_path,
        "schema_layout_json": schema_layout_json_path
    }