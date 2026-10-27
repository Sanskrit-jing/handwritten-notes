"""导出层：PNG 批量导出 + img2pdf 合成 PDF。"""
import io
import os

import img2pdf
from PIL import Image

from .params import DPI_BASE, PAGE_H, PAGE_W
from .renderer import render_page


def export_png(layout, params, out_dir: str, base_name: str,
               progress=None) -> list[str]:
    """逐页渲染 300 DPI PNG。progress: fn(done, total) -> None"""
    os.makedirs(out_dir, exist_ok=True)
    files = []
    n = layout.total_pages
    for i in range(n):
        img = render_page(layout, params, i, dpi=DPI_BASE, ss=2)
        path = os.path.join(out_dir, f"{base_name}_p{i + 1:02d}.png")
        img.save(path, "PNG")
        files.append(path)
        if progress:
            progress(i + 1, n)
    return files


def export_pdf(layout, params, out_dir: str, base_name: str,
               progress=None) -> str:
    """逐页渲染后直接在内存合成单文件 PDF。"""
    a4 = (img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))
    layout_fun = img2pdf.get_layout_fun(a4)
    n = layout.total_pages
    images = []
    for i in range(n):
        img = render_page(layout, params, i, dpi=DPI_BASE, ss=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        images.append(buf.getvalue())
        if progress:
            progress(i + 1, n)
    pdf_bytes = img2pdf.convert(images, layout_fun=layout_fun)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{base_name}.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return path
