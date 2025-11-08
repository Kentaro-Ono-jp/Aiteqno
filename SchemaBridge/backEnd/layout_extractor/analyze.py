import cv2
import numpy as np
import cv2, numpy as np

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

def _extract_line_masks(gray):
    # 二値化
    binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    h, w = binimg.shape
    # 罫線用モルフォロジ（水平方向・垂直方向）
    kx = max(15, w//60)   # 画像依存の自動サイズ
    ky = max(15, h//60)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky))
    h_lines = cv2.morphologyEx(binimg, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(binimg, cv2.MORPH_OPEN, v_kernel, iterations=1)
    line_mask = cv2.bitwise_or(h_lines, v_lines)
    return line_mask, h_lines, v_lines, binimg

def _lines_to_segments(mask, orientation="h"):
    # 細線化に近い縮小
    skel = cv2.ximgproc.thinning(mask) if hasattr(cv2, "ximgproc") else mask
    # Probabilistic Hough（短いゴミ抑制）
    min_len = int(0.08 * (mask.shape[1] if orientation=="h" else mask.shape[0]))
    lines = cv2.HoughLinesP(skel, 1, np.pi/180, threshold=80,
                            minLineLength=min_len, maxLineGap=10)
    segs = []
    if lines is not None:
        for x1,y1,x2,y2 in lines[:,0,:]:
            if orientation=="h" and abs(y2-y1) <= 3:
                segs.append({"x1":int(min(x1,x2)),"y1":int(y1),
                             "x2":int(max(x1,x2)),"y2":int(y2)})
            elif orientation=="v" and abs(x2-x1) <= 3:
                segs.append({"x1":int(x1),"y1":int(min(y1,y2)),
                             "x2":int(x2),"y2":int(max(y1,y2))})
    return segs

def _extract_text_boxes(binimg, line_mask):
    # 罫線を除去したテキスト領域に限定
    no_lines = cv2.bitwise_and(binimg, cv2.bitwise_not(line_mask))
    # 文字をブロック化（膨張→開閉）
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5,5))
    merged = cv2.dilate(no_lines, k, iterations=1)
    merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, k, iterations=1)
    cnts,_ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    H,W = binimg.shape
    for c in cnts:
        x,y,w,h = cv2.boundingRect(c)
        area=w*h
        if area<200 or h<10 or w<10:     # 極小ノイズは除外
            continue
        if w>0.95*W or h>0.95*H:         # ほぼ全面は除外
            continue
        boxes.append({"x":int(x),"y":int(y),"w":int(w),"h":int(h)})
    return boxes

def _detect_short_vertical_ticks(gray, binimg, h_lines):
    
    # 小さめの縦カーネルで縦成分のみ抽出
    H, W = binimg.shape
    ky_small = max(5, H // 200)
    v_small = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky_small))
    v_mask = cv2.morphologyEx(binimg, cv2.MORPH_OPEN, v_small, iterations=1)

    # 端点が水平線(h_lines)に接している縦線だけを拾う
    min_len = max(10, H // 120)                    # 短い縦棒も拾う
    lines = cv2.HoughLinesP(v_mask, 1, np.pi/180, 30,
                            minLineLength=min_len, maxLineGap=4)
    segs = []
    if lines is None:
        return segs

    # 端点が水平線に近いかを確認（±2px）
    h_pad = cv2.dilate(h_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (5,5)), 1)
    for x1,y1,x2,y2 in lines[:,0,:]:
        if abs(x2 - x1) > 3:  # ほぼ縦のみ
            continue
        y_top, y_bot = (y1, y2) if y1 < y2 else (y2, y1)
        # 端点近傍の水平線ヒット
        ok_top = h_pad[max(0, y_top-2):y_top+3, max(0, x1-2):x1+3].any()
        ok_bot = h_pad[max(0, y_bot-2):y_bot+3, max(0, x1-2):x1+3].any()
        if ok_top or ok_bot:
            segs.append({"x1": int(x1), "y1": int(y_top),
                         "x2": int(x2), "y2": int(y_bot)})
    return segs

def analyze_image(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(image_path)
    gray, _ = _deskew(img)
    line_mask, h_lines, v_lines, binimg = _extract_line_masks(gray)
    # 罫線セグメント
    # 罫線セグメント
    h_segs = _lines_to_segments(h_lines, "h")
    v_segs = _lines_to_segments(v_lines, "v")
    # 追加：水平線と接する短い縦棒も抽出
    v_ticks = _detect_short_vertical_ticks(gray, binimg, h_lines)

    # 設問側のブロック
    text_boxes = _extract_text_boxes(binimg, line_mask)
    return {
        "size": {"w": int(gray.shape[1]), "h": int(gray.shape[0])},
        "lines": h_segs + v_segs + v_ticks,   # ← 追加分を連結
        "boxes": text_boxes,
        "pre_filtered": True                  # 描画側の長さフィルタをスキップさせるフラグ
    }