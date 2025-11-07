import cv2
import json
import os
import numpy as np

def detect_layout(image_path, output_json):
    # 画像を読み込み（グレースケール変換）
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    # --- 前処理（適応二値化＆線マスク） ---
    from image_utils import binarize, extract_line_masks
    thresh = binarize(img)
    hor_mask, ver_mask = extract_line_masks(thresh, hor_len=60, ver_len=60)

    # --- マスクから輪郭→代表線に単純化 ---
    def contours_to_lines(mask, axis: str):
        segs = []
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            # 細かいノイズ除去（罫線のみ欲しい）
            if axis == "h" and w < 40:
                continue
            if axis == "v" and h < 40:
                continue
            if axis == "h":
                x1, y1 = x, y + h // 2
                x2, y2 = x + w, y + h // 2
            else:
                x1, y1 = x + w // 2, y
                x2, y2 = x + w // 2, y + h
            segs.append((int(x1), int(y1), int(x2), int(y2)))
        return segs

    h_lines = contours_to_lines(hor_mask, "h")
    v_lines = contours_to_lines(ver_mask, "v")

    # --- 近接/重なりをマージして1本化 ---
    def merge_collinear(segs, axis: str, gap=6, drift=2):
        # axis: 'h'ならyが近いものを、'v'ならxが近いものをマージ
        if not segs:
            return []
        key = (lambda s: (s[1], s[0], s[2])) if axis == "h" else (lambda s: (s[0], s[1], s[3]))
        segs = sorted(segs, key=key)
        merged = []
        for s in segs:
            if not merged:
                merged.append(list(s))
                continue
            a = merged[-1]
            if axis == "h":
                same_lane = abs(a[1] - s[1]) <= drift
                touching  = s[0] <= a[2] + gap and s[1] >= a[1] - drift and s[1] <= a[1] + drift
                if same_lane and touching:
                    a[0] = min(a[0], s[0]); a[2] = max(a[2], s[2]); a[1] = (a[1] + s[1]) // 2
                else:
                    merged.append(list(s))
            else:
                same_lane = abs(a[0] - s[0]) <= drift
                touching  = s[1] <= a[3] + gap and s[0] >= a[0] - drift and s[0] <= a[0] + drift
                if same_lane and touching:
                    a[1] = min(a[1], s[1]); a[3] = max(a[3], s[3]); a[0] = (a[0] + s[0]) // 2
                else:
                    merged.append(list(s))
        return [tuple(m) for m in merged]

    h_lines = merge_collinear(h_lines, "h", gap=10, drift=2)
    v_lines = merge_collinear(v_lines, "v", gap=10, drift=2)

    # 代表線のみを確定
    line_data = [{"x1": x1, "y1": y1, "x2": x2, "y2": y2} for (x1, y1, x2, y2) in (h_lines + v_lines)]

    # layout.json 出力（罫線のみ）
    layout = {
        "page": {"width": 595, "height": 842, "unit": "pt", "orientation": "portrait"},
        "lines": line_data
    }

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)

    print(f"✅ layout.json を出力しました → {output_json}")

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    detect_layout(
        os.path.join(here, "input", "form_blank_testClinic_v1.png"),
        os.path.join(here, "output", "layout_a4_portrait.json")
    )