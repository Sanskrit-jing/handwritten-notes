"""Word 模板资产：docx --Word COM--> PDF --PyMuPDF--> 逐页位图 + 逐字坐标。

依赖：本机安装 Microsoft Word（转换用），pip 包 pywin32、PyMuPDF。

两种消费方式：
- docx_to_page_images：只取纸面位图（背景模板铺底）。
- docx_to_word_assets：Word 版式模式，一份 PDF 同源取两种产物——
  光栅化位图 = 纸面像素（表格/横线/页眉全保留），
  逐字坐标 = 每个字符的基线位置与字号（手写层按原位誊写）。
  像素与坐标同源，预览与 Word 打印内容框必然逐像素对齐。
"""
import os
import tempfile

import numpy as np
from PIL import Image


def _load_fitz():
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as e:
            raise RuntimeError(
                "缺少依赖 PyMuPDF，请先执行：pip install pymupdf") from e
    return fitz


def _word_to_pdf(docx_path: str) -> str:
    """Word COM：docx -> pdf（临时目录），返回 pdf 路径，调用方负责清理。"""
    try:
        import pythoncom
        import win32com.client as win32
    except ImportError as e:
        raise RuntimeError(
            "缺少依赖 pywin32，请先执行：pip install pywin32") from e

    docx_path = os.path.abspath(docx_path)
    pdf_path = os.path.join(
        tempfile.gettempdir(),
        f"hw_bg_{os.getpid()}_{abs(hash(docx_path)) % 10 ** 8}.pdf")

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(docx_path, ReadOnly=True)
        doc.SaveAs2(pdf_path, FileFormat=17)   # 17 = wdFormatPDF
        doc.Close(SaveChanges=False)
    except Exception as e:
        raise RuntimeError(
            "Word 转换 PDF 失败，请确认本机已安装 Microsoft Word。\n"
            f"详细信息：{e}") from e
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    return pdf_path


def _pdf_to_images(pdf_path: str, dpi: int) -> list:
    """pdf -> 逐页 PIL.Image(RGB)。"""
    fitz = _load_fitz()
    images = []
    scale = dpi / 72.0
    with fitz.open(pdf_path) as pdf:
        for page in pdf:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale), alpha=False)
            images.append(Image.frombytes(
                "RGB", (pix.width, pix.height), pix.samples))
    return images


def _pdf_to_char_boxes(pdf_path: str, dpi: int) -> list:
    """pdf -> 逐页字符坐标表。

    每页一个列表，元素: {char, x, y, size, bbox}
      char  字符
      x     基线左端 x @dpi 像素
      y     基线 y @dpi 像素
      size  字号 @dpi 像素（em 高）
      bbox  (x0, y0, x1, y1) 字符包围盒 @dpi 像素
    空白字符跳过（不誊写）。
    """
    fitz = _load_fitz()
    scale = dpi / 72.0
    pages = []
    with fitz.open(pdf_path) as pdf:
        for page in pdf:
            items = []
            raw = page.get_text("rawdict")
            for block in raw.get("blocks", []):
                if block.get("type") != 0:   # 0=文本块，跳过图片块
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size_px = float(span.get("size", 0.0)) * scale
                        for chd in span.get("chars", []):
                            c = chd.get("c", "")
                            if not c.strip():
                                continue
                            ox, oy = chd.get("origin", (None, None))
                            bx0, by0, bx1, by1 = chd["bbox"]
                            if ox is None:   # 无基线信息时用包围盒近似
                                ox, oy = bx0, by1
                            items.append({
                                "char": c,
                                "x": ox * scale,
                                "y": oy * scale,
                                "size": size_px,
                                "bbox": (bx0 * scale, by0 * scale,
                                         bx1 * scale, by1 * scale),
                            })
            pages.append(items)
    return pages


def _cleanup(pdf_path: str):
    try:
        os.remove(pdf_path)
    except OSError:
        pass


def docx_to_page_images(docx_path: str, dpi: int = 300) -> list:
    """把 Word 文档转为逐页 PIL.Image(RGB) 列表（背景模板铺底用）。

    需要 Microsoft Word；转换在临时目录进行，结束后自动清理 PDF。
    """
    pdf_path = _word_to_pdf(docx_path)
    try:
        return _pdf_to_images(pdf_path, dpi)
    finally:
        _cleanup(pdf_path)


def docx_to_word_assets(docx_path: str, dpi: int = 300) -> tuple:
    """Word 版式模式：docx -> (逐页位图, 逐页字符坐标表)。

    两种产物来自同一份 PDF：位图作纸面，坐标作手写锚点，
    预览与导出共用同一坐标表，与 Word 内容框逐像素对齐。
    """
    pdf_path = _word_to_pdf(docx_path)
    try:
        images = _pdf_to_images(pdf_path, dpi)
        boxes = _pdf_to_char_boxes(pdf_path, dpi)
        return images, boxes
    finally:
        _cleanup(pdf_path)


# ---------- 内置背景模板 ----------

from app.paths import DATA_DIR, RESOURCE_DIR

# 资源根下的 backgrounds/ 文件夹：放入 docx/图片信纸模板即被识别为内置背景（便携模式）
BUILTIN_BG_DIR = os.path.join(RESOURCE_DIR, "backgrounds")


def builtin_backgrounds() -> list:
    """列出内置背景模板：[(显示名, 路径), ...]，按文件名排序。同名时图片版优先。"""
    if not os.path.isdir(BUILTIN_BG_DIR):
        return []
    stems = {}   # 小写 stem -> (显示名, 路径)
    for fn in sorted(os.listdir(BUILTIN_BG_DIR)):
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if ext in IMAGE_BG_EXTS or (ext == ".docx" and stem.lower() not in stems):
            stems[stem.lower()] = (stem, os.path.join(BUILTIN_BG_DIR, fn))
    return [stems[k] for k in sorted(stems)]


# ---------- 用户图片背景模板 ----------

IMAGE_BG_EXTS = (".png", ".jpg", ".jpeg")

# 用户"存为模板"保存的图片模板目录（数据根 backgrounds/ 下，保持便携）
USER_BG_DIR = os.path.join(DATA_DIR, "backgrounds", "user")


def user_backgrounds() -> list:
    """列出用户保存的图片背景模板：[(显示名, 路径), ...]，按文件名排序。"""
    if not os.path.isdir(USER_BG_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(USER_BG_DIR)):
        if fn.lower().endswith(IMAGE_BG_EXTS):
            out.append((os.path.splitext(fn)[0],
                        os.path.join(USER_BG_DIR, fn)))
    return out


def load_image_background(path: str) -> list:
    """读取图片背景模板（单页设计）：返回 [PIL.Image(RGB)]。"""
    img = Image.open(path)
    img.load()
    return [img.convert("RGB")]


def save_background_template(images: list, name: str) -> list:
    """把当前背景位图逐页存为用户图片模板（PNG @300dpi，同名覆盖）。

    单页存"{name}.png"；多页每页一个模板"{name} 第N页.png"。
    返回保存的文件路径列表。
    """
    os.makedirs(USER_BG_DIR, exist_ok=True)
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "背景模板"
    paths = []
    for i, im in enumerate(images):
        fn = f"{safe}.png" if len(images) == 1 else f"{safe} 第{i + 1}页.png"
        p = os.path.join(USER_BG_DIR, fn)
        im.convert("RGB").save(p)
        paths.append(p)
    return paths


# ---------- 信纸横线检测 ----------

def detect_rule_lines(img) -> list:
    """检测信纸背景位图中的横线（书写线）。

    横向投影法：逐行统计"最长连续暗像素段"，实线横线的暗段跨度大
    （>=30% 页宽）而入选；页眉文字、竖线边框等短笔画行被排除。
    相邻暗行（间隔 <=2px）聚类为一条线（线宽通常 2~5px）。
    返回 [(y, x0, x1), ...]，y 为线中心，x 为线两端，坐标同输入位图。
    """
    gray = np.asarray(img.convert("L"))
    dark = gray < 160
    h, w = dark.shape
    min_span = w * 0.3

    hits: list[tuple[int, int, int]] = []   # (y, x0, x1) 达标的暗行
    for y in range(h):
        idx = np.flatnonzero(dark[y])
        if idx.size == 0:
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        if max(len(r) for r in runs) >= min_span:
            hits.append((y, int(idx[0]), int(idx[-1])))

    rules: list[tuple[int, int, int]] = []
    group: list[tuple[int, int, int]] = []

    def _flush():
        if group:
            rules.append((int(round(sum(g[0] for g in group) / len(group))),
                          min(g[1] for g in group),
                          max(g[2] for g in group)))
            group.clear()

    for item in hits:
        if group and item[0] - group[-1][0] > 2:
            _flush()
        group.append(item)
    _flush()
    return rules
