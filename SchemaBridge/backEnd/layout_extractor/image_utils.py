# image_utils.py
# image_utils.py
import cv2

def binarize(gray):
    """薄い罫線も残しやすくするため、CLAHE と局所適応閾値を併用した二値化。"""
    if len(gray.shape) == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

    # ノイズ抑制 + 局所コントラスト補正
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe_img = clahe.apply(blur)

    # グローバル閾値（Otsu）
    _, th_otsu = cv2.threshold(clahe_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 局所適応閾値（薄い線を拾いやすくする）
    th_adapt = cv2.adaptiveThreshold(
        clahe_img,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=31,
        C=10,
    )

    # 2つの結果を統合して弱い線を残す
    merged = cv2.bitwise_or(th_otsu, th_adapt)

    # 軽いノイズ除去
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    merged = cv2.morphologyEx(merged, cv2.MORPH_OPEN, kernel, iterations=1)
    return merged

def extract_line_masks(gray, hor_len=60, ver_len=60):
    """
    灰度画像を受け取り、二値化してから水平/垂直マスクと統合マスクを返す。
    戻り値: (line_mask, h_lines, v_lines, binimg)
    """
    # 入力がBGRならグレースケール化
    if len(gray.shape) == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

    # 既存パイプライン互換の二値画像
    binimg = binarize(gray)
    h, w = binimg.shape

    # 画像サイズに応じたカーネル長（長い罫線優先）
    kx = max(15, w // hor_len)
    ky = max(15, h // ver_len)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky))

    # 長い水平/垂直線を抽出
    h_lines = cv2.morphologyEx(binimg, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(binimg, cv2.MORPH_OPEN, v_kernel, iterations=1)

    # 軽い閉演算でギャップを詰める（太線を優先、過剰膨張は避ける）
    h_lines = cv2.morphologyEx(h_lines, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)), iterations=1)
    v_lines = cv2.morphologyEx(v_lines, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)), iterations=1)

    # 統合マスク
    line_mask = cv2.bitwise_or(h_lines, v_lines)

    return line_mask, h_lines, v_lines, binimg