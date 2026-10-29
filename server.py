"""手写记录 Web 服务端（FastAPI）：把 app/ 引擎以 HTTP 接口暴露。

单用户单会话，状态常驻内存（桌面版 MainWindow 的对应物）：
- 文本/docx 输入 -> 段落 -> 全文重排（惰性，版本号失效）-> 按页渲染 PNG
- 参数读写（重排类/渲染类分级失效）+ config.json 落盘
- 字体/图片背景上传；导出 PNG ZIP / PDF

浏览器直连本服务即为步骤 1 的验证形态；Vue3 前端（步骤 2）走同一组接口。
"""
import io
import os
import shutil
import socket
import tempfile
import threading
import traceback
import zipfile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from PIL import Image

from app.docx_parser import Para, parse_docx
from app.exporter import export_pdf, export_png
from app.fonts import PROJECT_FONTS_DIR, available_fonts
from app.layout_engine import layout_document
from app.paths import DATA_DIR, RESOURCE_DIR
from app.params import (DPI_BASE, DPI_PREVIEW, INK_COLORS, Params,
                        PAPER_STYLES, REWRITE_MODES, STRIKE_MODES,
                        load_config, save_config)
from app.renderer import render_page

APP = FastAPI(title="手写记录")
APP.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])

# 重排类参数：改动使版式与全部渲染缓存失效；渲染类仅重画
_LAYOUT_FIELDS = {
    "font_path", "font_size_pt", "line_factor",
    "margin_top_mm", "margin_bottom_mm", "margin_left_mm", "margin_right_mm",
    "paper", "error_rate", "rewrite_mode", "handwriting_scale",
}
_RENDER_FIELDS = {"dx_amp", "dy_amp", "rot_amp", "strike_mode",
                  "ink_color", "stroke_weight", "seed"}

_ENUM_CHECKS = {
    "paper": PAPER_STYLES,
    "strike_mode": STRIKE_MODES,
    "rewrite_mode": REWRITE_MODES,
    "ink_color": list(INK_COLORS),
}
_FLOAT_FIELDS = (_LAYOUT_FIELDS | _RENDER_FIELDS) - set(_ENUM_CHECKS) - {"seed", "font_path"}
_FONT_EXTS = (".ttf", ".ttc", ".otf")
_BG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_BG_UPLOAD_DIR = os.path.join(DATA_DIR, "bg_uploads")


class TextIn(BaseModel):
    text: str


class _State:
    """会话状态：参数 + 排版输入 + 惰性版式 + 渲染缓存。"""

    def __init__(self):
        self.params = Params()
        self.params.apply_config(load_config())
        self.text = ""
        self.paras = [Para("", align="left", indent_pt=None, font_scale=1.0)]
        self.layout = None
        self._built_lv = -1
        self._cache = {}   # (lv, rv, page, dpi) -> png bytes
        self.lock = threading.Lock()
        self._reload_bg()

    # ---------- 背景 ----------

    def _reload_bg(self):
        path = self.params.bg_template_path
        self.params.bg_images = []
        if path and os.path.splitext(path)[1].lower() in _BG_EXTS \
                and os.path.exists(path):
            try:
                img = Image.open(path).convert("RGB")
                self.params.bg_images = [img]
            except Exception:
                traceback.print_exc()

    def invalidate(self, layout_level: bool):
        if layout_level:
            self.params.mark_layout_changed()
        else:
            self.params.mark_render_changed()
        self._cache.clear()

    def rebuild(self):
        """同步重排（提交文本/参数后调用，保证 meta 立即带页数）。"""
        with self.lock:
            self._ensure_layout_locked()

    # ---------- 重排（惰性，持锁） ----------

    def _ensure_layout_locked(self):
        if self.layout is not None and self._built_lv == self.params.layout_version:
            return self.layout
        if self.params.layout_mode == "word":
            raise HTTPException(400, "Word 版式依赖 Word COM 转换，Web 版暂不支持；"
                                     "请在参数中切回自由排版。")
        try:
            self.layout = layout_document(self.paras, self.params)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(500, f"排版失败：{e}")
        self._built_lv = self.params.layout_version
        return self.layout

    def page_png(self, page: int, dpi: int) -> bytes:
        key = (self.params.layout_version, self.params.render_version, page, dpi)
        data = self._cache.get(key)
        if data is not None:
            return data
        with self.lock:
            data = self._cache.get(key)   # 双检：等锁期间可能已填充
            if data is None:
                layout = self._ensure_layout_locked()
                if not 0 <= page < layout.total_pages:
                    raise HTTPException(404, f"页码越界：第 {page + 1} 页 / 共 {layout.total_pages} 页")
                img = render_page(layout, self.params, page, dpi=dpi, ss=2)
                buf = io.BytesIO()
                img.save(buf, "PNG")
                data = buf.getvalue()
                if len(self._cache) >= 32:
                    self._cache.clear()
                self._cache[key] = data
        return data

    def export(self, kind: str, out_dir: str, base: str):
        with self.lock:
            layout = self._ensure_layout_locked()
            if kind == "pdf":
                return [export_pdf(layout, self.params, out_dir, base)]
            return export_png(layout, self.params, out_dir, base)


STATE = _State()


def _set_config(cfg: dict):
    """白名单 + 类型/枚举校验后应用参数，返回是否重排级变更。"""
    unknown = set(cfg) - (_LAYOUT_FIELDS | _RENDER_FIELDS)
    if unknown:
        raise HTTPException(400, f"不支持的参数：{sorted(unknown)}")
    if cfg.get("layout_mode") == "word":
        raise HTTPException(400, "Word 版式暂不支持（Web 版），请使用 free。")
    if "font_path" in cfg:
        if not os.path.exists(cfg["font_path"]):
            raise HTTPException(400, f"字体文件不存在：{cfg['font_path']}")
        STATE.params.font_name = os.path.splitext(
            os.path.basename(cfg["font_path"]))[0]
    for k, values in _ENUM_CHECKS.items():
        if k in cfg and cfg[k] not in values:
            raise HTTPException(400, f"{k} 取值无效：{cfg[k]!r}")
    layout_level = bool(_LAYOUT_FIELDS & set(cfg))
    for k, v in cfg.items():
        if k in _FLOAT_FIELDS:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 需要数字，收到 {v!r}")
            if k == "seed":
                v = int(v)
        setattr(STATE.params, k, v)
    STATE.invalidate(layout_level)
    save_config(STATE.params.to_config())
    STATE.rebuild()
    return layout_level


def _meta() -> dict:
    p = STATE.params
    n = STATE.layout.total_pages if (STATE.layout is not None
                                     and STATE._built_lv == p.layout_version) else None
    return {
        "params": p.to_config(),
        "enums": {
            "paper": PAPER_STYLES,
            "strike_mode": STRIKE_MODES,
            "rewrite_mode": REWRITE_MODES,
            "ink_color": INK_COLORS,
        },
        "fonts": available_fonts(),
        "total_pages": n,
        "para_count": len(STATE.paras),
        "char_count": sum(len(pg) for pg in
                          (STATE.layout.chars_by_page
                           if n is not None else [])),
    }


@APP.get("/api/health")
def health():
    return {"ok": True}


@APP.get("/api/meta")
def get_meta():
    return _meta()


@APP.post("/api/config")
def post_config(cfg: dict):
    _set_config(cfg)
    return _meta()


@APP.post("/api/text")
def post_text(body: TextIn):
    texts = body.text.split("\n")
    STATE.text = body.text
    STATE.paras = [Para(t, align="left", indent_pt=None, font_scale=1.0)
                   for t in texts]
    STATE.invalidate(True)
    STATE.rebuild()
    return _meta()


@APP.post("/api/docx")
async def post_docx(file: UploadFile):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "请上传 .docx 文件")
    data = await file.read()
    tmp = os.path.join(tempfile.gettempdir(), f"hn_{os.getpid()}_{file.filename}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        STATE.paras = parse_docx(tmp)
    except Exception as e:
        raise HTTPException(400, f"无法解析该文档：{e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    STATE.text = "\n".join(p.text for p in STATE.paras)
    STATE.invalidate(True)
    STATE.rebuild()
    return _meta()


@APP.post("/api/font")
async def post_font(file: UploadFile):
    fn = os.path.basename(file.filename)
    if not fn.lower().endswith(_FONT_EXTS):
        raise HTTPException(400, "仅支持 ttf / ttc / otf 字体文件")
    os.makedirs(PROJECT_FONTS_DIR, exist_ok=True)
    with open(os.path.join(PROJECT_FONTS_DIR, fn), "wb") as f:
        f.write(await file.read())
    return {"fonts": available_fonts()}


@APP.post("/api/bg")
async def post_bg(file: UploadFile):
    fn = os.path.basename(file.filename)
    if not fn.lower().endswith(_BG_EXTS):
        raise HTTPException(400, "背景模板暂仅支持图片（png/jpg/webp）；"
                                 "Word 版式与 docx 背景为桌面版功能。")
    os.makedirs(_BG_UPLOAD_DIR, exist_ok=True)
    path = os.path.join(_BG_UPLOAD_DIR, fn)
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        os.remove(path)
        raise HTTPException(400, "图片解析失败，请换一张图。")
    STATE.params.bg_template_path = path
    STATE.params.bg_name = os.path.splitext(fn)[0]
    STATE.params.bg_images = [img]
    STATE.params.bg_rules = []
    STATE.invalidate(False)
    return {"bg_name": STATE.params.bg_name}


@APP.delete("/api/bg")
def delete_bg():
    STATE.params.bg_images = []
    STATE.params.bg_name = ""
    STATE.params.bg_rules = []
    STATE.params.bg_template_path = ""
    STATE.invalidate(False)
    return {"bg_name": ""}


@APP.get("/api/page/{page}")
def get_page(page: int, dpi: int = 0):
    return Response(STATE.page_png(page, dpi or DPI_PREVIEW),
                    media_type="image/png")


@APP.get("/api/export/{kind}")
def get_export(kind: str):
    if kind not in ("png", "pdf"):
        raise HTTPException(400, "kind 仅支持 png / pdf")
    base = os.path.splitext(os.path.basename(STATE.params.doc_path))[0] \
        if STATE.params.doc_path else "手写页"
    out_dir = tempfile.mkdtemp(prefix="hn_export_")
    try:
        files = STATE.export(kind, out_dir, base)
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    if kind == "pdf":
        path = files[0]
        return FileResponse(path, media_type="application/pdf",
                            filename=f"{base}.pdf",
                            background=_cleanup(out_dir))
    zip_path = os.path.join(out_dir, f"{base}_png.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, os.path.basename(p))
    return FileResponse(zip_path, media_type="application/zip",
                        filename=os.path.basename(zip_path),
                        background=_cleanup(out_dir))


def _cleanup(dir_path: str):
    from starlette.background import BackgroundTask
    return BackgroundTask(shutil.rmtree, dir_path, ignore_errors=True)


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    os.makedirs(PROJECT_FONTS_DIR, exist_ok=True)
    import uvicorn
    uvicorn.run(APP, host="127.0.0.1", port=_pick_port(), log_level="info")


if __name__ == "__main__":
    main()
