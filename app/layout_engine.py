"""排版引擎：两遍排版，产出全文的字符位置表（预览与导出共用的唯一数据源）。

流程：
  第一遍  按错字率抽样（固定种子派生随机流，保证可复现）。
  第二遍  逐字符累积步进宽度 -> 组行 -> 统一分页定位 -> 对齐偏移 -> 错字补写。

"Word 为背景"的核心在本模块：横线纸的横线位置 = 排版行的基线，
纸和字由同一次排版生成，几何上天然对齐，无需手动调整。

所有坐标以 A4 @300dpi 像素为基准。
"""
from dataclasses import dataclass, field

import numpy as np

from .fonts import COMMON_CHARS
from .glyph_engine import glyph_engine
from .params import PAGE_H, PAGE_W, Params

# 错字抽样随机流的隔离标签：seed + 标签 + 段落序号
_ERR_TAG = 0xA1E2


@dataclass
class CharInstance:
    """一个落在纸上的字符（含错字补写字）。"""
    char: str
    x: float                     # 基线左端 x @300dpi
    y: float                     # 基线 y @300dpi
    size: float                  # 字面高度 px @300dpi
    page: int
    para_idx: int
    seq: int                     # 全文字符序号（随机流派生用），补写字沿用原字 seq
    is_error: bool = False       # 该字是"被写错的字"（涂改对象）
    is_rewrite: bool = False     # 该字是补写的正确字
    error_glyph: str = ""        # 错写成的字（is_error 时有效）


@dataclass
class Line:
    page: int = 0
    y_baseline: float = 0.0
    rule_y: float = 0.0          # 横线纸的横线位置（基线略下方，字"坐"在线上）
    para_idx: int = 0
    size: float = 0.0
    chars: list = field(default_factory=list)   # 渲染用 CharInstance 列表


@dataclass
class LayoutResult:
    pages: list[list[Line]]                  # 每页的行列表（横线由此生成）
    chars_by_page: list[list[CharInstance]]  # 每页的字符（渲染直接消费）
    total_pages: int
    para_page: dict                          # 段落序号 -> 首次出现页
    error_count: int = 0
    line_errors: list = field(default_factory=list)  # 与 pages 内行顺序平行的每行错字数


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _layout_from_word(paras, params: Params) -> LayoutResult:
    """Word 版式模式：不自行排版，直接消费 PDF 提取的逐字坐标表。

    Word 决定每个字在哪一行、什么位置（版式唯一真相），
    本函数把坐标表换算成渲染用的 CharInstance：
    - 基线 y 与 x 原位保留（誊写位置 = Word 内容框位置）；
    - 字形按 handwriting_scale 缩放（印刷满格，手写略小略散），
      以字符 bbox 水平中心为锚缩放，避免整体向左偏；
    - 错字/涂改在该模式下不启用（固定坐标行间未必放得下重写空间）；
    - 段落映射为尽力匹配（供中栏点击跳页），页眉页脚等额外文字归入"未匹配"。
    """
    pages_boxes = params.word_char_boxes
    total_pages = max(1, len(pages_boxes))
    chars_by_page: list[list[CharInstance]] = [[] for _ in range(total_pages)]
    pages: list[list[Line]] = [[] for _ in range(total_pages)]

    # 扁平化全文 -> 段落索引映射（尽力匹配用）
    flat: list[str] = []
    flat_para: list[int] = []
    for i, para in enumerate(paras):
        for ch in para.text:
            flat.append(ch)
            flat_para.append(i)

    hs = params.handwriting_scale
    seq = 0
    flat_ptr = 0
    para_page: dict = {}
    for pno, boxes in enumerate(pages_boxes or []):
        for b in boxes:
            ch = b["char"]
            if not ch.strip():
                continue
            # 尽力匹配段落：在扁平全文里向前找该字符（限窗口，防页眉页脚乱跳）
            pidx = None
            j = flat_ptr
            limit = min(len(flat), flat_ptr + 500)
            while j < limit:
                if flat[j] == ch:
                    pidx = flat_para[j]
                    flat_ptr = j + 1
                    break
                j += 1
            if pidx is not None and pidx not in para_page:
                para_page[pidx] = pno

            x0, _y0, x1, _y1 = b["bbox"]
            w = max(1.0, x1 - x0)
            cx = (x0 + x1) / 2.0
            chars_by_page[pno].append(CharInstance(
                char=ch,
                x=cx - w * hs / 2.0,   # 以 bbox 中心缩放后的左端
                y=b["y"],              # 基线不动：手写字仍"坐"在 Word 的基线上
                size=b["size"] * hs,
                page=pno,
                para_idx=pidx if pidx is not None else 0,
                seq=seq,
            ))
            seq += 1

    return LayoutResult(
        pages=pages,
        chars_by_page=chars_by_page,
        total_pages=total_pages,
        para_page=para_page,
        error_count=0,
    )


def _lines_auto_break(paras, params: Params, font_path, upem, error_flags,
                      avail_w: float) -> list[dict]:
    """自动断行：逐字符累积步进宽度组行（unit = CJK 单字 / 连续 ASCII 词）。"""
    base_px = params.font_px
    all_lines = []  # dict: units, width, indent, size, para_idx, align, is_last
    for pi, para in enumerate(paras):
        size = base_px * para.font_scale
        em2px = size / upem

        row = error_flags[pi]
        # 构建 unit 流
        units = []  # [(chars:[(ch, err, adv_px)], adv_total, breakable_before)]
        i = 0
        while i < len(row):
            ch, err = row[i]
            adv = glyph_engine.advance(font_path, ch) * em2px
            if _is_word_char(ch):
                j, adv_total, chars_u = i, 0.0, []
                while j < len(row) and _is_word_char(row[j][0]):
                    a = glyph_engine.advance(font_path, row[j][0]) * em2px
                    chars_u.append((row[j][0], row[j][1], a))
                    adv_total += a
                    j += 1
                units.append((chars_u, adv_total, False))
                i = j
            else:
                units.append(([(ch, err, adv)], adv, True))
                i += 1

        indent_px = (para.indent_pt if para.indent_pt is not None else 2.0 * size)
        if para.align == "center":
            indent_px = 0.0

        line = {"units": [], "width": indent_px, "indent": indent_px,
                "size": size, "para_idx": pi, "align": para.align}
        para_lines = [line]

        for chars_u, adv_total, breakable_before in units:
            overflow = line["width"] + adv_total > avail_w
            if overflow and line["units"]:
                # 英文词被劈开时整词回退
                if not breakable_before and line["units"][-1][0][0][1] is None:
                    last_chars, last_adv = line["units"][-1]
                    if _is_word_char(last_chars[0][0]):
                        line["units"].pop()
                        line["width"] -= last_adv
                        line = {"units": [(chars_u, adv_total)],
                                "width": adv_total, "indent": 0.0,
                                "size": size, "para_idx": pi,
                                "align": para.align}
                        para_lines.append(line)
                        continue
                    # 行尾是完整词（以标点结束）无法回退，直接换行
                line = {"units": [(chars_u, adv_total)], "width": adv_total,
                        "indent": 0.0, "size": size, "para_idx": pi,
                        "align": para.align}
                para_lines.append(line)
            else:
                line["units"].append((chars_u, adv_total))
                line["width"] += adv_total

        for li, ln in enumerate(para_lines):
            ln["is_last"] = (li == len(para_lines) - 1)
            all_lines.append(ln)
    return all_lines


def _lines_pre_broken(paras, params: Params, pre_broken_lines,
                      font_path, upem, error_flags) -> list[dict]:
    """预断行：编辑器视觉行 = 排版行真相（方案 B'）。

    每个视觉行原样照搬：行首空格、内部空格、断行位置全部保留；
    错字标记按段落全文顺序与视觉行字符一一对应消费。
    """
    all_lines = []
    base_px = params.font_px
    for pi, para in enumerate(paras):
        size = base_px * para.font_scale
        em2px = size / upem
        flags = error_flags[pi]
        vis = pre_broken_lines[pi] if pi < len(pre_broken_lines) else [""]
        fi = 0
        for line_text in vis:
            units = []
            width = 0.0
            for ch in line_text:
                err = flags[fi][1] if fi < len(flags) else None
                fi += 1
                adv = glyph_engine.advance(font_path, ch) * em2px
                units.append(([(ch, err, adv)], adv))
                width += adv
            all_lines.append({"units": units, "width": width, "indent": 0.0,
                              "size": size, "para_idx": pi, "align": "left",
                              "is_last": True})
    return all_lines


def layout_document(paras, params: Params, pre_broken_lines=None) -> LayoutResult:
    """排版主入口。paras: list[docx_parser.Para]

    layout_mode="word" 且已导入 Word 版式资产时走 Word 版式分支
    （排版交给 Word，本引擎只负责把坐标表变成渲染数据）；
    传入 pre_broken_lines（每个段落的视觉行列表，来自纸面编辑器）时
    断行以编辑器为准（方案 B'：所见即所得）；
    否则走自由版式两遍自动排版。
    """
    if params.layout_mode == "word" and getattr(params, "word_char_boxes", None):
        return _layout_from_word(paras, params)

    font_path = params.font_path
    m_left = params.margin_left_px
    m_right = params.margin_right_px
    avail_w = PAGE_W - m_left - m_right
    top = params.margin_top_px
    bottom = PAGE_H - params.margin_bottom_px
    upem = glyph_engine._load(font_path)[2]

    # ---------- 第一遍：错字抽样 ----------
    # 随机流 = seed + 标签 + 段落序号：与版式参数无关，改行距/页边距
    # 不会改变"哪些字写错"；同参数重排结果完全一致（预览 = 导出）。
    error_flags: list[list[tuple[str, str | None]]] = []
    for pi, para in enumerate(paras):
        rng = np.random.default_rng([params.seed, _ERR_TAG, pi])
        row = []
        for ch in para.text:
            if ch.strip() and rng.random() < params.error_rate:
                pool = [c for c in COMMON_CHARS if c != ch]
                wrong = str(rng.choice(pool))
                row.append((ch, wrong))
            else:
                row.append((ch, None))
        error_flags.append(row)

    # ---------- 第二遍 a：逐段组行（不涉及页） ----------
    if pre_broken_lines is not None:
        all_lines = _lines_pre_broken(paras, params, pre_broken_lines,
                                      font_path, upem, error_flags)
    else:
        all_lines = _lines_auto_break(paras, params, font_path, upem,
                                      error_flags, avail_w)

    # ---------- 第二遍 b：统一分页定位 ----------
    # 背景信纸横线（bg_rules，由 main 应用背景时从位图检测）非空时：
    # 一行文字对一条横线，基线吸附线上、行宽取横线 x 范围、
    # 超宽行等比缩小字号（下限 60%）；横线用完翻页，
    # 文档页数超出背景模板页数时按页取模循环复用模板。
    bg_rules = getattr(params, "bg_rules", None) or []
    cur_page = 0
    y = top
    rule_idx = 0
    lines: list[Line] = []
    anchors: list[tuple[float, float] | None] = []   # 与 lines 平行：(行首 x, 可用宽)
    for ln in all_lines:
        lh = ln["size"] * params.line_factor
        has_content = any(
            ch.strip()
            for chars_u, _adv in ln["units"]
            for ch, _err, _a in chars_u
        )
        rules = bg_rules[cur_page % len(bg_rules)] if bg_rules else None
        if rules and rule_idx >= len(rules):
            cur_page += 1            # 本页横线写满 -> 翻页
            rule_idx = 0
            y = top
            rules = bg_rules[cur_page % len(bg_rules)] if bg_rules else None
        if not rules and has_content and y + lh > bottom:
            cur_page += 1
            rule_idx = 0
            y = top
            rules = bg_rules[cur_page % len(bg_rules)] if bg_rules else None
        if rules:
            ry, x0, x1 = rules[rule_idx]
            size = ln["size"]
            avail = float(x1 - x0)
            content_w = ln["width"] - ln["indent"]
            if content_w > avail and content_w > 0:
                size = max(size * avail / content_w, size * 0.6)
            line = Line(page=cur_page, y_baseline=ry - size * 0.22,
                        rule_y=ry, para_idx=ln["para_idx"], size=size)
            anchors.append((float(x0), avail))
            rule_idx += 1
        else:
            line = Line(
                page=cur_page,
                y_baseline=y + ln["size"] * 0.85,
                rule_y=y + ln["size"] * 0.85 + ln["size"] * 0.22,
                para_idx=ln["para_idx"],
                size=ln["size"],
            )
            anchors.append(None)
            y += lh
        lines.append(line)

    total_pages = max((l.page for l in lines), default=0) + 1
    pages: list[list[Line]] = [[] for _ in range(total_pages)]
    for l in lines:
        pages[l.page].append(l)

    # ---------- 生成字符位置表（含对齐与均摊字距） ----------
    chars_by_page: list[list[CharInstance]] = [[] for _ in range(total_pages)]
    para_page: dict = {}
    line_errors: list[int] = []
    seq = 0
    # all_lines 与 lines 按顺序一一对应
    for line, ln, anchor in zip(lines, all_lines, anchors):
        k = line.size / ln["size"] if ln["size"] else 1.0   # 吸附缩字倍率（网格行恒为 1）
        content_w = (ln["width"] - ln["indent"]) * k
        ax, avail_l = anchor if anchor is not None else (m_left, avail_w)
        offset = 0.0
        if ln["align"] == "center":
            offset = (avail_l - content_w) / 2.0
        elif ln["align"] == "right":
            offset = avail_l - content_w
        n_units = len(ln["units"])
        extra = 0.0
        if ln["align"] == "justify" and not ln["is_last"] and n_units > 1:
            extra = max(0.0, (avail_l - content_w) / (n_units - 1))
        x = ax + ln["indent"] * k + offset
        size = line.size
        n_err_line = 0
        for chars_u, _adv in ln["units"]:
            for ch, err, adv in chars_u:
                if ch.strip():
                    ci = CharInstance(
                        char=ch, x=x, y=line.y_baseline, size=size,
                        page=line.page, para_idx=line.para_idx, seq=seq,
                        is_error=err is not None, error_glyph=err or "",
                    )
                    chars_by_page[line.page].append(ci)
                    if err is not None:
                        n_err_line += 1
                    if line.para_idx not in para_page:
                        para_page[line.para_idx] = line.page
                    seq += 1
                x += adv * k + extra
        line_errors.append(n_err_line)

    error_count = sum(1 for page in chars_by_page for c in page if c.is_error)

    # ---------- 错字补写（右上方重写：占行间空隙，不占排版宽度） ----------
    if params.rewrite_mode.startswith("右上方"):
        for page in chars_by_page:
            for ci in list(page):
                if not ci.is_error:
                    continue
                gap = ci.size * params.line_factor - ci.size
                rw_size = max(min(ci.size * 0.7, gap * 0.8), ci.size * 0.35)
                page.append(CharInstance(
                    char=ci.error_glyph,
                    x=min(ci.x + ci.size * 0.62, PAGE_W - m_right - rw_size),
                    y=ci.y - ci.size * 0.30,
                    size=rw_size, page=ci.page, para_idx=ci.para_idx,
                    seq=ci.seq, is_rewrite=True,
                ))

    return LayoutResult(
        pages=pages,
        chars_by_page=chars_by_page,
        total_pages=total_pages,
        para_page=para_page,
        error_count=error_count,
        line_errors=line_errors,
    )
