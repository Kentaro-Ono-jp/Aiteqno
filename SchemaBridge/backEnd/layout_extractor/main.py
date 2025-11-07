import json
import os
import glob
import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from analyze import analyze_image

# 読み込み対象
LAYOUT_FILE = os.path.join("output", "layout_a4_portrait.json")
PDF_OUTPUT = os.path.join("output", "layout_preview.pdf")

def draw_layout_on_pdf(layout_data, output_pdf, debug_image=None, include_debug_page=True):
    # A4サイズ（595x842 pt）
    c = canvas.Canvas(output_pdf, pagesize=A4)
    page_width, page_height = A4

    # ---- 画像座標（左上原点・y下向き＝OpenCV準拠）→ PDF座標（左下原点・y上向き）変換 ----
    # lines と boxes の最大範囲からスケール算出（内部線分だけに依存しない）
    def _max_xy_from_layout(ld):
        xs, ys = [], []
        for l in ld.get("lines", []):
            xs += [l["x1"], l["x2"]]; ys += [l["y1"], l["y2"]]
        for b in ld.get("boxes", []):
            xs += [b["x"], b["x"] + b["w"]]; ys += [b["y"], b["y"] + b["h"]]
        size_info = ld.get("size") or {}
        xs.append(size_info.get("w", 0))
        ys.append(size_info.get("h", 0))
        return (max(xs) if xs else 1, max(ys) if ys else 1)
    max_x, max_y = _max_xy_from_layout(layout_data)

    margin = 20.0
    fit_w = page_width - margin * 2
    fit_h = page_height - margin * 2
    scale = min(fit_w / max_x, fit_h / max_y)

    # 上寄せ（上20pt余白）
    ox = (page_width - (max_x * scale)) / 2.0
    oy = page_height - margin - (max_y * scale)

    # 画像座標(x, y_top) → PDF座標(xp, yp)
    def to_pdf_xy(x, y):
        return (ox + x * scale, page_height - (oy + y * scale))

    # --- analyze.py で前処理済みなら、そのまま線を描画 ---
    if layout_data.get("pre_filtered"):
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1.0)
        for l in layout_data.get("lines", []):
            x1, y1 = to_pdf_xy(l["x1"], l["y1"])
            x2, y2 = to_pdf_xy(l["x2"], l["y2"])
            c.line(x1, y1, x2, y2)
    else:
        # ---- 線分の前処理：スナップ → 近接区間マージ（interval union） ----
        tol_snap = 3.0         # 水平/垂直に丸める許容(px)
        tol_group = 3.0        # 同一直線とみなす距離(px)
        gap_tolerance = 6.0    # 連結とみなすすき間(px)

        # スナップ済み線分の収集
        snapped_v = {}  # x(グループ中心) -> [(y1,y2), ...]
        snapped_h = {}  # y(グループ中心) -> [(x1,x2), ...]
        def _group_key(val, groups, tol):
            for g in list(groups.keys()):
                if abs(val - g) <= tol:
                    return g
            groups[val] = []
            return val

        def _snap_one(l):
            x1, y1, x2, y2 = l["x1"], l["y1"], l["x2"], l["y2"]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            if dx <= tol_snap and dy > dx:  # 縦
                x = round((x1 + x2) / 2)
                g = _group_key(x, snapped_v, tol_group)
                snapped_v[g].append((min(y1, y2), max(y1, y2)))
            elif dy <= tol_snap and dx > dy:  # 横
                y = round((y1 + y2) / 2)
                g = _group_key(y, snapped_h, tol_group)
                snapped_h[g].append((min(x1, x2), max(x1, x2)))
            else:
                return  # 斜めは無視

        for l in layout_data.get("lines", []):
            _snap_one(l)

        # 区間マージ（同一直線上でソート→重なり/近接は結合）
        def _merge_intervals(intervals, gap_tol):
            if not intervals:
                return []
            ints = sorted(intervals, key=lambda t: t[0])
            merged = [list(ints[0])]
            for a, b in ints[1:]:
                last = merged[-1]
                if a <= last[1] + gap_tol:   # 重なり or 近接
                    last[1] = max(last[1], b)
                else:
                    merged.append([a, b])
            return [(i[0], i[1]) for i in merged]

        merged_v = {x: _merge_intervals(ys, gap_tolerance) for x, ys in snapped_v.items()}
        merged_h = {y: _merge_intervals(xs, gap_tolerance) for y, xs in snapped_h.items()}

        # しきい値（マージ後に適用）
        min_len_abs = 120.0                  # 絶対長(px)
        min_len_rel_v = max_y * 0.10         # 画像高さの10%
        min_len_rel_h = max_x * 0.08         # 画像幅の8%
        min_v = max(min_len_abs, min_len_rel_v)
        min_h = max(min_len_abs * 0.8, min_len_rel_h)

        # 描画：マージ後の線のみ
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1.0)

        # 縦線
        for x, segs in merged_v.items():
            for y1, y2 in segs:
                if (y2 - y1) < min_v:
                    continue
                x1p, y1p = to_pdf_xy(x, y1)
                x2p, y2p = to_pdf_xy(x, y2)
                c.line(x1p, y1p, x2p, y2p)

        # 横線
        for y, segs in merged_h.items():
            for x1, x2 in segs:
                if (x2 - x1) < min_h:
                    continue
                x1p, y1p = to_pdf_xy(x1, y)
                x2p, y2p = to_pdf_xy(x2, y)
                c.line(x1p, y1p, x2p, y2p)

    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.setFont("Helvetica", 8)
    c.drawString(20, 20, f"A4 layout template (scale={scale:.3f}, margin={margin}pt)")

    if include_debug_page:
        c.showPage()

        size_info = layout_data.get("size") or {}
        src_w = size_info.get("w", max_x)
        src_h = size_info.get("h", max_y)

        # 背景に原稿を配置（存在すれば）
        reader = None
        if debug_image and os.path.isfile(debug_image):
            try:
                reader = ImageReader(debug_image)
            except Exception:
                reader = None

        content_w = max_x * scale
        base_x = (page_width - content_w) / 2.0
        base_y = margin

        if reader is not None:
            c.drawImage(reader, base_x, base_y, width=src_w * scale, height=src_h * scale, preserveAspectRatio=True, mask="auto")

        # デバッグ用線（淡い色）
        c.saveState()
        c.setStrokeColorRGB(0.0, 0.5, 1.0)
        c.setLineWidth(0.8)
        c.setDash(4, 3)
        for l in layout_data.get("lines", []):
            x1, y1 = to_pdf_xy(l["x1"], l["y1"])
            x2, y2 = to_pdf_xy(l["x2"], l["y2"])
            c.line(x1, y1, x2, y2)
        c.restoreState()

        # デバッグ用ボックス（淡い赤）
        if layout_data.get("boxes"):
            c.saveState()
            c.setStrokeColorRGB(1.0, 0.3, 0.3)
            c.setLineWidth(0.7)
            c.setDash(2, 2)
            for b in layout_data.get("boxes", []):
                x1p, y1p = to_pdf_xy(b["x"], b["y"])
                x2p, y2p = to_pdf_xy(b["x"] + b["w"], b["y"] + b["h"])
                rx, ry = min(x1p, x2p), min(y1p, y2p)
                rw, rh = abs(x2p - x1p), abs(y2p - y1p)
                c.rect(rx, ry, rw, rh, stroke=1, fill=0)
            c.restoreState()

        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.setFont("Helvetica", 8)
        label = "Debug overlay"
        if debug_image and os.path.isfile(debug_image):
            label += f" / source: {os.path.basename(debug_image)}"
        c.drawString(20, 20, f"{label} (scale={scale:.3f})")

    c.save()

def main():
    AUTO_ANALYZE = True
    DEFAULT_IMAGE = "form_blank_testClinic_v1.png"  # 既定ファイル名

    # --- 固定探索先：SchemaBridge/layout_extractor/input のみ ---
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 親を辿って "SchemaBridge" ルートを特定
    cur = script_dir
    repo_root = None
    while True:
        head, tail = os.path.split(cur)
        if tail == "SchemaBridge":
            repo_root = cur
            break
        if head == cur:  # ルートに到達
            break
        cur = head

    if repo_root is None:
        raise FileNotFoundError(
            f'"SchemaBridge" ルートを辿れませんでした。起点: {script_dir}'
        )

    # 入力ディレクトリ：backEnd 配下を最優先、なければ従来パスをフォールバック
    candidates = [
        os.path.join(repo_root, "backEnd", "layout_extractor", "input"),
        os.path.join(repo_root, "layout_extractor", "input"),
    ]
    input_dir = next((d for d in candidates if os.path.isdir(d)), None)
    if input_dir is None:
        tried = "\n  - " + "\n  - ".join(candidates)
        raise FileNotFoundError(f"入力ディレクトリが見つかりません。探索候補:{tried}")
    print(f"📁 Using input_dir: {input_dir}")

    # 既定名があればそれを使う。なければ input 内の最新PNGを使う
    target_path = os.path.join(input_dir, DEFAULT_IMAGE)
    if not os.path.isfile(target_path):
        pngs = glob.glob(os.path.join(input_dir, "*.[Pp][Nn][Gg]"))
        if not pngs:
            raise FileNotFoundError(
                f"PNGが見つかりません: {input_dir}（期待ファイル: {DEFAULT_IMAGE}）"
            )
        target_path = max(pngs, key=os.path.getmtime)

    TARGET_IMAGE = target_path
    print(f"🔎 Using image: {TARGET_IMAGE}")

    # 画像解析 → レイアウト生成 → PDF出力
    layout_data = analyze_image(TARGET_IMAGE)

    out_dir = os.path.dirname(PDF_OUTPUT) or "."
    os.makedirs(out_dir, exist_ok=True)
    try:
        draw_layout_on_pdf(
            layout_data,
            PDF_OUTPUT,
            debug_image=TARGET_IMAGE,
            include_debug_page=True,
        )
        print(f"✅ PDFを出力しました → {PDF_OUTPUT}")
    except PermissionError:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = os.path.join(out_dir, f"layout_preview_{ts}.pdf")
        draw_layout_on_pdf(layout_data, alt)
        print(f"⚠️ 開きっぱなしのため別名で保存しました → {alt}")

if __name__ == "__main__":
    main()