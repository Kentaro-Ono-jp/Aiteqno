import cv2
import numpy as np
from image_utils import binarize, extract_line_masks

def _deskew(gray):
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi/180, 200)
    if lines is None: 
        return gray, 0.0
    angles = []
    for rho, theta in lines[:,0,:]:
        ang = (theta - np.pi/2.0) * 180/np.pi
        if -10 <= ang <= 10:  # ほぼ水平のみ評価
            angles.append(ang)
    if not angles: 
        return gray, 0.0
    angle = np.median(angles)
    h, w = gray.shape[:2]
    M = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
    desk = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    return desk, angle

def _extract_short_verticals(gray):
    """
    短い縦線（~20–80px）専用の救済検出。
    既存の“長い罫線”検出で落ちる短線を Otsu→Canny→HoughLinesP で拾う。
    """
    th = binarize(gray)
    edges = cv2.Canny(th, 50, 150, apertureSize=3)
    H, W = th.shape[:2]

    # 低しきい値 + 短線向け最小長
    min_len = max(18, int(0.012 * H))   # 画像高さ1480なら ≈18px
    max_len = int(0.20 * H)             # 過度な長線はここでは扱わない
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=30,                   # 検出感度を高める
        minLineLength=min_len,
        maxLineGap=3
    )

    segs = []
    if lines is None:
        return segs

    for x1, y1, x2, y2 in lines[:, 0, :]:
        # ほぼ縦のみ通す（水平成分が大きいものは除外）
        if abs(x2 - x1) > abs(y2 - y1) * 0.25:
            continue
        length = max(abs(y2 - y1), abs(x2 - x1))
        if length < min_len or length > max_len:
            continue
        segs.append({"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)})
    return segs

def _lines_to_segments(mask, orientation="h"):
    skel = cv2.ximgproc.thinning(mask) if hasattr(cv2, "ximgproc") else mask

    H, W = mask.shape[:2]
    if orientation == "h":
        min_len = max(120, int(0.18 * W))   # 太い横罫に寄せてさらに長さで絞る
        angle_ok = lambda x1,y1,x2,y2: abs(y2 - y1) <= max(1, int(0.07 * abs(x2 - x1)))
        hough_threshold, max_gap = 140, 6
    else:
        min_len = max(100, int(0.12 * H))   # 縦も「長線のみ」
        angle_ok = lambda x1,y1,x2,y2: abs(x2 - x1) <= max(1, int(0.07 * abs(y2 - y1)))
        hough_threshold, max_gap = 140, 6

    lines = cv2.HoughLinesP(skel, 1, np.pi/180, threshold=hough_threshold,
                            minLineLength=min_len, maxLineGap=max_gap)
    segs = []
    if lines is not None:
        for x1,y1,x2,y2 in lines[:,0,:]:
            if not angle_ok(x1,y1,x2,y2):
                continue
            segs.append({"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)})
    return segs

def _extract_text_boxes(binimg, line_mask):
    """
    文字だけを対象に安定抽出：
      1) line_mask を除去してテキスト専用マスクを作成
      2) 軽く膨張して単語/行を連結
      3) 輪郭抽出→面積/高さ/縦横比でフィルタ
    """
    text_only = cv2.bitwise_and(binimg, cv2.bitwise_not(line_mask))

    # 連結（語→行の順で少しだけ繋げる：横7×1 → 縦1×3）
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))
    x = cv2.dilate(text_only, h_kernel, iterations=1)   # 横方向を優先して結合
    text_dil = cv2.dilate(x, v_kernel, iterations=1)    # 行内の上下を軽く結合

    contours, _ = cv2.findContours(text_dil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = binimg.shape[:2]
    boxes = []
    for cnt in contours:
        x, y, ww, hh = cv2.boundingRect(cnt)
        area = ww * hh
        # 面積/高さ/縦横比で安定化（帳票の見出し～本文帯を想定）
        if area < 80 or area > (w * h * 0.25):
            continue
        if hh < 10 or hh > 120:
            continue
        ratio = ww / max(hh, 1)
        if ratio < 0.5 or ratio > 15:
            continue
        boxes.append({"x": int(x), "y": int(y), "w": int(ww), "h": int(hh)})
    return boxes

def analyze_image(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(image_path)
    gray, _ = _deskew(img)
    line_mask, h_lines, v_lines, binimg = extract_line_masks(gray)
    # 罫線セグメント
    h_segs = _lines_to_segments(h_lines, "h")
    v_segs = _lines_to_segments(v_lines, "v")

    # 短い縦線の救済検出（~20–80px）を追加
    short_vs = _extract_short_verticals(gray)

    # 既存の縦線と“ほぼ同じ位置”の重複は捨て、短線のみをマージ
    merged_vs = []
    def _near(a, b, t=6):
        return abs(a - b) <= t

    for s in short_vs:
        dup = False
        for l in v_segs:
            same_x = _near(s["x1"], l["x1"]) and _near(s["x2"], l["x2"])
            close_y = _near(s["y1"], l["y1"], 20) or _near(s["y2"], l["y2"], 20)
            if same_x and close_y:
                dup = True
                break
        if not dup:
            merged_vs.append(s)
    v_segs = v_segs + merged_vs

    text_boxes = _extract_text_boxes(binimg, line_mask)
    return {
        "size": {"w": int(gray.shape[1]), "h": int(gray.shape[0])},
        "lines": h_segs + v_segs,
        "boxes": text_boxes,
        "pre_filtered": True
    }