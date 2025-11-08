# viewport.py
from typing import Tuple

class Viewport:
    """
    上原点・y下向きの正準座標を、用紙上（上原点）の座標へマッピングする。
    PDF描画時のy反転はレンダラ側で吸収する。
    """
    def __init__(self, page_width: float, page_height: float, margin: float,
                 content_w: float, content_h: float):
        self.page_width = page_width
        self.page_height = page_height
        self.margin = margin
        fit_w = page_width - margin * 2
        fit_h = page_height - margin * 2
        self.scale = 1.0 if content_w <= 0 or content_h <= 0 else min(fit_w / content_w, fit_h / content_h)
        self.content_width = content_w * self.scale
        self.content_height = content_h * self.scale
        self.ox = (page_width - self.content_width) / 2.0
        self.oy = margin  # 上余白固定

    def map_top(self, x: float, y: float) -> Tuple[float, float]:
        # 左上原点の正準座標 → ページ座標（左上原点）
        return (self.ox + x * self.scale, self.oy + y * self.scale)