"""docx 文档解析层：把 Word 文档变成排版引擎可消费的段落列表。

"Word 为背景"的第一步：解析出每个段落的文字、对齐方式、首行缩进，
让排版引擎能按原文档的版面结构誊写到纸上。
"""
from dataclasses import dataclass

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH


@dataclass
class Para:
    text: str
    align: str = "justify"        # left / center / right / justify
    indent_pt: float | None = None  # 首行缩进 pt；None 表示按中文习惯缩进两字
    font_scale: float = 1.0       # 标题段落整体字号放大系数


_ALIGN_MAP = {
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
}

_HEADING_SCALE = [
    (("Heading 1", "标题 1"), 1.5),
    (("Heading 2", "标题 2"), 1.3),
    (("Heading 3", "标题 3"), 1.15),
]


def _heading_scale(style_name: str) -> float:
    for names, scale in _HEADING_SCALE:
        if style_name in names:
            return scale
    return 1.0


def parse_docx(path: str) -> list[Para]:
    """解析 .docx，返回段落列表。空段落保留（作为空行誊写）。"""
    doc = Document(path)
    paras: list[Para] = []
    for p in doc.paragraphs:
        text = p.text
        align = _ALIGN_MAP.get(p.alignment, "justify")
        style_name = p.style.name if p.style is not None else ""

        # 首行缩进：优先取 Word 里的显式设置；否则按中文正文习惯缩进两字
        fli = p.paragraph_format.first_line_indent
        indent_pt = fli.pt if fli is not None else None

        paras.append(Para(
            text=text,
            align=align,
            indent_pt=indent_pt,
            font_scale=_heading_scale(style_name),
        ))
    return paras
