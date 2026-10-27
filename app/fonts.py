"""系统字体发现与常用字表。"""
import os

from app.paths import RESOURCE_DIR

# 资源根下的 fonts/ 文件夹：用户把字体文件放进来即可被识别（便携模式）
PROJECT_FONTS_DIR = os.path.join(RESOURCE_DIR, "fonts")

# 候选字体（手写感优先），安装了才显示
CANDIDATE_FONTS = [
    ("楷体",     r"C:\Windows\Fonts\simkai.ttf"),
    ("华文行楷", r"C:\Windows\Fonts\STXINGKA.TTF"),
    ("华文新魏", r"C:\Windows\Fonts\STXINWEI.TTF"),
    ("仿宋",     r"C:\Windows\Fonts\simfang.ttf"),
    ("隶书",     r"C:\Windows\Fonts\SIMLI.TTF"),
    ("幼圆",     r"C:\Windows\Fonts\SIMYOU.TTF"),
    ("方正舒体", r"C:\Windows\Fonts\FZSTK.TTF"),
    ("方正姚体", r"C:\Windows\Fonts\FZYTK.TTF"),
    ("宋体",     r"C:\Windows\Fonts\simsun.ttc"),
    ("黑体",     r"C:\Windows\Fonts\simhei.ttf"),
]


def font_family_name(path: str) -> str:
    """用 fontTools 读取字体族名（优先 typographic family），失败退回文件名。"""
    try:
        from fontTools.ttLib import TTCollection, TTFont
        if path.lower().endswith(".ttc"):
            font = TTCollection(path, lazy=True).fonts[0]
        else:
            font = TTFont(path, fontNumber=0, lazy=True)
        name = font["name"]
        for nid in (16, 1):
            rec = name.getDebugName(nid)
            if rec:
                return rec.strip()
    except Exception:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def _scan_dir(directory: str):
    out = []
    if os.path.isdir(directory):
        for fn in sorted(os.listdir(directory)):
            if fn.lower().endswith((".ttf", ".ttc", ".otf")):
                p = os.path.join(directory, fn)
                # 显示名用文件名：手写字体的元数据族名常是拼音缩写（如 YYT）
                out.append((os.path.splitext(fn)[0], p))
    return out


def available_fonts():
    """返回 [(显示名, 路径), ...]：项目 fonts/ 目录优先，内置候选兜底。"""
    fonts = _scan_dir(PROJECT_FONTS_DIR)
    seen = {os.path.normcase(p) for _, p in fonts}
    for name, path in CANDIDATE_FONTS:
        if os.path.exists(path) and os.path.normcase(path) not in seen:
            fonts.append((name, path))
            seen.add(os.path.normcase(path))
    return fonts


# 写错字时的随机替身字（常用字表摘选）
COMMON_CHARS = (
    "的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和要她出也得里后自以会"
    "家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美总"
    "从无情己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间斯知世"
    "什两次使身者被高已亲其进此话常与活正感"
)
