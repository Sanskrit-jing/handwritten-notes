# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('backgrounds', 'backgrounds'), ('ico', 'ico'),
           ('web/dist', 'web/dist')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['onnxruntime', 'pikepdf', 'PIL._avif'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 排除可选依赖的残留二进制与软件 OpenGL 兜底（桌面 Widgets 程序无需）
a.binaries = [b for b in a.binaries if not any(
    k in b[0] for k in ('onnxruntime', 'pikepdf', 'opengl32sw', 'PIL/_avif'))]

# 排除开发期残留在源 backgrounds/user 下的用户模板（运行时由数据根自建）
a.datas = [d for d in a.datas
           if 'backgrounds/user' not in d[1].replace('\\', '/')]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HandwrittenNotes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ico/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HandwrittenNotes',
)
