"""无 GUI 冒烟测试：验证 字形拆笔画 -> 排版 -> 渲染 -> 导出 全链路。

用法: python tests/smoke_render.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.docx_parser import parse_docx
from app.exporter import export_png
from app.glyph_engine import glyph_engine
from app.layout_engine import layout_document
from app.params import DPI_PREVIEW, Params
from app.renderer import render_page

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLE = os.path.join(ROOT, "samples", "示例文档.docx")
OUT = os.path.join(ROOT, "output")


def main():
    # 1) 字形引擎：拆笔画数量检查
    t0 = time.time()
    for ch, expect_min in (("三", 3), ("回", 2), ("国", 2)):
        strokes = glyph_engine.strokes(r"C:\Windows\Fonts\simkai.ttf", ch)
        holes = sum(len(s["holes"]) for s in strokes)
        print(f"[glyph] {ch}: {len(strokes)} 笔画, {holes} 洞")
        assert len(strokes) >= expect_min, f"{ch} 拆笔画异常"
    print(f"[glyph] 解析耗时 {time.time() - t0:.2f}s")

    # 2) 解析 + 排版
    t0 = time.time()
    paras = parse_docx(SAMPLE)
    params = Params()
    params.error_rate = 0.06   # 冒烟测试顺带验证错字链路
    layout = layout_document(paras, params)
    print(f"[layout] {len(paras)} 段 -> {layout.total_pages} 页, "
          f"{layout.error_count} 处错字, 耗时 {time.time() - t0:.2f}s")
    assert layout.total_pages >= 1
    assert layout.error_count > 0

    # 3) 渲染预览页
    t0 = time.time()
    img = render_page(layout, params, 0, dpi=DPI_PREVIEW, ss=2)
    print(f"[render] 预览页尺寸 {img.size}, 耗时 {time.time() - t0:.2f}s")

    # 4) 确定性验证：同参数渲染两次应逐像素一致
    img2 = render_page(layout, params, 0, dpi=DPI_PREVIEW, ss=2)
    from PIL import ImageChops
    diff = ImageChops.difference(img, img2).getbbox()
    assert diff is None, "两次渲染结果不一致！"
    print("[render] 确定性验证通过（预览 = 导出）")

    # 5) 导出
    t0 = time.time()
    files = export_png(layout, params, OUT, "smoke",
                       progress=lambda d, t: print(f"  导出 {d}/{t}"))
    print(f"[export] {len(files)} 张 PNG, 耗时 {time.time() - t0:.2f}s")

    # 6) Word 版式模式分支（模拟 PDF 提取的逐字坐标表，不依赖 Word COM）
    t0 = time.time()
    wparams = Params()
    wparams.layout_mode = "word"
    wparams.handwriting_scale = 0.9
    wparams.word_char_boxes = [
        [{"char": c, "x": 300 + i * 120.0, "y": 400.0, "size": 50.0,
          "bbox": (300 + i * 120.0, 355.0, 350 + i * 120.0, 410.0)}
         for i, c in enumerate("手写测试第一页文字")],
        [{"char": c, "x": 300 + i * 120.0, "y": 400.0, "size": 50.0,
          "bbox": (300 + i * 120.0, 355.0, 350 + i * 120.0, 410.0)}
         for i, c in enumerate("第二页内容")],
    ]
    n_expect = sum(len("".join(b["char"] for b in pg))
                   for pg in wparams.word_char_boxes)
    wlayout = layout_document(paras, wparams)
    assert wlayout.total_pages == 2, "Word 版式页数应为坐标表页数"
    assert wlayout.error_count == 0, "Word 版式不应产生错字"
    n_word_chars = sum(len(p) for p in wlayout.chars_by_page)
    assert n_word_chars == n_expect, "Word 版式字符数应与坐标表一致"
    wimg = render_page(wlayout, wparams, 0, dpi=DPI_PREVIEW, ss=2)
    wimg2 = render_page(wlayout, wparams, 0, dpi=DPI_PREVIEW, ss=2)
    diff = ImageChops.difference(wimg, wimg2).getbbox()
    assert diff is None, "Word 版式两次渲染结果不一致！"
    # 基线原位：手写字 y 应等于坐标表 y（基线不动）
    ci0 = wlayout.chars_by_page[0][0]
    assert abs(ci0.y - 400.0) < 1e-6, "Word 版式基线应与坐标表一致"
    assert abs(ci0.size - 45.0) < 1e-6, "Word 版式字号应按缩放系数缩小"
    print(f"[word] Word 版式分支 OK：{wlayout.total_pages} 页, "
          f"{n_word_chars} 字, 耗时 {time.time() - t0:.2f}s")

    print("冒烟测试全部通过 ->", OUT)


if __name__ == "__main__":
    main()
