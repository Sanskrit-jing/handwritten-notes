"""字形引擎：把 TTF/OTF 字形拆成"笔画"（闭合轮廓组），并施加逐笔画扰动。

核心思路（轮廓即笔画）：
- 一个字形 = 若干闭合轮廓；大多数字体里轮廓与笔画一一对应（"三"=3 条轮廓）。
- 带洞字（回/国/园）的洞是反向轮廓，必须与包住它的外轮廓绑定，
  否则洞会被独立扰动导致穿帮。用嵌套深度判定外/洞，洞归属包含它的最小外轮廓。
- 解析结果按 (字体, 字符) 缓存；扰动是纯几何变换，不重复解析。

坐标系约定：
- fontTools 字形坐标：y 向上，原点在基线左侧，单位 em（head.unitsPerEm）。
- 本引擎输出保持 em 坐标（y 向上），翻转到图像坐标由渲染层负责。
"""
import math

import numpy as np
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen


class GlyphEngine:
    def __init__(self):
        self._fonts = {}       # (path, index) -> (glyphset, cmap, upem, hmtx)
        self._glyph_cache = {} # (path, index, char) -> strokes

    # ---------- 字体加载 ----------

    def _load(self, path: str, index: int = 0):
        key = (path, index)
        if key not in self._fonts:
            font = TTFont(path, fontNumber=index, lazy=True)
            self._fonts[key] = (
                font.getGlyphSet(),
                font.getBestCmap(),
                font["head"].unitsPerEm,
                font["hmtx"],
            )
        return self._fonts[key]

    def advance(self, path: str, ch: str, index: int = 0) -> float:
        """字符在 em 单位下的步进宽度。"""
        glyphset, cmap, upem, hmtx = self._load(path, index)
        gname = cmap.get(ord(ch))
        if gname is None:
            return 0.5 * upem
        return float(hmtx[gname][0])

    def strokes(self, path: str, ch: str, index: int = 0):
        """返回 [{'outer': np(N,2), 'holes': [np(M,2), ...]}, ...]，em 坐标 y 向上。

        缺字时返回一个方框轮廓占位（渲染层会画出方框提示缺字）。
        """
        key = (path, index, ch)
        cached = self._glyph_cache.get(key)
        if cached is not None:
            return cached

        glyphset, cmap, upem, hmtx = self._load(path, index)
        gname = cmap.get(ord(ch))
        if gname is None:
            w, top = 0.5 * upem, 0.75 * upem
            pad = 0.04 * upem
            box = np.array([
                [pad, pad], [w - pad, pad],
                [w - pad, top - pad], [pad, top - pad],
            ], dtype=float)
            strokes = [{"outer": box, "holes": []}]
        else:
            pen = RecordingPen()
            glyphset[gname].draw(pen)
            contours = self._flatten(pen.value)
            strokes = self._bind(contours)
        self._glyph_cache[key] = strokes
        return strokes

    # ---------- 轮廓提取与拍平 ----------

    def _flatten(self, ops):
        """把 RecordingPen 的指令流拍平成多段闭合轮廓（浮点点列）。"""
        contours = []
        cur: list[tuple[float, float]] = []

        def quad_samples(p0, p1, p2, n=8):
            t = np.linspace(0.0, 1.0, n + 1)[1:]
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
            return list(zip(x, y))

        def cubic_samples(p0, p1, p2, p3, n=10):
            t = np.linspace(0.0, 1.0, n + 1)[1:]
            x = ((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0]
                 + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0])
            y = ((1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1]
                 + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
            return list(zip(x, y))

        for op, args in ops:
            if op == "moveTo":
                if cur:
                    contours.append(np.array(cur))
                cur = [(float(args[0][0]), float(args[0][1]))]
            elif op == "lineTo":
                cur.append((float(args[0][0]), float(args[0][1])))
            elif op == "qCurveTo":
                # TrueType：任意个连续 off-curve 点 + 末位 on-curve（None 表示闭合回起点）
                pts = [(float(p[0]), float(p[1])) for p in args if p is not None]
                if not pts:
                    continue
                closed = args[-1] is None
                offs = pts if closed else pts[:-1]
                if not cur:
                    # TrueType 合法形态：轮廓无初始 on-curve（无 moveTo），
                    # 隐式起点 = 首尾两个 off-curve 的中点（手写字体常用此形态）
                    cur = [((offs[-1][0] + offs[0][0]) / 2.0,
                            (offs[-1][1] + offs[0][1]) / 2.0)]
                on = cur[0] if closed else pts[-1]
                prev = cur[-1]
                if len(offs) == 1:
                    cur.extend(quad_samples(prev, offs[0], on))
                else:
                    # 相邻 off-curve 中点即隐式 on-curve 点
                    ends = []
                    for i in range(len(offs) - 1):
                        ends.append((
                            (offs[i][0] + offs[i + 1][0]) / 2.0,
                            (offs[i][1] + offs[i + 1][1]) / 2.0,
                        ))
                    ends.append(on)
                    for ctrl, end in zip(offs, ends):
                        cur.extend(quad_samples(prev, ctrl, end))
                        prev = end
            elif op == "curveTo":
                # 畸形字形防护：曲线指令前无起点则跳过
                if not cur:
                    continue
                # 三次贝塞尔（CFF 字体）
                p1, p2, p3 = [(float(a[0]), float(a[1])) for a in args]
                cur.extend(cubic_samples(cur[-1], p1, p2, p3))
            elif op in ("closePath", "endPath"):
                if cur:
                    contours.append(np.array(cur))
                cur = []
        if cur:
            contours.append(np.array(cur))
        # 丢弃退化轮廓（点数 <2）：部分手写字体存在畸形字形，传给绘制层会崩
        return [c for c in contours if len(c) >= 2]

    @staticmethod
    def _signed_area(pts: np.ndarray) -> float:
        x, y = pts[:, 0], pts[:, 1]
        return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))

    @staticmethod
    def _points_inside(inner: np.ndarray, outer: np.ndarray) -> bool:
        """射线法：inner 的全部点是否都落在 outer 内（分块向量化）。"""
        step = 256
        px, py = outer[:, 0], outer[:, 1]
        x2, y2 = np.roll(px, -1), np.roll(py, -1)
        horiz = py == y2
        px, py, x2, y2 = px[~horiz], py[~horiz], x2[~horiz], y2[~horiz]
        for s in range(0, len(inner), step):
            x = inner[s:s + step, 0][:, None]
            y = inner[s:s + step, 1][:, None]
            cond = (py > y) != (y2 > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (x2 - px) * (y - py) / (y2 - py) + px
            cross = np.logical_and(cond, x < xint).sum(axis=1) % 2
            if not np.all(cross == 1):
                return False
        return True

    def _bind(self, contours):
        """外轮廓与洞绑定成笔画。

        嵌套深度（被几个其他轮廓**完全包含**）为偶 -> 外轮廓；奇 -> 洞；
        洞归属包含它的、面积最小的直接外轮廓。
        手写字体笔画轮廓互相交叠，单点 probe 会把交叠笔画误判成洞，
        故判定要求 inner 的全部点都落在 outer 内（包围盒预筛加速）。
        """
        n = len(contours)
        if n == 0:
            return []
        if n == 1:
            return [{"outer": contours[0], "holes": []}]

        boxes = [(c[:, 0].min(), c[:, 0].max(), c[:, 1].min(), c[:, 1].max())
                 for c in contours]

        def bbox_inside(bi, bj):
            return not (bi[0] < bj[0] or bi[1] > bj[1]
                        or bi[2] < bj[2] or bi[3] > bj[3])

        depths = [0] * n
        for i in range(n):
            for j in range(n):
                if i != j and bbox_inside(boxes[i], boxes[j]) \
                        and self._points_inside(contours[i], contours[j]):
                    depths[i] += 1

        strokes = []
        outer_idx = []
        for i, c in enumerate(contours):
            if depths[i] % 2 == 0:
                strokes.append({"outer": c, "holes": []})
                outer_idx.append(i)
        for i, c in enumerate(contours):
            if depths[i] % 2 == 1:
                best, best_area = None, None
                for k, j in enumerate(outer_idx):
                    if bbox_inside(boxes[i], boxes[j]) \
                            and self._points_inside(c, contours[j]):
                        a = abs(self._signed_area(contours[j]))
                        if best is None or a < best_area:
                            best, best_area = k, a
                if best is not None:
                    strokes[best]["holes"].append(c)
                else:  # 兜底：找不到归属就当外轮廓
                    strokes.append({"outer": c, "holes": []})
                    outer_idx.append(i)
        return strokes

    # ---------- 逐笔画扰动 ----------

    def char_polys(self, path: str, ch: str, size_px: float,
                   dx: float, dy: float, rot: float, index: int = 0):
        """返回扰动后的笔画多边形列表，坐标为 em 缩放到 size_px 后（y 仍向上）。

        变换顺序固定：先绕笔画自身质心旋转，再平移 (dx, dy)。
        """
        strokes = self.strokes(path, ch, index)
        scale = size_px / self._load(path, index)[2]

        cosr, sinr = math.cos(rot), math.sin(rot)
        rot_m = np.array([[cosr, -sinr], [sinr, cosr]])
        shift = np.array([dx, dy])

        out = []
        for st in strokes:
            outer = st["outer"] * scale
            centroid = outer.mean(axis=0)
            o = (outer - centroid) @ rot_m.T + centroid + shift
            holes = [(h * scale - centroid) @ rot_m.T + centroid + shift
                     for h in st["holes"]]
            out.append((o, holes))
        return out


# 全局单例：解析缓存跨页共享
glyph_engine = GlyphEngine()
