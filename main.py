"""手写记录软件 - 三栏主界面。

左栏：预览（QGraphicsView 多页连续视图，缩放/翻页/点击联动）
中栏：双形态——自由排版=纸面编辑器（所见即所得排版，红框预览 1:1 照搬
      其断行/缩进/空格）；Word 版式=Word 版面对照页（保存后自动同步誊写）
右栏：参数面板（截图功能全覆盖，参数分两类决定增量刷新路径）

刷新模型（对应设计第 4 段）：
- 重排类参数（字体/字号/行距/页边距/错字率/文档）-> 全文重排 + 全页重渲染
- 重渲染类参数（扰动三项/涂改方式/墨色/纸张）-> 布局不变仅重画
- 所有参数改动 debounce 300ms；渲染在后台线程排队，可见页优先
- 随机流 = seed + 页码 + 字符序号：参数变化后笔迹依然确定，预览 = 导出
"""
import copy
import ctypes
import os
import queue
import random
import sys
import time
import traceback

from PyQt6.QtCore import QEvent, QFileSystemWatcher, Qt, QThread, \
    QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap, \
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsView, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QStackedWidget,
    QStyle, QTextEdit, QVBoxLayout, QWidget,
)

from app.bg_template import builtin_backgrounds, detect_rule_lines, \
    docx_to_page_images, docx_to_word_assets, load_image_background, \
    save_background_template, user_backgrounds
from app.docx_parser import Para, parse_docx
from app.exporter import export_pdf, export_png
from app.fonts import PROJECT_FONTS_DIR, available_fonts, font_family_name
from app.layout_engine import layout_document
from app.params import (
    DPI_BASE, DPI_PREVIEW, INK_COLORS, PAPER_STYLES, Params,
    REWRITE_MODES, STRIKE_MODES, load_config, save_config,
)
from app.renderer import render_page

GAP = 36  # 预览页间距（场景像素）

# 界面主题：浅灰绿底 + 白色圆角分组卡片 + 绿色主操作按钮
# （参考 Handwriting-simulator 的 UI 主题风格）
THEME_QSS = """
* { font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; }
QMainWindow { background: #EFF1EA; }
QSplitter::handle { background: #EFF1EA; }
QGroupBox {
    background: #FFFFFF;
    border: 1px solid #E1E6D8;
    border-radius: 10px;
    margin-top: 15px;
    padding: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    top: 3px;
    padding: 0 4px;
    color: #44573C;
    font-weight: 600;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #FFFFFF;
    border: 1px solid #D4DAC9;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: #A9C79A;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #6FA25B; }
QComboBox::drop-down { border: none; width: 18px; }
QPushButton {
    background: #FFFFFF;
    border: 1px solid #D4DAC9;
    border-radius: 4px;
    padding: 5px 12px;
    color: #333333;
}
QPushButton:hover { border-color: #6FA25B; color: #44702F; }
QPushButton:pressed { background: #F0F5EA; }
QPushButton#primary {
    background: #6FA25B;
    color: #FFFFFF;
    border: none;
    font-weight: 600;
}
QPushButton#primary:hover { background: #7CB067; }
QPushButton#primary:pressed { background: #628F4F; }
QSlider { min-height: 20px; }
QSlider::groove:horizontal { height: 4px; background: #D9DFD2; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #A9C79A; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: #6FA25B;
}
QSlider::handle:horizontal:hover { background: #7CB067; }
QTextEdit {
    background: #FFFFFF;
    border: 1px solid #E1E6D8;
    border-radius: 10px;
    padding: 4px;
    selection-background-color: #DCEBD2;
}
QLabel { color: #333333; }
QScrollArea { border: none; background: transparent; }

/* ---- 数字调节框：扁平上下按钮（替换原生凸起小方块） ---- */
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: transparent;
    border: none;
    width: 15px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: #E7ECDF;
    border-radius: 3px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url(@UP@); width: 8px; height: 6px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url(@DOWN@); width: 8px; height: 6px;
}

/* ---- 下拉框：柔和箭头 + 弹出列表样式 ---- */
QComboBox::down-arrow {
    image: url(@DOWN@); width: 10px; height: 7px;
    margin-right: 5px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #D4DAC9;
    border-radius: 6px;
    selection-background-color: #E7ECDF;
    selection-color: #333333;
    outline: none;
}

/* ---- 滚动条：细圆角浅色 ---- */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #C7CFC0; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #A9B6A0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #C7CFC0; border-radius: 3px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #A9B6A0; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 菜单 / 提示 / 弹窗 / 进度条 ---- */
QMenu { background: #FFFFFF; border: 1px solid #D4DAC9; border-radius: 6px; padding: 4px; }
QMenu::item { padding: 5px 24px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: #E7ECDF; color: #44702F; }
QMenu::separator { height: 1px; background: #E1E6D8; margin: 4px 8px; }
QToolTip { background: #FFFFFF; color: #333333; border: 1px solid #D4DAC9; padding: 4px 6px; }
QDialog { background: #FFFFFF; }
QProgressBar {
    background: #FFFFFF;
    border: 1px solid #D4DAC9;
    border-radius: 4px;
    text-align: center;
    color: #333333;
}
QProgressBar::chunk { background: #6FA25B; border-radius: 3px; }

/* ---- 预览工具栏分段按钮（适宽/适页/100%，选中态高亮） ---- */
QPushButton[seg="true"] {
    background: #FFFFFF;
    border: 1px solid #D4DAC9;
    border-radius: 3px;
    padding: 3px 10px;
    color: #556052;
    font-size: 12px;
}
QPushButton[seg="true"]:hover { border-color: #6FA25B; color: #44702F; }
QPushButton[seg="true"]:checked {
    background: #6FA25B;
    color: #FFFFFF;
    border-color: #6FA25B;
    font-weight: 600;
}
"""


def _arrow_png(name: str, up: bool, w: int, h: int) -> str:
    """生成抗锯齿三角箭头 PNG 到临时目录，返回正斜杠路径（QSS image: 用）。

    Qt6 QSS 的 border-trick 画不出三角（实测渲染为实心方块），
    运行时生成小图是唯一稳定方案。PIL 4x 超采样 + LANCZOS 缩小抗锯齿。
    """
    import tempfile
    from PIL import Image, ImageDraw
    path = os.path.join(tempfile.gettempdir(), name)
    s = 4
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = (138, 152, 132, 255)          # #8A9884
    if up:
        pts = [(0.15, 0.8), (0.85, 0.8), (0.5, 0.2)]
    else:
        pts = [(0.15, 0.2), (0.85, 0.2), (0.5, 0.8)]
    d.polygon([(x * w * s, y * h * s) for x, y in pts], fill=c)
    img.resize((w, h), Image.LANCZOS).save(path)
    return path.replace("\\", "/")


def theme_qss_with_arrows() -> str:
    """THEME_QSS 中 @UP@/@DOWN@ 占位符替换为运行时生成的三角箭头图。"""
    return THEME_QSS.replace("@UP@", _arrow_png("hw_arrow_up.png", True, 8, 6)) \
                    .replace("@DOWN@", _arrow_png("hw_arrow_down.png", False, 10, 7))


def pil_to_qimage(img) -> QImage:
    img = img.convert("RGBA")
    w, h = img.width, img.height
    return QImage(img.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


# ================= 渲染后台线程 =================

class RenderWorker(QThread):
    rendered = pyqtSignal(int, tuple, QImage)
    render_failed = pyqtSignal(str)   # 渲染异常上浮（如字体加载失败）

    def __init__(self):
        super().__init__()
        self._queue: queue.Queue = queue.Queue()
        self._alive = True

    def submit(self, jobs):
        self._clear()
        for j in jobs:
            self._queue.put(j)

    def _clear(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self):
        self._alive = False
        self.wait(1500)

    def run(self):
        while self._alive:
            try:
                job = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue
            page, key, layout, params = job
            try:
                img = render_page(layout, params, page, dpi=DPI_PREVIEW, ss=2)
                self.rendered.emit(page, key, pil_to_qimage(img))
            except Exception as e:
                traceback.print_exc()
                self.render_failed.emit(str(e))


# ================= 左栏：预览 =================

class ClickSlider(QSlider):
    """点击即定位的水平滑块。

    QSlider 默认点击 = 翻一页（page step），行程短时一点就跳到端点；
    这里改为"点到哪、滑块到哪"，并自行维护拖动状态。
    """

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
            self._jump_to(e.position().x())
            self.setSliderDown(True)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            e.accept()
            self._jump_to(e.position().x())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            e.accept()
            self.setSliderDown(False)
        else:
            super().mouseReleaseEvent(e)

    def _jump_to(self, x: float):
        v = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), round(x), max(1, self.width()))
        self.setValue(v)


class PreviewPane(QWidget):
    page_clicked = pyqtSignal(int)   # 点击某页 -> 联动中栏
    page_changed = pyqtSignal(int)   # 当前可见页变化
    mode_changed = pyqtSignal(str)           # 版式模式切换（"free" / "word"）
    bg_import_requested = pyqtSignal()       # 导入背景模板（Word/图片）
    bg_builtin_requested = pyqtSignal(str)   # 使用下拉选中的背景模板
    bg_clear_requested = pyqtSignal()        # 清除背景模板
    bg_save_requested = pyqtSignal()         # 把当前 Word 背景存为图片模板

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_w = round(210 / 25.4 * DPI_PREVIEW)   # 992
        self._page_h = round(297 / 25.4 * DPI_PREVIEW)   # 1403
        self._items: list = []

        self.view = QGraphicsView(self)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 浅暖灰画布 + 页面投影：页面像纸浮在桌面上（Word/WPS 视觉），
        # 适宽模式下底部露出的画布不再是一块生硬灰条
        self.view.setBackgroundBrush(QColor(216, 221, 210))
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.mousePressEvent = self._on_press
        self.view.verticalScrollBar().valueChanged.connect(self._track_page)

        bar = QHBoxLayout()
        bar.setContentsMargins(11, 0, 0, 0)   # 左缩进：与预览页左缘对齐
        self.btn_fit_w = QPushButton("适宽")
        self.btn_fit_p = QPushButton("适页")
        self.btn_100 = QPushButton("100%")
        self.spin_page = QSpinBox()
        self.spin_page.setRange(1, 1)
        self.spin_page.setFixedWidth(58)
        self.spin_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_total = QLabel("/ 1")
        for w in (self.btn_fit_w, self.btn_fit_p, self.btn_100):
            w.setProperty("seg", True)     # 分段按钮样式（QSS）
            w.setCheckable(True)
            w.setAutoExclusive(True)
            bar.addWidget(w)
        self.btn_fit_w.setChecked(True)    # showEvent 默认适宽
        bar.addStretch(1)
        bar.addWidget(QLabel("页码"))
        bar.addWidget(self.spin_page)
        bar.addWidget(self.lbl_total)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(bar)

        # ---- 第二行工具栏：版式模式与背景模板（自右栏上移，右栏少一组）----
        bar2 = QHBoxLayout()
        bar2.setContentsMargins(11, 0, 0, 0)  # 左缩进：与预览页左缘对齐
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["自由排版", "Word 版式"])
        self.cmb_mode.setToolTip(
            "Word 版式：排版完全交给 Word，预览在 Word 排好的位置上手写誊写，\n"
            "版式与 Word 打印内容框一模一样")
        self.btn_bg = QPushButton("导入模板…")
        self.cmb_builtin_bg = QComboBox()
        self.btn_bg_save = QPushButton("存为模板")
        self.btn_bg_save.setToolTip(
            "把当前 Word 信纸背景存为图片模板，下次选用免 Word 转换")
        self.btn_bg_save.setEnabled(False)
        self.btn_bg_clear = QPushButton("清除背景")
        bar2.addWidget(QLabel("模式"))
        bar2.addWidget(self.cmb_mode)
        bar2.addWidget(self.btn_bg)
        bar2.addWidget(self.cmb_builtin_bg)
        bar2.addWidget(self.btn_bg_save)
        bar2.addWidget(self.btn_bg_clear)
        bar2.addStretch(1)
        layout.addLayout(bar2)
        layout.addWidget(self.view)

        self.btn_fit_w.clicked.connect(self.fit_width)
        self.btn_fit_p.clicked.connect(self.fit_page)
        self.btn_100.clicked.connect(self.zoom_100)
        self.spin_page.valueChanged.connect(self._goto_page)
        self.btn_bg.clicked.connect(self.bg_import_requested.emit)
        self.btn_bg_save.clicked.connect(self.bg_save_requested.emit)
        self.btn_bg_clear.clicked.connect(self.bg_clear_requested.emit)
        self.cmb_builtin_bg.currentIndexChanged.connect(self._on_builtin_bg)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_ui)

        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        self.refresh_bg_combo()

    # ---- 版式模式与背景模板 ----

    def _on_mode_ui(self, idx: int):
        self.mode_changed.emit("word" if idx == 1 else "free")

    def set_mode(self, mode: str):
        """外部（启动恢复）同步版式模式选中项，不发信号。"""
        idx = 1 if mode == "word" else 0
        if self.cmb_mode.currentIndex() != idx:
            self.cmb_mode.blockSignals(True)
            self.cmb_mode.setCurrentIndex(idx)
            self.cmb_mode.blockSignals(False)

    def _on_builtin_bg(self):
        path = self.cmb_builtin_bg.currentData()
        if path:
            self.bg_builtin_requested.emit(path)
        else:
            self.bg_clear_requested.emit()   # （无）= 不用信纸背景

    def refresh_bg_combo(self, select_path: str | None = None):
        """重建背景模板下拉：（无）+ 内置信纸(docx) + 分隔 + 我的模板(图片)。

        select_path 给定时定位选中该模板；否则尽量保持当前选中项。
        """
        cmb = self.cmb_builtin_bg
        cmb.blockSignals(True)
        cur = cmb.currentData()
        cmb.clear()
        cmb.addItem("（无）", None)
        for name, path in builtin_backgrounds():
            cmb.addItem(name, path)
        users = user_backgrounds()
        if users:
            cmb.insertSeparator(cmb.count())
            for name, path in users:
                cmb.addItem(name, path)
        target = select_path if select_path is not None else cur
        if target:
            idx = cmb.findData(target)
            if idx >= 0:
                cmb.setCurrentIndex(idx)
        cmb.blockSignals(False)

    def reset_builtin_bg(self):
        """外部导入/清除背景后，把内置信纸下拉复位到（无），不触发信号。"""
        if self.cmb_builtin_bg.currentIndex() != 0:
            self.cmb_builtin_bg.blockSignals(True)
            self.cmb_builtin_bg.setCurrentIndex(0)
            self.cmb_builtin_bg.blockSignals(False)

    # ---- 页面管理 ----

    def set_pages(self, total: int):
        self.scene.clear()
        self._items = []
        blank = QPixmap(self._page_w, self._page_h)
        blank.fill(QColor(238, 240, 243))
        for i in range(total):
            it = self.scene.addPixmap(blank)
            it.setPos(0, i * (self._page_h + GAP))
            it.setGraphicsEffect(self._page_shadow())   # 页面投影
            self._items.append(it)
        self.spin_page.blockSignals(True)
        self.spin_page.setRange(1, max(1, total))
        self.spin_page.blockSignals(False)
        self.lbl_total.setText(f"/ {total}")

    @staticmethod
    def _page_shadow() -> QGraphicsDropShadowEffect:
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(18)
        eff.setOffset(0, 2)
        eff.setColor(QColor(60, 70, 55, 80))
        return eff

    def set_page_image(self, page: int, qimg: QImage):
        if 0 <= page < len(self._items):
            self._items[page].setPixmap(QPixmap.fromImage(qimg))

    def goto_page(self, page: int):
        self.spin_page.setValue(page)

    def _goto_page(self, p: int):
        if 0 <= p - 1 < len(self._items):
            self.view.ensureVisible(self._items[p - 1].sceneBoundingRect(),
                                    0, 80)

    def current_page(self) -> int:
        center = self.view.mapToScene(self.view.viewport().rect()).boundingRect().center().y()
        idx = int(center // (self._page_h + GAP))
        return max(0, min(len(self._items) - 1, idx))

    def _track_page(self):
        self.spin_page.blockSignals(True)
        self.spin_page.setValue(self.current_page() + 1)
        self.spin_page.blockSignals(False)
        self.page_changed.emit(self.current_page())

    # ---- 缩放 ----

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.view.scale(factor, factor)
        else:
            super().wheelEvent(e)

    def fit_width(self):
        self.view.resetTransform()
        s = (self.view.viewport().width() - 24) / self._page_w
        self.view.scale(s, s)

    def fit_page(self):
        self.view.resetTransform()
        s = min((self.view.viewport().width() - 24) / self._page_w,
                (self.view.viewport().height() - 24) / self._page_h)
        self.view.scale(s, s)

    def zoom_100(self):
        self.view.resetTransform()

    def showEvent(self, e):
        super().showEvent(e)
        self.fit_width()

    def _on_press(self, ev):
        pos = self.view.mapToScene(ev.position().toPoint())
        idx = int(pos.y() // (self._page_h + GAP))
        if 0 <= idx < len(self._items):
            self.page_clicked.emit(idx)
        QGraphicsView.mousePressEvent(self.view, ev)


# ================= 中栏：Word 版面对照 =================

class ComparePane(QWidget):
    """Word 版式模式下的中栏：显示 Word 原版面页，与左侧手写预览同大同位对应。

    版面位图与手写渲染的纸面来自同一份 PDF（bg_images），所以两边
    每个字的位置天然一一对应；翻页与预览双向联动。
    """

    page_selected = pyqtSignal(int)   # 用户翻页 -> 预览跳页（0 基）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._imgs: list = []      # PIL 原图（300dpi），按需转换当前页
        self._page = 0

        bar = QHBoxLayout()
        title = QLabel("Word 版面对照")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        self.spin_page = QSpinBox()
        self.spin_page.setRange(1, 1)
        self.lbl_total = QLabel("/ 0")
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(QLabel("页码"))
        bar.addWidget(self.spin_page)
        bar.addWidget(self.lbl_total)

        self.lbl_sync = QLabel("")
        self.lbl_sync.setStyleSheet("color:#8A9884; font-size:11px;")

        self.lbl_empty = QLabel(
            "导入 Word 文档后，此处显示 Word 原版面，\n"
            "与左侧手写预览逐字位置一一对应；\n"
            "在 Word 中修改并保存后，这里会自动同步。")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet("color:#8A9884;")

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.viewport().setStyleSheet("background:#D8DDD2;")   # 与预览画布同色
        self.lbl_img = QLabel()
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(60, 70, 55, 80))
        self.lbl_img.setGraphicsEffect(shadow)
        self.scroll.setWidget(self.lbl_img)
        self.scroll.viewport().installEventFilter(self)

        self.body = QStackedWidget()
        self.body.addWidget(self.lbl_empty)   # index 0：空态
        self.body.addWidget(self.scroll)      # index 1：版面页

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)
        lay.addLayout(bar)
        lay.addWidget(self.lbl_sync)
        lay.addWidget(self.body)

        self.spin_page.valueChanged.connect(self._on_spin)

    # ---- 数据与翻页 ----

    def set_images(self, imgs: list):
        """传入整份 Word 版面位图（PIL），只存引用，翻页时按需转换当前页。"""
        self._imgs = imgs
        n = len(imgs)
        self._page = 0
        self.spin_page.blockSignals(True)
        self.spin_page.setRange(1, max(1, n))
        self.spin_page.setValue(1)
        self.spin_page.blockSignals(False)
        self.lbl_total.setText(f"/ {n}")
        self.body.setCurrentIndex(1 if n else 0)
        self._rescale()

    def set_page(self, p: int):
        """外部（预览滚动）联动翻页，不回发信号避免循环。"""
        if not self._imgs:
            return
        p = max(0, min(len(self._imgs) - 1, p))
        changed = p != self._page
        self._page = p
        self.spin_page.blockSignals(True)
        self.spin_page.setValue(p + 1)
        self.spin_page.blockSignals(False)
        if changed:
            self.scroll.verticalScrollBar().setValue(0)   # 翻页回到页首
            self._rescale()

    def set_sync_status(self, text: str):
        self.lbl_sync.setText(text)

    def _on_spin(self, v: int):
        if self._imgs:
            self._page = max(0, min(len(self._imgs) - 1, v - 1))
        self._rescale()
        self.page_selected.emit(self._page)

    # ---- 缩放显示：按宽度适配，与预览"适宽"同比例 ----

    def _rescale(self):
        if not self._imgs or self._page >= len(self._imgs):
            self.lbl_img.clear()
            return
        vw = max(80, self.scroll.viewport().width() - 20)
        qimg = pil_to_qimage(self._imgs[self._page])
        scaled = qimg.scaledToWidth(vw, Qt.TransformationMode.SmoothTransformation)
        self.lbl_img.setPixmap(QPixmap.fromImage(scaled))
        self.lbl_img.resize(scaled.size())

    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport() and ev.type() == QEvent.Type.Resize:
            self._rescale()
        return super().eventFilter(obj, ev)


# ================= 中栏：纸面编辑器（自由排版模式） =================

def _err_selection(cur: QTextCursor) -> QTextEdit.ExtraSelection:
    """整行淡黄高亮的 ExtraSelection。"""
    sel = QTextEdit.ExtraSelection()
    sel.cursor = cur
    fmt = QTextCharFormat()
    fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
    fmt.setBackground(QColor(255, 246, 199))
    sel.format = fmt
    return sel


class PaperEditor(QTextEdit):
    """纸面编辑器：内容与断行真相（方案 B'）。

    显示样式固定（宋体、固定行距边距），不随右栏参数变化；
    用户在这里断行、留空行，左栏手写预览按参数样式逐行照搬内容。
    """

    edited = pyqtSignal()   # 用户真实编辑（程序写入不触发）

    def __init__(self, params: Params, parent=None):
        super().__init__(parent)
        self._params = params
        self._guard = False      # 程序写入时不触发 edited
        self._last_wrap = -1
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(0)
        self.textChanged.connect(self._on_text_changed)
        self._rewrap_timer = QTimer(self)
        self._rewrap_timer.setSingleShot(True)
        self._rewrap_timer.setInterval(150)
        self._rewrap_timer.timeout.connect(self._on_rewrap)
        self.apply_geometry()

    # ---- 几何：字号 / 行距 / 页边距 / 换行宽 ----

    def _editor_font(self) -> QFont:
        # 编辑器显示样式固定：宋体 12pt（16px@96dpi），不随右栏参数变化
        f = QFont("宋体")
        f.setPixelSize(16)
        return f

    def apply_geometry(self) -> int:
        """编辑器固定几何：宋体、固定边距/行高/换行宽，与右栏参数无关。

        返回本次生效的换行宽（px@96dpi），553px ≈ A4 内容区逻辑宽度。
        """
        self._guard = True
        try:
            self.setViewportMargins(26, 24, 26, 0)
            f = self._editor_font()
            self.setFont(f)
            self.document().setDefaultFont(f)
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)
            bf = cursor.blockFormat()
            bf.setLineHeight(26, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
            cursor.setBlockFormat(bf)
            wrap = max(120, min(553, self.viewport().width()))
            self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
            self.setLineWrapColumnOrWidth(wrap)
        finally:
            self._guard = False
        return wrap

    # ---- 视觉行提取（排版引擎预断行输入）与段落读写 ----

    def visual_lines(self) -> list[list[str]]:
        """每段的视觉行列表（块 layout 的真实断行结果）。"""
        out: list[list[str]] = []
        b = self.document().firstBlock()
        while b.isValid():
            text = b.text()
            lay = b.layout()
            rows = []
            for i in range(lay.lineCount()):
                ln = lay.lineAt(i)
                rows.append(text[ln.textStart():ln.textStart() + ln.textLength()])
            out.append(rows)
            b = b.next()
        return out

    def paragraphs_text(self) -> list[str]:
        out = []
        b = self.document().firstBlock()
        while b.isValid():
            out.append(b.text())
            b = b.next()
        return out

    def build_paras(self) -> list[Para]:
        return [Para(t, align="left", indent_pt=None, font_scale=1.0)
                for t in self.paragraphs_text()]

    def set_paragraphs(self, texts: list[str]):
        """程序写入段落文本（不触发 edited）。"""
        self._guard = True
        try:
            self.setPlainText("\n".join(texts))
        finally:
            self._guard = False

    # ---- 事件 ----

    def _on_text_changed(self):
        if self._guard:
            return
        self.edited.emit()

    def _on_rewrap(self):
        wrap = self.apply_geometry()
        if wrap != self._last_wrap:   # 换行宽真变了才需要重排
            self._last_wrap = wrap
            self.edited.emit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._rewrap_timer.start()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._rewrap_timer.start()


# ================= 右栏：参数面板 =================

class ParamPane(QWidget):
    layout_param_changed = pyqtSignal()   # 重排类
    render_param_changed = pyqtSignal()   # 重渲染类
    reshuffle_requested = pyqtSignal()    # 换一批笔迹
    export_png_requested = pyqtSignal()
    export_pdf_requested = pyqtSignal()

    def __init__(self, params: Params, parent=None):
        super().__init__(parent)
        self.p = params
        root = QVBoxLayout(self)

        # ---- 版式 ----
        g2 = QGroupBox("版式")
        f2 = QFormLayout(g2)
        fonts = available_fonts()
        self.cmb_font = QComboBox()
        for name, path in fonts:
            self.cmb_font.addItem(name, path)
        # 初始选中：配置恢复的字体优先，否则回落第一项（云烟默认逻辑在 Params 默认值中）
        idx = self.cmb_font.findData(params.font_path)
        self.cmb_font.setCurrentIndex(max(0, idx))
        if self.cmb_font.currentData():
            self.p.font_path, self.p.font_name = (
                self.cmb_font.currentData(), self.cmb_font.currentText())
        btn_font = QPushButton("浏览字体文件（ttf / ttc / otf）…")
        btn_font.clicked.connect(self._browse_font)
        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(8.0, 36.0)
        self.spin_size.setSingleStep(0.5)
        self.spin_size.setValue(params.font_size_pt)
        self.spin_size.setSuffix(" pt")
        self.spin_line = QDoubleSpinBox()
        self.spin_line.setRange(1.3, 2.5)
        self.spin_line.setSingleStep(0.1)
        self.spin_line.setValue(params.line_factor)
        self.cmb_paper = QComboBox()
        self.cmb_paper.addItems(PAPER_STYLES)
        self.cmb_paper.setCurrentText(params.paper)
        # 手写缩放（仅 Word 版式模式生效）：印刷字满格，手写略小略散
        self.sld_hscale, self.lbl_hscale = self._slider(
            70, 110, round(params.handwriting_scale * 100), " %")
        f2.addRow("字体", self.cmb_font)
        f2.addRow(btn_font)
        f2.addRow("字号", self.spin_size)
        f2.addRow("行距倍数", self.spin_line)
        f2.addRow("纸张样式", self.cmb_paper)
        f2.addRow("手写缩放", self._row(self.sld_hscale, self.lbl_hscale))
        root.addWidget(g2)

        # ---- 页边距（位置式布局：上 / 左 中 右 / 下，单位 mm）----
        self.g2b = QGroupBox("页边距 (mm)")
        grid = QGridLayout(self.g2b)
        grid.setHorizontalSpacing(10)
        self.spin_mt = self._mm_spin(params.margin_top_mm)
        self.spin_ml = self._mm_spin(params.margin_left_mm)
        self.spin_mr = self._mm_spin(params.margin_right_mm)
        self.spin_mb = self._mm_spin(params.margin_bottom_mm)
        cap = QLabel("边距")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("color:#8A9884; background:transparent; border:none;")
        grid.addWidget(self.spin_mt, 0, 1)
        grid.addWidget(self.spin_ml, 1, 0)
        grid.addWidget(cap, 1, 1)
        grid.addWidget(self.spin_mr, 1, 2)
        grid.addWidget(self.spin_mb, 2, 1)
        root.addWidget(self.g2b)

        # ---- 笔画扰动 ----
        g3 = QGroupBox("笔画扰动")
        f3 = QFormLayout(g3)
        self.sld_dx, self.lbl_dx = self._slider(0, 8, params.dx_amp, " px")
        self.sld_dy, self.lbl_dy = self._slider(0, 8, params.dy_amp, " px")
        self.sld_rot, self.lbl_rot = self._slider(0, 200, round(params.rot_amp * 1000), "")
        self.lbl_rot.setText(f"{params.rot_amp:.2f} rad")
        f3.addRow("水平位移", self._row(self.sld_dx, self.lbl_dx))
        f3.addRow("竖直位移", self._row(self.sld_dy, self.lbl_dy))
        f3.addRow("笔画旋转", self._row(self.sld_rot, self.lbl_rot))
        root.addWidget(g3)

        # ---- 写错字 ----
        self.g4 = QGroupBox("写错字")
        f4 = QFormLayout(self.g4)
        self.sld_err, self.lbl_err = self._slider(0, 200, 0, "")
        self.lbl_err.setText("0 %")
        self.cmb_rewrite = QComboBox()
        self.cmb_rewrite.addItems(REWRITE_MODES)
        self.cmb_strike = QComboBox()
        self.cmb_strike.addItems(STRIKE_MODES)
        f4.addRow("错字率", self._row(self.sld_err, self.lbl_err))
        f4.addRow("重写方式", self.cmb_rewrite)
        f4.addRow("涂改方式", self.cmb_strike)
        root.addWidget(self.g4)

        # ---- 墨迹 ----
        g5 = QGroupBox("墨迹")
        f5 = QFormLayout(g5)
        self.sld_weight, self.lbl_weight = self._slider(
            80, 150, round(params.stroke_weight * 100), " %")
        self.cmb_ink = QComboBox()
        self.cmb_ink.addItems(list(INK_COLORS.keys()))
        f5.addRow("笔迹粗细", self._row(self.sld_weight, self.lbl_weight))
        f5.addRow("墨色", self.cmb_ink)
        root.addWidget(g5)

        # ---- 导出 ----
        g6 = QGroupBox("导出")
        v6 = QVBoxLayout(g6)
        btn_png = QPushButton("导出 PNG（300 DPI）")
        btn_pdf = QPushButton("导出 PDF")
        for b in (btn_png, btn_pdf):
            b.setObjectName("primary")
        v6.addWidget(btn_png)
        v6.addWidget(btn_pdf)
        btn_png.clicked.connect(self.export_png_requested.emit)
        btn_pdf.clicked.connect(self.export_pdf_requested.emit)
        root.addWidget(g6)

        row_save = QHBoxLayout()
        btn_save = QPushButton("保存参数")
        btn_save.clicked.connect(self._save_now)
        btn_reset = QPushButton("恢复默认参数")
        btn_reset.clicked.connect(self._reset_defaults)
        row_save.addWidget(btn_save)
        row_save.addWidget(btn_reset)
        root.addLayout(row_save)
        btn_reshuffle = QPushButton("换一批笔迹（重新随机）")
        root.addWidget(btn_reshuffle)
        root.addStretch(1)

        # ---- 信号分类：重排类 vs 重渲染类 ----
        self.cmb_font.currentIndexChanged.connect(lambda _: self._layout_changed())
        self.spin_size.valueChanged.connect(lambda _: self._layout_changed())
        self.spin_line.valueChanged.connect(lambda _: self._layout_changed())
        for s in (self.spin_mt, self.spin_ml, self.spin_mr, self.spin_mb):
            s.valueChanged.connect(lambda _: self._layout_changed())
        self.sld_err.valueChanged.connect(self._on_err_rate)
        self.cmb_rewrite.currentTextChanged.connect(lambda _: self._layout_changed())
        self.sld_hscale.valueChanged.connect(self._on_hscale)
        btn_reshuffle.clicked.connect(self.reshuffle_requested.emit)

        # 纸张/扰动/涂改/墨色只影响绘制，不触发重排
        self.cmb_paper.currentTextChanged.connect(lambda _: self._render_changed())
        self.sld_dx.valueChanged.connect(self._on_dx)
        self.sld_dy.valueChanged.connect(self._on_dy)
        self.sld_rot.valueChanged.connect(self._on_rot)
        self.sld_weight.valueChanged.connect(self._on_weight)
        self.cmb_strike.currentTextChanged.connect(lambda _: self._render_changed())
        self.cmb_ink.currentTextChanged.connect(lambda _: self._render_changed())

        # 参数面板内滚轮一律只滚动面板，不改任何数值：
        # 拦截各控件的滚轮事件后 ignore()，让事件冒泡给父级 QScrollArea
        for w in self.findChildren((QSpinBox, QDoubleSpinBox, QComboBox, QSlider)):
            w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if isinstance(ev, QWheelEvent):
            ev.ignore()   # 冒泡到 QScrollArea 滚动面板
            return True
        return super().eventFilter(obj, ev)

    @staticmethod
    def _slider(lo, hi, val, suffix):
        s = ClickSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(val))
        lbl = QLabel(f"{val}{suffix}")
        return s, lbl

    @staticmethod
    def _row(slider, lbl):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1)
        h.addWidget(lbl)
        return w

    def _layout_changed(self):
        self.p.font_path = self.cmb_font.currentData()
        self.p.font_name = self.cmb_font.currentText()
        self.p.font_size_pt = self.spin_size.value()
        self.p.line_factor = self.spin_line.value()
        self.p.margin_top_mm = self.spin_mt.value()
        self.p.margin_bottom_mm = self.spin_mb.value()
        self.p.margin_left_mm = self.spin_ml.value()
        self.p.margin_right_mm = self.spin_mr.value()
        self.p.error_rate = self.sld_err.value() / 1000.0
        self.p.rewrite_mode = self.cmb_rewrite.currentText()
        self.layout_param_changed.emit()

    def _browse_font(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字体文件", self.params.last_dir_font,
            "字体文件 (*.ttf *.ttc *.otf)")
        if not path:
            return
        self.params.last_dir_font = os.path.dirname(path)
        idx = self.cmb_font.findData(path)
        if idx < 0:
            self.cmb_font.addItem(font_family_name(path), path)
            idx = self.cmb_font.count() - 1
        self.cmb_font.setCurrentIndex(idx)   # 触发 _layout_changed

    @staticmethod
    def _mm_spin(val):
        s = QDoubleSpinBox()
        s.setRange(5.0, 60.0)
        s.setSingleStep(1.0)
        s.setDecimals(1)
        s.setValue(val)
        return s

    def _render_changed(self):
        self.p.paper = self.cmb_paper.currentText()
        self.p.strike_mode = self.cmb_strike.currentText()
        self.p.ink_color = self.cmb_ink.currentText()
        self.render_param_changed.emit()

    def _save_now(self):
        """显式保存：立即写入 config.json（自动保存之外的保险），按钮回显结果。"""
        save_config(self.p.to_config())
        btn = self.sender()
        if btn is not None:
            btn.setText("已保存 ✓")
            QTimer.singleShot(1200, lambda: btn.setText("保存参数"))

    def _reset_defaults(self):
        """恢复出厂默认：params 归默认值后各控件回填，走信号同步标签并触发重排。"""
        self.p.reset_to_defaults()
        idx = self.cmb_font.findData(self.p.font_path)
        self.cmb_font.setCurrentIndex(max(0, idx))
        self.spin_size.setValue(self.p.font_size_pt)
        self.spin_line.setValue(self.p.line_factor)
        self.spin_mt.setValue(self.p.margin_top_mm)
        self.spin_ml.setValue(self.p.margin_left_mm)
        self.spin_mr.setValue(self.p.margin_right_mm)
        self.spin_mb.setValue(self.p.margin_bottom_mm)
        self.cmb_paper.setCurrentText(self.p.paper)
        self.sld_dx.setValue(round(self.p.dx_amp))
        self.sld_dy.setValue(round(self.p.dy_amp))
        self.sld_rot.setValue(round(self.p.rot_amp * 1000))
        self.sld_err.setValue(round(self.p.error_rate * 1000))
        self.sld_weight.setValue(round(self.p.stroke_weight * 100))
        self.cmb_rewrite.setCurrentText(self.p.rewrite_mode)
        self.cmb_strike.setCurrentText(self.p.strike_mode)
        self.cmb_ink.setCurrentText(self.p.ink_color)
        self.sld_hscale.setValue(round(self.p.handwriting_scale * 100))
        self.layout_param_changed.emit()   # 兜底：控件值未变的项也重排一次

    def _on_dx(self, v):
        self.lbl_dx.setText(f"{v} px")
        self.p.dx_amp = float(v)
        self.render_param_changed.emit()

    def _on_dy(self, v):
        self.lbl_dy.setText(f"{v} px")
        self.p.dy_amp = float(v)
        self.render_param_changed.emit()

    def _on_weight(self, v):
        self.lbl_weight.setText(f"{v} %")
        self.p.stroke_weight = v / 100.0
        self.render_param_changed.emit()

    def _on_rot(self, v):
        self.lbl_rot.setText(f"{v / 1000:.2f} rad")
        self.p.rot_amp = v / 1000.0
        self.render_param_changed.emit()

    def _on_err_rate(self, v):
        self.lbl_err.setText(f"{v / 10:.0f} %")
        self.p.error_rate = v / 1000.0
        self.layout_param_changed.emit()

    # ---- Word 版式模式 ----

    def set_mode_enabled(self, word: bool):
        """Word 版式下排版交给 Word：字号/行距/纸张/边距/错字失效，仅留缩放。

        由 MainWindow 在版式模式切换时调用（模式下拉已上移到预览工具栏）。
        """
        for w in (self.spin_size, self.spin_line, self.cmb_paper):
            w.setEnabled(not word)
        self.g2b.setEnabled(not word)   # 页边距
        self.g4.setEnabled(not word)    # 写错字
        self.sld_hscale.setEnabled(word)

    def _on_hscale(self, v):
        self.lbl_hscale.setText(f"{v} %")
        self.p.handwriting_scale = v / 100.0
        if self.p.layout_mode == "word":
            self.layout_param_changed.emit()


# ================= 主窗口 =================

class WordAssetsWorker(QThread):
    """Word 版式资产转换线程：docx -> (纸面位图, 逐字坐标)。

    Word COM 转换耗时数秒，放在后台线程执行，界面保持响应；
    完成后经 done 信号回到 UI 线程落盘。
    """

    done = pyqtSignal(list, list, object)   # (imgs, boxes, err)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        imgs, boxes, err = [], [], None
        for attempt in range(3):
            try:
                imgs, boxes = docx_to_word_assets(self._path, dpi=DPI_BASE)
                err = None
                break
            except Exception as e:
                err = e
                if attempt < 2:
                    self.sleep(1.2)   # Word 保存瞬间会短暂锁文件，稍候重试
        self.done.emit(imgs, boxes, err)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("手写记录 - Word 誊写上纸")
        # 适配屏幕：窗口不超过屏幕可用区域（避免底边被任务栏/屏幕裁掉）
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1500, scr.width() - 40), min(950, scr.height() - 40))

        self.params = Params()
        self.params.apply_config(load_config())   # 启动恢复上次配置
        self.bg_images: list = []
        self.params.bg_images = self.bg_images   # 共享同一列表引用
        self.paras = []
        self.layout = None
        self.doc_path = ""
        self._bg_from_word = False   # 当前背景是否由 Word 版式管线生成
        self._syncing = False        # Word 文档自动同步进行中
        self._restore_page = None    # 同步刷新后恢复到的页码
        self._render_key = (0, 0)
        self._render_err_shown = False   # 渲染失败弹窗只提示一次
        # Word 版式异步转换状态：worker/进度框/完成回调/忙时挂起的最新请求
        self._word_worker: WordAssetsWorker | None = None
        self._word_dlg: QProgressDialog | None = None
        self._word_after = None
        self._word_quiet = False
        self._word_pending = None

        # Word 文档文件监视：Word 里保存后自动重新誊写
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_doc_file_changed)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(1500)   # 防抖：等 Word 写盘完成
        self._sync_timer.timeout.connect(self._sync_word_doc)

        self.worker = RenderWorker()
        self.worker.rendered.connect(self._on_rendered)
        self.worker.start()

        # ---- 三栏 ----
        self.preview = PreviewPane()
        self.editor = PaperEditor(self.params)
        self.compare = ComparePane()
        # 中栏双形态：自由排版=纸面编辑器；Word 版式=Word 版面对照页
        self.mid_stack = QStackedWidget()
        self.mid_stack.addWidget(self.editor)      # index 0
        self.mid_stack.addWidget(self.compare)     # index 1
        # 中栏顶部文档条（原右栏"文档"组）：导入按钮 + 文件名 + 统计，横排单行
        self.btn_import = QPushButton("导入 Word 文档…")
        self.btn_import.setObjectName("primary")
        self.lbl_doc = QLabel("未导入")
        self.lbl_doc.setWordWrap(True)
        self.lbl_info = QLabel("")
        self.btn_doc_clear = QPushButton("清除文档")
        self.btn_doc_clear.setToolTip("清除已导入的 Word 文档内容，回到空纸状态")
        self.btn_doc_clear.setEnabled(False)
        doc_bar = QWidget()
        doc_bar_l = QHBoxLayout(doc_bar)
        doc_bar_l.setContentsMargins(8, 6, 8, 2)
        doc_bar_l.addWidget(self.btn_import)
        doc_bar_l.addWidget(self.btn_doc_clear)
        doc_bar_l.addWidget(self.lbl_doc, 1)
        doc_bar_l.addWidget(self.lbl_info)
        self.mid_wrap = QWidget()
        mid_l = QVBoxLayout(self.mid_wrap)
        mid_l.setContentsMargins(0, 0, 0, 0)
        mid_l.setSpacing(0)
        mid_l.addWidget(doc_bar)
        mid_l.addWidget(self.mid_stack)
        self.param_pane = ParamPane(self.params)
        self.param_pane.setMinimumWidth(280)

        # 参数栏放进滚动区：矮屏幕上也能滚到全部控件
        param_scroll = QScrollArea()
        param_scroll.setWidget(self.param_pane)
        param_scroll.setWidgetResizable(True)
        param_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        split = QSplitter()
        split.addWidget(self.preview)
        split.addWidget(self.mid_wrap)
        split.addWidget(param_scroll)
        # 预览区与对照区同宽：同一张纸按同比例显示，位置逐字对应
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 0)
        split.setSizes([760, 760, 300])
        self.setCentralWidget(split)

        # ---- 信号 ----
        self.param_pane.layout_param_changed.connect(self._full_refresh)
        self.param_pane.render_param_changed.connect(self._render_only_refresh)
        self.param_pane.reshuffle_requested.connect(self._reshuffle)
        self.btn_import.clicked.connect(self._import_docx)
        self.btn_doc_clear.clicked.connect(self._clear_doc)
        self.param_pane.export_png_requested.connect(lambda: self._export("png"))
        self.param_pane.export_pdf_requested.connect(lambda: self._export("pdf"))
        # 版式模式与背景模板（已上移到预览工具栏）
        self.preview.mode_changed.connect(self._on_mode_changed)
        self.preview.bg_import_requested.connect(self._import_bg)
        self.preview.bg_builtin_requested.connect(self._use_builtin_bg)
        self.preview.bg_clear_requested.connect(self._clear_bg)
        self.preview.bg_save_requested.connect(self._save_bg_template)
        self.editor.edited.connect(self._full_refresh)
        self.worker.render_failed.connect(self._on_render_failed)
        # 预览 <-> 对照区 双向翻页联动
        self.preview.page_changed.connect(self.compare.set_page)
        self.compare.page_selected.connect(
            lambda p: self.preview.goto_page(p + 1))

        # debounce 300ms
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._do_refresh)

        self._pending_full = False
        self._cache: dict = {}

        # 同步 UI 初始状态：版式模式选中项 + Word 模式控件启停
        self.preview.set_mode(self.params.layout_mode)
        self.param_pane.set_mode_enabled(self.params.layout_mode == "word")

        # 启动恢复：优先载入上次的文档；没有则载入示例（若有）
        doc = self.params.doc_path
        if doc and os.path.exists(doc):
            self._load_docx(doc)
        else:
            sample = os.path.join(os.path.dirname(__file__), "samples",
                                  "示例文档.docx")
            if os.path.exists(sample):
                self._load_docx(sample)
        # 自由排版下恢复上次的背景模板（Word 版式由 _load_docx 自动重建资产）；
        # 旧配置存的 docx 路径若已有同名图片版，优先图片（启动免 Word 转换）
        path = self.params.bg_template_path
        if path and path.lower().endswith(".docx"):
            png = os.path.splitext(path)[0] + ".png"
            if os.path.exists(png):
                path = png
        if (self.params.layout_mode == "free" and path
                and os.path.exists(path)):
            self._use_builtin_bg(path)
        self._update_bg_save_btn()

    # ---------- 文档导入与重排 ----------

    def _import_docx(self):
        start = self.params.last_dir_doc
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Word 文档", start, "Word 文档 (*.docx)")
        if path:
            self.params.last_dir_doc = os.path.dirname(path)
            self._load_docx(path)

    def _load_docx(self, path: str):
        try:
            self.paras = parse_docx(path)
        except Exception as e:
            QMessageBox.critical(self, "导入失败",
                                 f"无法解析该文档：\n{e}\n\n请确认为有效的 .docx 文件。")
            return
        self.doc_path = path
        self.params.doc_path = path
        self.btn_doc_clear.setEnabled(True)
        self.lbl_doc.setText(os.path.basename(path))
        if self.params.layout_mode == "word":
            # Word 版式：后台线程转换，完成后 _on_word_assets 收尾
            self._build_word_assets(path, after=self._finish_doc_load)
            return
        # 自由排版：docx 内容灌进纸面编辑器（断行/排版以编辑器为准）
        self.editor.set_paragraphs(self._docx_texts_for_editor())
        self._finish_doc_load()

    def _finish_doc_load(self):
        """文档加载收尾（Word 资产就绪后或自由排版立即执行）。"""
        self._watch_doc(self.doc_path)
        self.params.mark_layout_changed()
        self._full_refresh()

    def _clear_doc(self):
        """一键清除已导入的 Word 文档内容，回到未导入的空纸状态。"""
        if not self.doc_path:
            return
        self._watch_doc(None)       # 停止文件自动同步
        self._syncing = False
        self._sync_timer.stop()
        self._word_pending = None   # 丢弃在途的转换排队请求
        self.doc_path = ""
        self.params.doc_path = ""
        self.paras = []
        self.lbl_doc.setText("未导入")
        self.lbl_info.setText("")
        self.editor.set_paragraphs([""])   # 内部有 guard，不触发 edited
        if self._bg_from_word:      # 纸面还铺着 Word 版面：一并清除
            self.bg_images[:] = []
            self.compare.set_images([])
            self.params.bg_name = ""
            self.params.bg_rules = []
            self.params.bg_template_path = ""
            self._bg_from_word = False
        self.params.word_char_boxes[:] = []
        self.btn_doc_clear.setEnabled(False)
        self._update_bg_save_btn()
        self.params.mark_layout_changed()
        self._full_refresh()
        self.statusBar().showMessage("已清除导入的 Word 文档内容", 4000)

    def _docx_texts_for_editor(self) -> list[str]:
        """docx 段落 -> 编辑器文本：首行缩进换算为行首全角空格。"""
        texts = []
        for para in self.paras:
            t = para.text
            if t.strip() and para.indent_pt:
                n = max(1, min(8, round(para.indent_pt / self.params.font_size_pt)))
                t = "　" * n + t
            texts.append(t)
        return texts or [""]

    def _build_word_assets(self, path: str, quiet: bool = False, after=None):
        """Word 版式管线（后台线程）：docx -> (纸面位图, 逐字坐标)。

        转换在 WordAssetsWorker 中执行，期间界面保持响应；完成后统一在
        _on_word_assets 落盘并执行 after 收尾回调。quiet=True 时失败不弹
        错误框（用于 Word 保存后的自动同步）。已有转换在跑时只保留最新
        请求，完成后自动执行。
        """
        if self._word_worker is not None:
            self._word_pending = (path, quiet, after)
            return
        self._word_quiet = quiet
        self._word_after = after
        self._word_dlg = QProgressDialog(
            "Word 版式转换中（Word → PDF → 提取字符坐标）…", None, 0, 0, self)
        self._word_dlg.setWindowTitle("请稍候")
        self._word_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._word_dlg.show()
        self._word_worker = WordAssetsWorker(path, self)
        self._word_worker.done.connect(self._on_word_assets)
        self._word_worker.start()

    def _on_word_assets(self, imgs: list, boxes: list, err: Exception | None):
        """转换完成回调（UI 线程）：落盘结果并执行收尾。"""
        self._word_worker = None
        if self._word_dlg:
            self._word_dlg.close()
            self._word_dlg = None
        after = self._word_after
        self._word_after = None
        if err is not None or not imgs:
            if self._word_quiet:   # 自动同步失败：只更新状态文字
                self._syncing = False
                self.compare.set_sync_status(
                    "同步失败（文件可能被占用），在 Word 中再保存一次即可重试")
            else:
                msg = str(err) if err else "转换结果为空。"
                QMessageBox.critical(self, "Word 版式转换失败", msg)
            return
        if self.params.layout_mode != "word" or not self.doc_path:
            return   # 转换期间用户已切模式或清除文档，丢弃过期结果
        self.bg_images[:] = imgs                  # 纸面 = Word 页面原样
        self.params.word_char_boxes[:] = boxes    # 手写锚点 = 逐字坐标
        self.params.bg_rules = []                 # Word 版式自定行位，不用信纸横线
        self.params.bg_name = os.path.basename(self.doc_path)
        self.params.bg_template_path = self.doc_path
        self._bg_from_word = True
        self._update_bg_save_btn()
        self.compare.set_images(self.bg_images)   # 对照区 = 同一份 Word 版面
        if after is not None:
            after()
        nxt = self._word_pending
        self._word_pending = None
        if nxt is not None:
            self._build_word_assets(*nxt)

    def _on_mode_changed(self, mode: str):
        self.params.layout_mode = mode
        # 中栏切换双形态：word=版面对照页，free=段落列表
        self.mid_stack.setCurrentIndex(1 if mode == "word" else 0)
        self.param_pane.set_mode_enabled(mode == "word")
        self._update_bg_save_btn()
        if mode == "word":
            if self.doc_path and (not self.params.word_char_boxes
                                  or not self._bg_from_word):
                # 已有文档但还没有 Word 版式资产：对当前文档做一次转换
                self._load_docx(self.doc_path)
                return
            self._watch_doc(self.doc_path or None)
        else:
            self._watch_doc(None)
            if self.paras:
                # 切回编辑器排版：把当前文档灌入编辑器（缩进 -> 全角空格）
                self.editor.set_paragraphs(self._docx_texts_for_editor())
            if self._bg_from_word:
                # 切回自由排版：清掉 Word 版式生成的纸面（避免印字与手写重叠）
                self.bg_images[:] = []
                self.params.bg_name = ""
                self.params.bg_rules = []
                self.params.bg_template_path = ""
                self._bg_from_word = False
                self.compare.set_images([])
        self.params.mark_layout_changed()
        self._full_refresh()

    # ---------- Word 文档自动同步 ----------

    def _watch_doc(self, path: str):
        """监视当前 docx（仅 Word 版式模式）；传 None 停止监视。"""
        for p in self._watcher.files():
            self._watcher.removePath(p)
        if path and os.path.exists(path) and self.params.layout_mode == "word":
            self._watcher.addPath(path)
            self.compare.set_sync_status(
                f"监视中：{os.path.basename(path)} · 在 Word 中保存后自动同步")
        else:
            self.compare.set_sync_status("")

    def _on_doc_file_changed(self, path: str):
        # Word 保存时会替换文件，路径可能被移出监视：重新加回
        if path not in self._watcher.files() and os.path.exists(path):
            self._watcher.addPath(path)
        if self._syncing:
            return
        self.compare.set_sync_status("检测到文档修改…")
        self._sync_timer.start()   # 防抖 1.5s，等 Word 写盘/解锁

    def _sync_word_doc(self):
        if (self._syncing or not self.doc_path
                or self.params.layout_mode != "word"):
            return
        if not os.path.exists(self.doc_path):
            return
        self._syncing = True
        cur = self.preview.current_page()
        self.compare.set_sync_status("正在重新誊写（Word → PDF → 提取坐标）…")

        def _after():
            self._syncing = False
            self._restore_page = cur      # 刷新后回到原来正在看的页
            self.params.mark_layout_changed()
            self._full_refresh()
            self.compare.set_sync_status(
                f"已同步 {time.strftime('%H:%M:%S')} · "
                f"监视中：{os.path.basename(self.doc_path)}")

        # 后台线程转换，完成后 _after 收尾；失败在 _on_word_assets 中处理
        self._build_word_assets(self.doc_path, quiet=True, after=_after)

    def _reshuffle(self):
        self.params.seed = random.randint(0, 2 ** 31 - 1)
        self.params.mark_layout_changed()
        self._full_refresh()

    # ---------- 背景模板 ----------

    def _import_bg(self):
        start = self.params.last_dir_bg
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景模板", start,
            "背景模板 (*.docx *.png *.jpg *.jpeg)")
        if not path:
            return
        self.params.last_dir_bg = os.path.dirname(path)
        self.preview.reset_builtin_bg()
        self._apply_bg_path(path)

    def _use_builtin_bg(self, path: str):
        self._apply_bg_path(path)   # 下拉保持当前选中项，与背景状态一致

    def _apply_bg_path(self, path: str):
        """应用背景模板（按扩展名分流）：图片直接加载，docx 走 Word 转换。"""
        if path.lower().endswith(".docx"):
            dlg = QProgressDialog("正在转换背景模板（Word → PDF → 图片）…",
                                  None, 0, 0, self)
            dlg.setWindowTitle("请稍候")
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.show()
            QApplication.processEvents()
            try:
                imgs = docx_to_page_images(path, dpi=DPI_BASE)
            except Exception as e:
                dlg.close()
                QMessageBox.critical(self, "背景模板导入失败", str(e))
                return
            dlg.close()
            if not imgs:
                QMessageBox.warning(self, "背景模板导入失败", "转换结果为空。")
                return
        else:
            try:
                imgs = load_image_background(path)
            except Exception as e:
                QMessageBox.critical(self, "背景模板导入失败", str(e))
                return
        self.bg_images[:] = imgs          # 原地替换：params 引用保持有效
        self.params.bg_rules = [detect_rule_lines(im) for im in imgs]
        self.params.bg_name = os.path.splitext(os.path.basename(path))[0]
        self.params.bg_template_path = path
        self._bg_from_word = False
        self.preview.refresh_bg_combo(select_path=path)
        self._update_bg_save_btn()
        if any(self.params.bg_rules):
            # 信纸横线接管行位（一行对一线），属版式变化需全量重排
            self.params.mark_layout_changed()
            self._full_refresh()
        else:
            self.params.mark_render_changed()
            self._render_only_refresh()

    def _update_bg_save_btn(self):
        """存为模板按钮：仅自由排版 + 当前背景来自 docx 转换时可点。"""
        ok = (self.params.layout_mode == "free" and bool(self.bg_images)
              and self.params.bg_template_path.lower().endswith(".docx"))
        self.preview.btn_bg_save.setEnabled(ok)

    def _save_bg_template(self):
        """把当前 docx 背景位图存为用户图片模板，并切换到图片版复用。"""
        if self._bg_from_word or not self.bg_images:
            return
        name = (os.path.splitext(os.path.basename(self.params.bg_template_path))[0]
                if self.params.bg_template_path else self.params.bg_name) or "背景模板"
        try:
            paths = save_background_template(self.bg_images, name)
        except Exception as e:
            QMessageBox.critical(self, "保存模板失败", str(e))
            return
        self._apply_bg_path(paths[0])   # 会话切到图片模板：免 Word、秒加载
        self.statusBar().showMessage(
            f"已存为模板：{os.path.basename(paths[0])}" if len(paths) == 1
            else f"已保存 {len(paths)} 页为 {len(paths)} 个图片模板", 6000)

    def _clear_bg(self):
        self.bg_images[:] = []
        self.compare.set_images([])
        self.params.bg_name = ""
        self.params.bg_rules = []
        self.params.bg_template_path = ""
        self._bg_from_word = False
        self.preview.reset_builtin_bg()
        self._update_bg_save_btn()
        self.params.mark_render_changed()
        self._render_only_refresh()

    def _full_refresh(self):
        self._pending_full = True
        self._debounce.start()   # debounce 300ms 后执行 _do_refresh

    def _render_only_refresh(self):
        if self.layout is None:
            return
        self._pending_full = False
        self._debounce.start()

    def _do_refresh(self):
        save_config(self.params.to_config())   # 参数变化即自动落盘（幂等，<1ms）
        if self._pending_full:
            self._pending_full = False
            vis_lines = None
            if self.params.layout_mode != "word":
                # 编辑器排版：视觉行 = 断行真相（方案 B'）
                self.paras = self.editor.build_paras()
                vis_lines = self.editor.visual_lines()
            try:
                self.layout = layout_document(
                    self.paras, self.params, pre_broken_lines=vis_lines)
            except Exception:
                traceback.print_exc()
                QMessageBox.critical(self, "排版失败", "排版引擎异常，详见控制台。")
                return
            self._update_error_highlights()
            self.preview.set_pages(self.layout.total_pages)
            if self._restore_page is not None:
                p = self._restore_page
                self._restore_page = None
                self.preview.goto_page(p + 1)
            n_err = self.layout.error_count
            n_chars = sum(len(pg) for pg in self.layout.chars_by_page)
            tag = "Word 版式 · " if self.params.layout_mode == "word" else ""
            if (self.params.layout_mode == "word"
                    and not self.params.word_char_boxes):
                self.lbl_info.setText("Word 版式：请先导入 Word 文档")
            else:
                self.lbl_info.setText(
                    f"{tag}{len(self.paras)} 段 · {n_chars} 字 · {n_err} 处错字 · "
                    f"{self.layout.total_pages} 页")
        self._request_render()

    # ---------- 编辑器错字整行高亮 / 渲染错误提示 ----------

    def _update_error_highlights(self):
        """错字所在视觉行整行淡黄高亮（行序 == layout.line_errors）。"""
        sels = []
        if self.params.layout_mode != "word":
            vis = self.editor.visual_lines()
            if self.layout is not None and len(self.layout.line_errors) == sum(
                    len(r) for r in vis):
                row_idx = 0
                b = self.editor.document().firstBlock()
                while b.isValid():
                    lay = b.layout()
                    for i in range(lay.lineCount()):
                        if self.layout.line_errors[row_idx]:
                            ln = lay.lineAt(i)
                            cur = QTextCursor(b)
                            cur.setPosition(b.position() + ln.textStart())
                            cur.setPosition(
                                b.position() + ln.textStart() + ln.textLength(),
                                QTextCursor.MoveMode.KeepAnchor)
                            sels.append(_err_selection(cur))
                        row_idx += 1
                    b = b.next()
        self.editor.setExtraSelections(sels)

    def _on_render_failed(self, msg: str):
        self.statusBar().showMessage(f"渲染失败：{msg}", 10000)
        if not self._render_err_shown:
            self._render_err_shown = True
            QMessageBox.critical(self, "渲染失败",
                                 f"手写渲染出错（常见原因：字体文件无效）：\n{msg}")

    # ---------- 渲染调度 ----------

    def _request_render(self):
        if self.layout is None:
            return
        self._render_key = (self.params.layout_version, self.params.render_version)
        self._cache.clear()
        cur = self.preview.current_page()
        order = sorted(range(self.layout.total_pages), key=lambda p: abs(p - cur))
        jobs = []
        for p in order:
            key = (self.params.layout_version, self.params.render_version, p, DPI_PREVIEW)
            jobs.append((p, key, self.layout, copy.copy(self.params)))
        self.worker.submit(jobs)

    def _on_rendered(self, page: int, key: tuple, qimg: QImage):
        if key[:2] != self._render_key:
            return  # 过期任务（期间参数又变了）
        self._render_err_shown = False
        self.preview.set_page_image(page, qimg)

    # ---------- 导出 ----------

    def _export(self, kind: str):
        if self.layout is None:
            QMessageBox.information(self, "提示", "请先输入内容或导入 Word 文档。")
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "选择导出目录", self.params.last_dir_export)
        if not out_dir:
            return
        self.params.last_dir_export = out_dir
        base = os.path.splitext(os.path.basename(self.doc_path))[0] or "手写页"
        prog = QProgressDialog("正在导出…", "取消", 0, self.layout.total_pages, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)

        def progress(done, total):
            prog.setValue(done)
            QApplication.processEvents()

        try:
            if kind == "png":
                files = export_png(self.layout, self.params, out_dir, base, progress)
                QMessageBox.information(
                    self, "导出完成",
                    f"已导出 {len(files)} 张 PNG 到：\n{out_dir}")
            else:
                path = export_pdf(self.layout, self.params, out_dir, base, progress)
                QMessageBox.information(self, "导出完成", f"PDF 已保存到：\n{path}")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "导出失败", str(e))
        prog.close()

    def closeEvent(self, e):
        save_config(self.params.to_config())   # 关窗兜底：防抖窗口内的最后改动也落盘
        if self._word_worker is not None and self._word_worker.isRunning():
            self._word_worker.wait(15000)   # 等 Word 转换收尾，防线程销毁崩溃
        self.worker.stop()
        super().closeEvent(e)


APP_ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ico", "icon.png")


def main():
    os.makedirs(PROJECT_FONTS_DIR, exist_ok=True)   # 便携字体目录 fonts/
    if os.name == "nt":
        # Windows 任务栏按 python.exe 分组会显示 Python 默认图标；
        # 注册独立 AppUserModelID 后任务栏才采用下方设置的窗口图标
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "handwritten.notes.app")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme_qss_with_arrows())
    icon = QIcon(APP_ICON_PATH) if os.path.exists(APP_ICON_PATH) else None
    if icon is not None:
        app.setWindowIcon(icon)   # 窗口标题栏图标，MainWindow 自动继承
    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)   # 任务栏兜底：Windows 下窗口级设置更可靠
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
