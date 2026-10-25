# -*- mode: python ; coding: utf-8 -*-
"""ArchCode 打包配置(onedir,无控制台,双击 = 桌面窗口)。

数据落点对应源码引用:
- web/dist          -> webui/server.py 的 _DIST(应用根/web/dist)
- archcode/styles.tcss -> textual CSS_PATH
- archcode/skills/builtin -> skills/loader.py 内置技能目录
- archcode/agents/builtins -> agents/loader.py importlib.resources
"""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("web/dist", "web/dist"),
    ("archcode/styles.tcss", "archcode"),
    ("archcode/skills/builtin", "archcode/skills/builtin"),
    ("archcode/agents/builtins", "archcode/agents/builtins"),
]
binaries = []
hiddenimports = [
    "tkinter",
    "tkinter.filedialog",
    "tkinter.commondialog",
    "tkinter.messagebox",
]

# pywebview 的平台后端是动态导入的,需要整包收集
d, b, h = collect_all("webview")
datas += d
binaries += b
hiddenimports += h

a = Analysis(
    ["run_archcode.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ArchCode",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ArchCode",
)
