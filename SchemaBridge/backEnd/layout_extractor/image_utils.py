# image_utils.py
import cv2
import numpy as np

def binarize(gray):
    """適応的に二値化して罫線抽出に適したビットマップを返す"""
    if len(gray.shape) == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    # Otsu + 反転（黒=線）
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return th

def extract_line_masks(thresh, hor_len=60, ver_len=60):
    """水平・垂直の線分マスク（モルフォロジ）を返す"""
    h, w = thresh.shape
    kx = max(15, w // hor_len)
    ky = max(15, h // ver_len)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky))
    hor_mask = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel, iterations=1)
    ver_mask = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel, iterations=1)
    return hor_mask, ver_mask