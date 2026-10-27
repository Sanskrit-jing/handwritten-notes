"""渲染引擎：把字符位置表逐页画成位图（预览 120 DPI / 导出 300 DPI）。

关键实现：
- 逐笔画扰动：每笔画从随机流 seed+页码+字符序号 依次采样 dx/dy/rot，
  先绕笔画质心旋转、再平移；改某项参数的幅度不会改变其他项的采样值。
- 带洞笔画在独立小图上填充（外轮廓填墨、洞挖空），再以 alpha 蒙版合成
  到整页，保证挖洞不误伤相邻笔画。
- 整页以 2x 超采样绘制后 LANCZOS 缩小，获得抗锯齿。
- 涂改痕迹（单横线/双横线/涂黑团）在字符绘制完成后叠加。
"""
import math

import numpy as np
from PIL import Image, ImageDraw

from .glyph_engine import glyph_engine
from .params import INK_COLORS, PAGE_H, PAGE_W, Params

RULE_COLOR = (215, 222, 230)   # 横线/方格 #D7DEE6
PAPER_COLOR = (255, 255, 255)


def _hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render_page(layout, params: Params, page_idx: int,
                dpi: int = 120, ss: int = 2) -> Image.Image:
    """渲染一页。dpi 决定输出尺寸，ss 为超采样倍率。"""
    dev = dpi / 300.0 * ss           # 300dpi 页面坐标 -> 设备坐标
    W = round(PAGE_W * dev)
    H = round(PAGE_H * dev)
    img = Image.new("RGB", (W, H), PAPER_COLOR)
    draw = ImageDraw.Draw(img)

    # 前置校验：字体加载失败必须上浮报错，不能静默产出空白页
    glyph_engine._load(params.font_path)

    _draw_paper(img, draw, layout, params, page_idx, dev)
    for ci in layout.chars_by_page[page_idx]:
        _draw_char(img, draw, params, ci, dev)
    _draw_corrections(draw, layout, params, page_idx, dev)

    if ss > 1:
        img = img.resize((round(W / ss), round(H / ss)), Image.LANCZOS)
    return img


# ---------- 纸张 ----------

def _draw_paper(img, draw, layout, params, page_idx, dev):
    # 背景模板优先：整页位图铺底（页数超出模板页数时循环复用），不再画纸张装饰
    bg_images = getattr(params, "bg_images", None)
    if bg_images:
        img.paste(
            bg_images[page_idx % len(bg_images)].resize(img.size, Image.LANCZOS),
            (0, 0))
        return

    ml = params.margin_left_px
    mr = params.margin_right_px
    if params.paper == "横线纸":
        for line in layout.pages[page_idx]:
            y = round(line.rule_y * dev)
            draw.line([(round(0.35 * ml * dev), y),
                       (round((PAGE_W - 0.35 * mr) * dev), y)],
                      fill=RULE_COLOR, width=max(1, round(2 * dev)))
    elif params.paper == "方格纸":
        cell = params.line_height
        mt = params.margin_top_px
        x0, y0 = ml, mt
        x1, y1 = PAGE_W - mr, PAGE_H - params.margin_bottom_px
        k = 0
        while y0 + k * cell <= y1:
            y = round((y0 + k * cell) * dev)
            draw.line([(round(x0 * dev), y), (round(x1 * dev), y)],
                      fill=RULE_COLOR, width=max(1, round(1.5 * dev)))
            k += 1
        k = 0
        while x0 + k * cell <= x1:
            x = round((x0 + k * cell) * dev)
            draw.line([(x, round(y0 * dev)), (x, round(y1 * dev))],
                      fill=RULE_COLOR, width=max(1, round(1.5 * dev)))
            k += 1
    # 空白页：无装饰


# ---------- 字符 ----------

def _draw_char(img, draw, params: Params, ci, dev):
    rng = np.random.default_rng([params.seed, ci.page, ci.seq, int(ci.is_rewrite)])
    base = _hex_rgb(INK_COLORS.get(params.ink_color, "#1A1A1A"))
    jitter = 1.0 + float(rng.uniform(-0.05, 0.05))
    ink = tuple(min(255, max(0, round(c * jitter))) for c in base)

    try:
        strokes = glyph_engine.strokes(params.font_path, ci.char)
        upem = glyph_engine._load(params.font_path)[2]
    except Exception:
        return

    scale = ci.size / upem
    weight = getattr(params, "stroke_weight", 1.0)
    for st in strokes:
        dx = float(rng.uniform(-params.dx_amp, params.dx_amp))
        dy = float(rng.uniform(-params.dy_amp, params.dy_amp))
        rot = float(rng.uniform(-params.rot_amp, params.rot_amp))
        _draw_stroke(img, draw, ink, st, ci, scale, dx, dy, rot, dev, weight)


def _draw_stroke(img, draw, ink, stroke, ci, scale, dx, dy, rot, dev, weight=1.0):
    outer = stroke["outer"]
    centroid = outer.mean(axis=0)
    cosr, sinr = math.cos(rot), math.sin(rot)
    rot_m = np.array([[cosr, -sinr], [sinr, cosr]])

    def to_page(pts: np.ndarray):
        q = (pts - centroid) * weight   # 笔迹加粗：绕笔画质心外扩（字洞同步缩小）
        q = q @ rot_m.T + centroid
        q = q * scale + np.array([dx, dy])
        out = np.empty((len(q), 2))
        out[:, 0] = ci.x + q[:, 0]
        out[:, 1] = ci.y - q[:, 1]      # 字体 y 向上 -> 图像 y 向下
        return out * dev

    dev_outer = to_page(outer)
    dev_holes = [to_page(h) for h in stroke["holes"]]

    mn = dev_outer.min(axis=0) - 2
    mx = dev_outer.max(axis=0) + 2
    x0, y0 = int(math.floor(mn[0])), int(math.floor(mn[1]))
    x1, y1 = int(math.ceil(mx[0])), int(math.ceil(mx[1]))
    if x1 <= x0 or y1 <= y0:
        return

    tile = Image.new("L", (x1 - x0, y1 - y0), 0)
    td = ImageDraw.Draw(tile)
    td.polygon([tuple(p) for p in dev_outer - (x0, y0)], fill=255)
    for h in dev_holes:
        td.polygon([tuple(p) for p in h - (x0, y0)], fill=0)
    img.paste(ink, (x0, y0), tile)


# ---------- 涂改痕迹 ----------

def _draw_corrections(draw, layout, params, page_idx, dev):
    ink = _hex_rgb(INK_COLORS.get(params.ink_color, "#1A1A1A"))
    for ci in layout.chars_by_page[page_idx]:
        if not ci.is_error:
            continue
        rng = np.random.default_rng([params.seed, ci.page, ci.seq, 0x57])
        w = ci.size
        x1 = (ci.x - w * 0.04) * dev
        x2 = (ci.x + w * 1.02) * dev

        if params.strike_mode == "单横线":
            _strike_line(draw, rng, x1, x2, ci, dev, ink, 0.45)
        elif params.strike_mode == "双横线":
            _strike_line(draw, rng, x1, x2, ci, dev, ink, 0.38)
            _strike_line(draw, rng, x1, x2, ci, dev, ink, 0.62)
        elif params.strike_mode == "涂黑团":
            _blob(draw, rng, ci, dev, ink)


def _strike_line(draw, rng, x1, x2, ci, dev, ink, h_ratio):
    y = (ci.y - ci.size * h_ratio) * dev
    tilt = float(rng.uniform(-1.5, 1.5)) * ci.size * 0.02 * dev
    width = max(1, round(ci.size * 0.07 * dev))
    draw.line([(x1, y + tilt), (x2, y - tilt)], fill=ink, width=width)


def _blob(draw, rng, ci, dev, ink):
    """涂黑团：多条随机斜线团反复覆盖错字区域。"""
    w = ci.size
    cx = (ci.x + w * 0.5) * dev
    cy = (ci.y - w * 0.45) * dev
    r = w * 0.55 * dev
    lw = max(1, round(w * 0.16 * dev))
    for _ in range(14):
        a = float(rng.uniform(0, math.pi))
        rr = r * float(rng.uniform(0.55, 1.0))
        x1 = cx - rr * math.cos(a)
        y1 = cy - rr * math.sin(a) * 0.7
        x2 = cx + rr * math.cos(a)
        y2 = cy + rr * math.sin(a) * 0.7
        jx = float(rng.uniform(-0.1, 0.1)) * r
        jy = float(rng.uniform(-0.1, 0.1)) * r
        draw.line([(x1 + jx, y1 + jy), (x2 + jx, y2 + jy)], fill=ink, width=lw)
