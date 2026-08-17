"""Build Aiteqno's original dense Japanese baseline form.

The generated raster is project-owned synthetic test artwork.  It deliberately
resembles a generic administrative form in *complexity*, but its wording and
layout were authored for Aiteqno and are not derived from a third-party form.
The committed fixture is the base64 file; regeneration is an explicit review
operation because installed font bytes affect raster output.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import PIL
from PIL import Image, ImageDraw, ImageFont


WIDTH = 700
HEIGHT = 991
FIXTURE_ID = "synthetic-dense-japanese-form-v1"


@dataclass(frozen=True, slots=True)
class TextBlock:
    block_id: str
    text: str
    bbox: tuple[int, int, int, int]
    essential: bool = False


@dataclass(frozen=True, slots=True)
class Structure:
    structure_id: str
    kind: str
    bbox: tuple[int, int, int, int]
    essential: bool = False


class FormBuilder:
    def __init__(self, font_path: Path) -> None:
        self.image = Image.new("RGB", (WIDTH, HEIGHT), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self.fonts = {
            10: ImageFont.truetype(str(font_path), 10),
            11: ImageFont.truetype(str(font_path), 11),
            12: ImageFont.truetype(str(font_path), 12),
            13: ImageFont.truetype(str(font_path), 13),
            14: ImageFont.truetype(str(font_path), 14),
            16: ImageFont.truetype(str(font_path), 16),
            24: ImageFont.truetype(str(font_path), 24),
        }
        self.blocks: list[TextBlock] = []
        self.structures: list[Structure] = []

    def text(
        self,
        block_id: str,
        xy: tuple[int, int],
        value: str,
        *,
        size: int = 12,
        essential: bool = False,
        anchor: str = "la",
    ) -> None:
        font = self.fonts[size]
        self.draw.text(xy, value, fill="black", font=font, anchor=anchor)
        measured = self.draw.textbbox(xy, value, font=font, anchor=anchor)
        self.blocks.append(
            TextBlock(
                block_id=block_id,
                text=value,
                bbox=(measured[0], measured[1], measured[2], measured[3]),
                essential=essential,
            )
        )

    def line(
        self,
        structure_id: str,
        xy: tuple[int, int, int, int],
        *,
        width: int = 1,
        essential: bool = False,
    ) -> None:
        self.draw.line(xy, fill="black", width=width)
        x1, y1, x2, y2 = xy
        self.structures.append(
            Structure(
                structure_id=structure_id,
                kind="line",
                bbox=(min(x1, x2), min(y1, y2), max(x1, x2) + 1, max(y1, y2) + 1),
                essential=essential,
            )
        )

    def rectangle(
        self,
        structure_id: str,
        bbox: tuple[int, int, int, int],
        *,
        width: int = 1,
        essential: bool = False,
    ) -> None:
        self.draw.rectangle(bbox, outline="black", width=width)
        self.structures.append(
            Structure(
                structure_id=structure_id,
                kind="rectangle",
                bbox=bbox,
                essential=essential,
            )
        )

    def grid(
        self,
        prefix: str,
        bbox: tuple[int, int, int, int],
        *,
        rows: tuple[int, ...] = (),
        columns: tuple[int, ...] = (),
        essential: bool = False,
    ) -> None:
        self.rectangle(f"{prefix}-outer", bbox, essential=essential)
        left, top, right, bottom = bbox
        for index, y in enumerate(rows):
            self.line(
                f"{prefix}-row-{index:02d}",
                (left, y, right, y),
                essential=essential,
            )
        for index, x in enumerate(columns):
            self.line(
                f"{prefix}-column-{index:02d}",
                (x, top, x, bottom),
                essential=essential,
            )


def _bbox_dict(bbox: tuple[int, int, int, int]) -> dict[str, float]:
    left, top, right, bottom = bbox
    return {
        "x": round(left / WIDTH, 8),
        "y": round(top / HEIGHT, 8),
        "width": round((right - left) / WIDTH, 8),
        "height": round((bottom - top) / HEIGHT, 8),
    }


def _build(font_path: Path) -> FormBuilder:
    form = FormBuilder(font_path)
    form.rectangle("page-frame", (1, 1, WIDTH - 2, HEIGHT - 2), width=1)

    form.text("document-number", (42, 55), "管理番号 ＿＿＿＿＿＿＿＿", size=11)
    form.text(
        "created-date", (655, 55), "作成日：     年   月   日", size=11, anchor="ra"
    )
    form.line("header-left-rule", (42, 75, 220, 75))
    form.line("header-right-rule", (480, 75, 658, 75))
    form.text(
        "title",
        (350, 90),
        "文 書 解 析  評 価 シ ー ト",
        size=24,
        essential=True,
        anchor="ma",
    )

    form.grid(
        "identity-grid",
        (42, 124, 658, 246),
        rows=(154, 192, 220),
        columns=(112, 424),
        essential=True,
    )
    form.line("identity-short-column", (498, 124, 498, 192), essential=True)
    form.text("furigana-label", (52, 132), "ふりがな", size=11)
    form.text("category-label", (436, 132), "区 分", size=11)
    form.text("category-options", (510, 132), "個 人 ・ 法 人", size=11)
    form.text("name-label", (58, 166), "氏 名", size=14, essential=True)
    form.text("identifier-label", (436, 166), "識別番号", size=11)
    form.text("address-label", (58, 199), "住 所", size=13, essential=True)
    form.text("phone-label", (52, 226), "電 話", size=11, essential=True)
    form.text("mail-label", (436, 226), "メール", size=11)

    form.text(
        "section-1-title",
        (47, 270),
        "1．依頼する処理を選択してください",
        size=16,
        essential=True,
    )
    form.grid(
        "request-grid",
        (42, 292, 658, 500),
        rows=(322, 352, 382, 412, 442, 472),
        columns=(150,),
        essential=True,
    )
    request_rows = (
        (
            "request-format",
            "対象形式",
            "PNG ・ PDF ・ DOCX ・ その他（                    ）",
        ),
        ("request-language", "主な言語", "日本語 ・ 英語 ・ 数字中心 ・ 混在"),
        ("request-purpose", "利用目的", "検索 ・ 転記 ・ 比較 ・ 保存 ・ データ連携"),
        ("request-priority", "優先事項", "文字精度 ・ 構造 ・ 配置 ・ 処理速度"),
        ("request-output", "希望出力", "JSON ・ DOCX ・ 画像 ・ 確認用レポート"),
        ("request-retention", "保存期間", "即時削除 ・ 30日 ・ 90日 ・ 指定なし"),
        (
            "request-notes",
            "補足事項",
            "罫線や小さな文字を含む場合は、ここに記載してください。",
        ),
    )
    for index, (row_id, label, value) in enumerate(request_rows):
        y = 300 + index * 30
        form.text(f"{row_id}-label", (54, y), label, size=11, essential=index < 2)
        form.text(row_id, (162, y), value, size=11, essential=index < 4)

    form.text(
        "section-2-title",
        (47, 526),
        "2．原稿に含まれる要素を確認してください",
        size=16,
        essential=True,
    )
    form.grid(
        "content-grid",
        (42, 548, 658, 658),
        rows=(576, 604, 632),
        columns=(128,),
        essential=True,
    )
    content_rows = (
        (
            "content-visual",
            "図  形",
            "□ 写真   □ 図解   □ 印影   □ バーコード   □ なし",
        ),
        (
            "content-structure",
            "構  造",
            "□ 表   □ 箇条書き   □ 段組み   □ 入力欄   □ なし",
        ),
        (
            "content-writing",
            "筆  記",
            "□ 活字   □ 手書き   □ 修正跡   □ かすれ   □ 傾き",
        ),
        (
            "content-security",
            "機密性",
            "□ 公開   □ 社内   □ 要配慮   □ 個人情報を含まない",
        ),
    )
    for index, (row_id, label, value) in enumerate(content_rows):
        y = 555 + index * 28
        form.text(f"{row_id}-label", (54, y), label, size=11)
        form.text(row_id, (140, y), value, size=11, essential=index < 2)

    form.text(
        "section-3-title",
        (47, 684),
        "3．出力後の確認方法を指定してください",
        size=16,
        essential=True,
    )
    form.grid(
        "review-grid",
        (42, 706, 658, 786),
        rows=(746,),
        columns=(190, 414),
        essential=True,
    )
    form.text("review-machine-label", (54, 717), "自動判定", size=11)
    form.text(
        "review-machine",
        (202, 717),
        "□ 必須文字   □ 構造   □ ページ数",
        size=11,
        essential=True,
    )
    form.text("review-machine-note", (426, 717), "閾値：70％以上", size=11)
    form.text("review-human-label", (54, 757), "目視確認", size=11)
    form.text(
        "review-human",
        (202, 757),
        "□ 重なり   □ 欠け   □ 読みやすさ",
        size=11,
        essential=True,
    )
    form.text("review-human-note", (426, 757), "判定：合格・保留・不合格", size=11)

    form.text(
        "section-4-title",
        (47, 812),
        "4．次の注意事項を確認してください",
        size=16,
        essential=True,
    )
    form.grid(
        "confirmation-grid",
        (42, 834, 658, 920),
        rows=(863, 892),
        essential=True,
    )
    confirmations = (
        (
            "confirm-approximation",
            "□ 復元結果は原稿の完全な複製ではなく、読みやすい近似である。",
        ),
        (
            "confirm-editable",
            "□ 最終成果物は編集可能なDOCXとして開けることを確認する。",
        ),
        ("confirm-evidence", "□ 判定に使用したJSON、PDF、画像、環境情報を保存する。"),
    )
    for index, (row_id, value) in enumerate(confirmations):
        form.text(row_id, (54, 841 + index * 29), value, size=11, essential=True)

    form.text("footer-signature", (43, 944), "確認者署名：", size=11)
    form.line("footer-signature-rule", (120, 960, 360, 960), essential=True)
    form.text(
        "footer-result",
        (650, 944),
        "最終判定： 合格 ・ 保留 ・ 不合格",
        size=11,
        anchor="ra",
    )
    return form


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def _publish_new_directory(
    output_directory: Path, payloads: Mapping[str, bytes]
) -> None:
    """Publish a complete fixture without exposing a partially written result.

    The destination directory is deliberately create-only.  All files are first
    written into a sibling staging directory, then the completed directory is
    renamed into place.  Refusing even an empty destination also makes retries
    explicit and prevents a fixture build from modifying reviewed evidence.
    """

    if os.path.lexists(output_directory):
        raise FileExistsError(
            f"output directory already exists; refusing to modify it: "
            f"{output_directory}"
        )

    output_directory.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(output_directory):
        raise FileExistsError(
            f"output directory already exists; refusing to modify it: "
            f"{output_directory}"
        )

    staging_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.",
            suffix=".tmp",
            dir=output_directory.parent,
        )
    )
    published = False
    try:
        for filename, payload in payloads.items():
            if Path(filename).name != filename:
                raise ValueError(f"fixture filename must be a basename: {filename!r}")
            _write_new(staging_directory / filename, payload)

        # Check again after the potentially slow raster writes.  rename() is the
        # sole publication operation and is atomic while staying on one volume.
        if os.path.lexists(output_directory):
            raise FileExistsError(
                f"output directory already exists; refusing to modify it: "
                f"{output_directory}"
            )
        staging_directory.rename(output_directory)
        published = True
    finally:
        if not published and os.path.lexists(staging_directory):
            shutil.rmtree(staging_directory)


def _fixture_reference(form: FormBuilder, source_sha256: str) -> dict[str, object]:
    return {
        "reference_version": 1,
        "reference_id": FIXTURE_ID,
        "source_sha256": source_sha256,
        "source_dimensions": {
            "sha256": source_sha256,
            "pixel_width": WIDTH,
            "pixel_height": HEIGHT,
        },
        "reviewed": False,
        "review": {
            "status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "revision": 1,
        },
        "normalization": "NFKC then remove every Unicode whitespace character",
        "text_regions": [
            {
                "id": block.block_id,
                "text": block.text,
                "bbox": _bbox_dict(block.bbox),
                "essential": block.essential,
            }
            for block in form.blocks
        ],
        "relationships": [
            {
                "kind": kind,
                "source": source,
                "target": target,
                "essential": True,
            }
            for kind, source, target in (
                ("reading_order", "title", "section-1-title"),
                ("reading_order", "section-1-title", "section-2-title"),
                ("reading_order", "section-2-title", "section-3-title"),
                ("reading_order", "section-3-title", "section-4-title"),
                ("containment", "identity-grid-outer", "name-label"),
                ("containment", "request-grid-outer", "request-format"),
                ("containment", "content-grid-outer", "content-structure"),
                ("containment", "review-grid-outer", "review-machine"),
                ("containment", "confirmation-grid-outer", "confirm-editable"),
                ("adjacency", "request-format-label", "request-format"),
                ("adjacency", "request-language-label", "request-language"),
                ("adjacency", "content-visual-label", "content-visual"),
                ("adjacency", "review-machine-label", "review-machine"),
            )
        ],
        "structural_items": [
            {
                "id": item.structure_id,
                "type": item.kind,
                "bbox": _bbox_dict(item.bbox),
                "essential": item.essential,
            }
            for item in form.structures
        ],
        "essential_text_anchors": [
            "文書解析評価シート",
            "氏名",
            "住所",
            "電話",
            "依頼する処理を選択してください",
            "対象形式",
            "主な言語",
            "原稿に含まれる要素を確認してください",
            "出力後の確認方法を指定してください",
            "注意事項を確認してください",
            "編集可能なDOCX",
            "環境情報を保存する",
        ],
        "expected_page_count": 1,
        "required_manual_checks": [
            "no_fatal_text_overlap",
            "no_text_clipping",
            "layout_human_usable",
            "word_open_edit_save",
        ],
    }


def _select_font(explicit: Path | None) -> Path:
    candidates = (
        explicit,
        Path("C:/Windows/Fonts/YuGothR.ttc"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf"),
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("no supported Japanese fixture font was found")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--font", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    output_directory = arguments.output_directory.resolve(strict=False)
    font_path = _select_font(arguments.font)
    form = _build(font_path)

    from io import BytesIO

    buffer = BytesIO()
    form.image.save(
        buffer, format="PNG", compress_level=9, optimize=False, dpi=(96, 96)
    )
    png_data = buffer.getvalue()
    source_sha256 = hashlib.sha256(png_data).hexdigest()
    b64_data = base64.b64encode(png_data) + b"\n"
    reference_data = (
        json.dumps(
            _fixture_reference(form, source_sha256),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    generation_data = (
        json.dumps(
            {
                "fixture_id": FIXTURE_ID,
                "authorship": "Original synthetic test artwork authored for Aiteqno.",
                "license": "MIT",
                "contains_personal_data": False,
                "font": {
                    "path_at_generation": str(font_path),
                    "sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
                },
                "pillow_version": PIL.__version__,
                "python": platform.python_version(),
                "source_sha256": source_sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    _publish_new_directory(
        output_directory,
        {
            "source.png": png_data,
            "source.png.b64": b64_data,
            "reference.json": reference_data,
            "generation.json": generation_data,
        },
    )
    print(output_directory / "source.png")
    print(f"sha256={source_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
