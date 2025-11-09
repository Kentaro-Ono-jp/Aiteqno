import os
from typing import Optional, Tuple
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw
from viewport import Viewport  # 同階層

def _max_xy_from_layout(layout_data):
    xs, ys = [], []
    for l in layout_data.get("lines", []):
        xs += [l["x1"], l["x2"]]; ys += [l["y1"], l["y2"]]
    for b in layout_data.get("boxes", []):
        xs += [b["x"], b["x"] + b["w"]]; ys += [b["y"], b["y"] + b["h"]]
    size_info = layout_data.get("size") or {}
    xs.append(size_info.get("w", 0))
    ys.append(size_info.get("h", 0))
    max_x = max(xs) if xs else 1
    max_y = max(ys) if ys else 1
    return (max(max_x, 1), max(max_y, 1))

class _PdfRenderer:
    def __init__(self, output_pdf, page_size=A4):
        self.c = canvas.Canvas(output_pdf, pagesize=page_size)
        self.page_width, self.page_height = page_size

    def _y(self, y_top: float) -> float:
        return self.page_height - y_top

    def draw_image(self, x, y, w, h, path):
        try:
            reader = ImageReader(path)
            self.c.drawImage(reader, x, self._y(y) - h, width=w, height=h,
                             preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    def draw_line(self, x1, y1, x2, y2, stroke=(0, 0, 0), dash: Optional[Tuple[int, int]] = None, width=1.0):
        self.c.saveState()
        self.c.setStrokeColorRGB(*stroke)
        self.c.setLineWidth(width)
        if dash:
            self.c.setDash(dash[0], dash[1])
        self.c.line(x1, self._y(y1), x2, self._y(y2))
        self.c.restoreState()

    def draw_rect(self, x, y, w, h, stroke=(0, 0, 0), dash: Optional[Tuple[int, int]] = None, width=1.0, fill=None):
        self.c.saveState()
        self.c.setStrokeColorRGB(*stroke)
        self.c.setLineWidth(width)
        if dash:
            self.c.setDash(dash[0], dash[1])
        if fill is not None:
            self.c.setFillColorRGB(*fill)
            self.c.rect(x, self._y(y) - h, w, h, stroke=1, fill=1)
        else:
            self.c.rect(x, self._y(y) - h, w, h, stroke=1, fill=0)
        self.c.restoreState()

    def draw_text(self, x, y, text, size=8, color=(0.3, 0.3, 0.3)):
        self.c.setFillColorRGB(*color)
        self.c.setFont("Helvetica", size)
        self.c.drawString(x, self._y(y), text)

    def save(self):
        self.c.save()

class _PngRenderer:
    def __init__(self, page_size=A4):
        pw, ph = page_size
        self.image = Image.new("RGB", (int(round(pw)), int(round(ph))), "white")
        self.draw = ImageDraw.Draw(self.image)

    def draw_image(self, x, y, w, h, path):
        try:
            bg = Image.open(path).convert("RGB")
            bg = bg.resize((int(round(w)), int(round(h))))
            self.image.paste(bg, (int(round(x)), int(round(y))))
        except Exception:
            pass

    def _dash_line(self, x1, y1, x2, y2, color, width, dash):
        # 単純なパターン描画（dash=(on, off) を繰り返す）
        import math
        on, off = dash if dash else (0, 0)
        total = math.hypot(x2 - x1, y2 - y1)
        if total == 0:
            return
        dx, dy = (x2 - x1) / total, (y2 - y1) / total
        pos = 0.0
        on = max(1, int(round(on)))
        off = max(1, int(round(off)))
        while pos < total:
            seg_on = min(on, int(total - pos))
            x_start = x1 + dx * pos
            y_start = y1 + dy * pos
            x_end   = x1 + dx * (pos + seg_on)
            y_end   = y1 + dy * (pos + seg_on)
            self.draw.line((x_start, y_start, x_end, y_end), fill=color, width=width)
            pos += on + off

    def draw_line(self, x1, y1, x2, y2, stroke=(0, 0, 0), dash: Optional[Tuple[int, int]] = None, width=1):
        color = tuple(int(255*c) for c in stroke)
        width = int(round(width))
        if dash:
            self._dash_line(x1, y1, x2, y2, color, width, dash)
        else:
            self.draw.line((x1, y1, x2, y2), fill=color, width=width)

    def draw_rect(self, x, y, w, h, stroke=(0, 0, 0), dash: Optional[Tuple[int, int]] = None, width=1, fill=None):
        color = tuple(int(255*c) for c in stroke)
        width = int(round(width))
        if fill is not None:
            # 今回は塗りつぶしは使っていないので従来通り
            self.draw.rectangle((x, y, x + w, y + h), outline=color, width=width, fill=tuple(int(255*c) for c in fill))
        else:
            if dash:
                # 矩形を4辺のダッシュ線で描く
                self._dash_line(x, y, x + w, y,     color, width, dash)
                self._dash_line(x + w, y, x + w, y + h, color, width, dash)
                self._dash_line(x + w, y + h, x, y + h, color, width, dash)
                self._dash_line(x, y + h, x, y,     color, width, dash)
            else:
                self.draw.rectangle((x, y, x + w, y + h), outline=color, width=width)

    def draw_text(self, x, y, text, size=8, color=(0.3, 0.3, 0.3)):
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        self.draw.text((x, y), text, fill=tuple(int(255*c) for c in color), font=font)

    def save(self, path):
        self.image.save(path)

def render_layout(layout_data: dict, backend, vp: Viewport, debug_image: Optional[str]):
    size_info = layout_data.get("size") or {}
    src_w = size_info.get("w", 0) or layout_data.get("max_x") or 0
    src_h = size_info.get("h", 0) or layout_data.get("max_y") or 0
    if debug_image and os.path.isfile(debug_image) and src_w and src_h:
        bx, by = vp.map_top(0, 0)
        bw, bh = (src_w * vp.scale, src_h * vp.scale)
        backend.draw_image(bx, by, bw, bh, debug_image)

    # --- ここから罫線描画：SOT（layout_data["lines"]）を共通利用 ---
    # 1) 実運用の黒実線（デフォルトで常に描く）
    for l in layout_data.get("lines", []):
        x1t, y1t = vp.map_top(l["x1"], l["y1"])
        x2t, y2t = vp.map_top(l["x2"], l["y2"])
        backend.draw_line(x1t, y1t, x2t, y2t, stroke=(0.0, 0.0, 0.0), dash=None, width=0.8)

    # 2) デバッグオーバーレイ（青点線：罫線）
    if layout_data.get("debug_overlay_lines", layout_data.get("debug_overlay", True)):
        for l in layout_data.get("lines", []):
            x1t, y1t = vp.map_top(l["x1"], l["y1"])
            x2t, y2t = vp.map_top(l["x2"], l["y2"])
            backend.draw_line(x1t, y1t, x2t, y2t, stroke=(0.0, 0.5, 1.0), dash=(4, 3), width=0.8)

    # 3) デバッグオーバーレイ（赤点線：OCR対象ボックス）※新設フラグで制御
    if layout_data.get("debug_overlay_boxes", True):
        for b in layout_data.get("boxes", []):
            x1t, y1t = vp.map_top(b["x"], b["y"])
            w, h = (b["w"] * vp.scale, b["h"] * vp.scale)
            backend.draw_rect(x1t, y1t, w, h, stroke=(1.0, 0.3, 0.3), dash=(2, 2), width=0.7)

    line_h = 12
    gap = 2
    y2 = vp.page_height - vp.margin - line_h
    y1 = vp.page_height - vp.margin - (2 * line_h + gap)
    label_main = f"A4 layout template (scale={vp.scale:.3f}, margin={vp.margin}pt)"
    label_dbg = "Debug overlay"
    if debug_image and os.path.isfile(debug_image):
        label_dbg += f" / source: {os.path.basename(debug_image)}"
    backend.draw_text(20, y1, label_main, size=8, color=(0.3, 0.3, 0.3))
    backend.draw_text(20, y2, label_dbg,  size=8, color=(0.3, 0.3, 0.3))

def draw_layout_on_pdf(layout_data, output_pdf, debug_image=None, page_size=None, margin=0.0):
    # 入力画像の寸法をページサイズに採用（A4前提でも縮小をかけない）
    if page_size is None:
        if debug_image and os.path.isfile(debug_image):
            with Image.open(debug_image) as im:
                page_size = (float(im.width), float(im.height))
        else:
            size_info = layout_data.get("size") or {}
            page_size = (float(size_info.get("w", A4[0])), float(size_info.get("h", A4[1])))

    page_width, page_height = page_size
    max_x, max_y = _max_xy_from_layout(layout_data)
    vp = Viewport(page_width, page_height, margin, max_x, max_y, fit="none")
    renderer = _PdfRenderer(output_pdf, page_size=page_size)
    render_layout(layout_data, renderer, vp, debug_image)
    renderer.save()

def draw_layout_on_png(layout_data, output_png, debug_image=None, page_size=None, margin=0.0):
    if page_size is None:
        if debug_image and os.path.isfile(debug_image):
            with Image.open(debug_image) as im:
                page_size = (float(im.width), float(im.height))
        else:
            size_info = layout_data.get("size") or {}
            page_size = (float(size_info.get("w", A4[0])), float(size_info.get("h", A4[1])))

    page_width, page_height = page_size
    max_x, max_y = _max_xy_from_layout(layout_data)
    vp = Viewport(page_width, page_height, margin, max_x, max_y, fit="none")
    renderer = _PngRenderer(page_size=page_size)
    render_layout(layout_data, renderer, vp, debug_image)
    renderer.save(output_png)