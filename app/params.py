"""全局参数定义与页面常量。

排版坐标基准：A4 @ 300 DPI（2480 x 3508 px）。
预览按 120 DPI 渲染，导出按 300 DPI 渲染，坐标乘以 dpi/300 即可换算。
"""
import json
import os
import traceback
from dataclasses import MISSING, dataclass, field, fields

from app.fonts import PROJECT_FONTS_DIR
from app.paths import DATA_DIR, RESOURCE_DIR

PAGE_W = 2480   # A4 宽 @300dpi
PAGE_H = 3508   # A4 高 @300dpi
DPI_BASE = 300
DPI_PREVIEW = 150  # 预览分辨率：过低会把细笔画平均成浅灰

# 配置文件：数据根 config.json（便携式，源码=项目根；冻结=exe 目录，不入库）
PROJECT_DIR = DATA_DIR
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")

INK_COLORS = {
    "碳黑": "#1A1A1A",
    "蓝黑": "#2B3A67",
    "纯蓝": "#1F4FD8",
}

PAPER_STYLES = ["横线纸", "空白页", "方格纸"]
REWRITE_MODES = ["右上方重写", "不重写（仅涂改）"]
STRIKE_MODES = ["单横线", "双横线", "涂黑团"]

# 出厂默认值对应用户截图：水平位移=2、竖直位移=2、旋转=0.05、错字率=0%、右上方重写、单横线


def _default_font() -> tuple:
    """默认字体：项目 fonts/ 目录下的云烟体（手写感优先），没有则楷体。"""
    try:
        for fn in sorted(os.listdir(PROJECT_FONTS_DIR)):
            if "云烟" in fn and fn.lower().endswith((".ttf", ".ttc", ".otf")):
                return os.path.join(PROJECT_FONTS_DIR, fn), \
                    os.path.splitext(fn)[0]
    except OSError:
        pass
    return r"C:\Windows\Fonts\simkai.ttf", "楷体"


def _default_bg() -> str:
    """默认背景：内置黑色信纸（优先图片版，秒加载免 Word；都没有则无背景）。"""
    base = os.path.join(RESOURCE_DIR, "backgrounds", "实线信纸模板（黑色）")
    for ext in (".png", ".docx"):
        if os.path.exists(base + ext):
            return base + ext
    return ""


@dataclass
class Params:
    # ---- 文档与版式（重排类：改动触发全文重排）----
    font_path: str = field(default_factory=lambda: _default_font()[0])
    font_name: str = field(default_factory=lambda: _default_font()[1])
    font_size_pt: float = 12.0
    line_factor: float = 1.6
    # 四向页边距（mm），默认 Word"普通"边距：上下 25.4、左右 31.8
    margin_top_mm: float = 25.4
    margin_bottom_mm: float = 25.4
    margin_left_mm: float = 31.8
    margin_right_mm: float = 31.8
    paper: str = "横线纸"
    error_rate: float = 0.0

    # ---- 笔画扰动与涂改（重渲染类：布局不变，仅重画）----
    dx_amp: float = 2.0      # 水平笔画位移幅度 px @300dpi
    dy_amp: float = 2.0      # 竖直笔画位移幅度
    rot_amp: float = 0.05    # 笔画旋转幅度 rad
    rewrite_mode: str = "右上方重写"
    strike_mode: str = "单横线"
    ink_color: str = "碳黑"
    stroke_weight: float = 1.0   # 笔迹粗细倍率（绕笔画质心外扩轮廓，模拟用力书写）

    seed: int = 42

    # ---- 背景模板（Word/图片转整页位图铺底，重渲染类）----
    bg_name: str = ""
    bg_images: list = field(default_factory=list)   # [PIL.Image @300dpi]，每页一张
    # 信纸横线表：每页 [(y, x0, x1)] @300dpi，应用背景时从位图检测；
    # 非空时排版引擎"一行对一线"吸附横线（版式类：触发全量重排）
    bg_rules: list = field(default_factory=list)

    # ---- Word 版式模式（layout_mode="word"）：排版交给 Word，程序按坐标誊写 ----
    layout_mode: str = "free"        # "free"=程序自排版 | "word"=Word 决定每个字的位置
    handwriting_scale: float = 0.9   # 手写缩放：印刷字满格，手写略小略散
    word_char_boxes: list = field(default_factory=list)
    # 每页一个列表，元素: {char, x, y, size, bbox} @300dpi 像素
    # x/y 为基线左端与基线位置；bbox=(x0,y0,x1,y1) 用于水平居中缩放

    # ---- 会话状态（随配置保存；"恢复默认"时保持不变）----
    doc_path: str = ""          # 当前文档路径（启动时自动重新加载）
    # 背景模板路径（docx 或图片）；默认 = 内置黑色信纸（冷启动即铺信纸）
    bg_template_path: str = field(default_factory=_default_bg)
    # 路径记忆：各文件对话框从上次位置打开
    last_dir_doc: str = ""
    last_dir_bg: str = ""
    last_dir_font: str = ""
    last_dir_export: str = ""

    # 版本号：layout 变化会使 render 缓存全部失效
    layout_version: int = 0
    render_version: int = 0

    def mark_layout_changed(self):
        self.layout_version += 1
        self.render_version += 1

    def mark_render_changed(self):
        self.render_version += 1

    def to_config(self) -> dict:
        """可持久化字段快照（白名单：仅标量与路径，不含图像/坐标大对象）。"""
        return {k: getattr(self, k) for k in _SAVE_FIELDS}

    def apply_config(self, cfg: dict):
        """应用配置 dict（只覆盖 _SAVE_FIELDS 中存在的键）。

        字体路径在当前机器不存在（如配置来自别的机器）时回落默认字体。
        """
        for k in _SAVE_FIELDS:
            if k in cfg:
                setattr(self, k, cfg[k])
        if not os.path.exists(self.font_path):
            self.font_path, self.font_name = _default_font()

    def reset_to_defaults(self):
        """排版/手写参数恢复出厂默认；会话状态（文档、背景、目录记忆）不变。"""
        for f in fields(self):
            if f.name in _RESET_KEEP:
                continue
            if f.default_factory is not MISSING:
                default = f.default_factory()
            elif f.default is not MISSING:
                default = f.default
            else:
                continue
            setattr(self, f.name, default)

    @property
    def font_px(self) -> float:
        """字号 pt -> px @300dpi。"""
        return self.font_size_pt / 72.0 * DPI_BASE

    @property
    def line_height(self) -> float:
        return self.font_px * self.line_factor

    @property
    def margin_top_px(self) -> float:
        return self.margin_top_mm / 25.4 * DPI_BASE

    @property
    def margin_bottom_px(self) -> float:
        return self.margin_bottom_mm / 25.4 * DPI_BASE

    @property
    def margin_left_px(self) -> float:
        return self.margin_left_mm / 25.4 * DPI_BASE

    @property
    def margin_right_px(self) -> float:
        return self.margin_right_mm / 25.4 * DPI_BASE


# 参与保存/恢复的字段白名单（仅 JSON 标量；bg_images/word_char_boxes 等大对象不存）
_SAVE_FIELDS = [
    "font_path", "font_name", "font_size_pt", "line_factor",
    "margin_top_mm", "margin_bottom_mm", "margin_left_mm", "margin_right_mm",
    "paper", "error_rate", "dx_amp", "dy_amp", "rot_amp",
    "rewrite_mode", "strike_mode", "ink_color", "stroke_weight",
    "layout_mode", "handwriting_scale",
    "doc_path", "bg_template_path",
    "last_dir_doc", "last_dir_bg", "last_dir_font", "last_dir_export",
]

# "恢复默认"时保持不变的会话状态字段
_RESET_KEEP = {
    "doc_path", "bg_template_path",
    "last_dir_doc", "last_dir_bg", "last_dir_font", "last_dir_export",
    "bg_name", "bg_images", "bg_rules", "word_char_boxes",
    "layout_version", "render_version",
}


def load_config() -> dict:
    """读取 config.json；不存在或损坏时返回 {}（打印原因，不静默吞错）。"""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        traceback.print_exc()   # 保存失败打印到控制台，不静默吞错
