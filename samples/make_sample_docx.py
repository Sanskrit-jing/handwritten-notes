"""生成测试用 .docx：标题 + 缩进段落 + 居中段 + 中英混排，覆盖排版分支。"""
import os

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

OUT = os.path.join(os.path.dirname(__file__), "示例文档.docx")

doc = Document()

h = doc.add_paragraph("手写记录软件测试文档", style="Heading 1")
h.alignment = WD_ALIGN_PARAGRAPH.CENTER

doc.add_paragraph(
    "这是一段用于测试的正文。手写记录软件会把 Word 文档中的文字自动誊写到纸张上，"
    "横线纸的每一道横线都来自排版引擎生成的行位置，文字自然落在横线上，"
    "因此不需要像传统手写模拟器那样手动调整对齐参数。"
)
doc.add_paragraph(
    "本段包含一些英文与数字，例如 Python 3.14、A4 paper 210mm，"
    "用来验证中英文混排时的断行策略：中文逐字换行，英文单词整体回退，"
    "避免出现单词被从中间劈开的情况。"
)
p = doc.add_paragraph("这一行是居中对齐的段落，用于验证对齐功能。")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_paragraph(
    "当把错字率滑条调高时，誊写过程会随机写错一些字：先用涂改方式把错字划掉，"
    "再按照重写方式在错字的右上方写一个缩小一号的正确字。"
    "行距保持一点六倍以上时，行间空隙足以容纳补写的字，不会发生重叠。"
)
doc.add_paragraph("")

doc.add_paragraph(
    "笔画扰动会让每一个笔画独立发生微小的位移和旋转：水平位移默认两像素，"
    "竖直位移两像素，旋转幅度零点零五弧度。同一个字每出现一次都独立采样，"
    "所以全文找不到两个完全相同的字，这是手写感的关键。"
)

for run in doc.paragraphs[1].runs:
    run.font.size = Pt(12)

doc.save(OUT)
print("已生成:", OUT)
