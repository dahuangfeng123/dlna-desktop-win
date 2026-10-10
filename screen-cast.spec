# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：DLNA 投屏器 → 单个 exe。

产物**不含 ffmpeg**，也不含任何 ffmpeg 载荷。运行期按这个顺序找：
    环境变量 FFMPEG_PATH  →  exe 同目录  →  %LOCALAPPDATA%\\DlnaCast\\bin
    →  常见安装路径  →  PATH  →  从 GitHub 下载到 exe 同目录
（查找顺序定义在 screen-cast.py 的 find_ffmpeg()，下载在 ffmpeg_fetch.py）

为什么不内嵌：把 231 MB 的 ffmpeg 塞进 exe 会让产物从 13 MB 涨到 69 MB，
每次双击都要让杀软重扫一遍，窗口出现前要多等几秒 —— 而 ffmpeg 本身
一年也未必变一次，没必要每次跟着 exe 走。

用的命令：
    python -m PyInstaller --noconfirm --clean screen-cast.spec
一般不用直接调，走 build_exe.py 即可。
"""

import os

HERE = SPECPATH          # PyInstaller 注入：spec 文件所在目录
# 产物名由 build_exe.py 用环境变量传进来（中文版叫「DLNA投屏器.exe」，
# 英文版叫 DLNACast.exe）。不带这个变量直接跑 PyInstaller 时退回中文名。
APP_NAME = os.environ.get("DLNACAST_APP_NAME") or "DLNA投屏器"

# ------------------------------------------------------------------ 数据文件
# screen-cast.py 是**数据文件**而不是模块：文件名带连字符没法 import，
# 运行时由 cast_gui 按路径加载。因此它的依赖 PyInstaller 静态分析看不到，
# 必须在下面 hiddenimports 里手动列全。
datas = [
    (os.path.join(HERE, "screen-cast.py"), "."),
    (os.path.join(HERE, "static", "tv.html"), "static"),
    (os.path.join(HERE, "static", "mpegts.js"), "static"),
]

# screen-cast.py 用到、但静态分析扫不到的标准库。
# ffmpeg_fetch 是 cast_gui 直接 import 的（静态分析能看到），列在这里只是
# 为了万一它哪天改成动态加载时不会漏。
hiddenimports = [
    "argparse", "atexit", "collections", "ctypes", "ctypes.wintypes",
    "http.server", "shutil", "socket", "subprocess", "tempfile",
    "threading", "urllib.request", "webbrowser", "zlib", "zipfile",
    "xml.etree.ElementTree", "xml.sax.saxutils",
    "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.scrolledtext",
    "ffmpeg_fetch",
    # 界面文案双语。screen-cast.py 是数据文件，它的 import 静态分析看不到，
    # 所以要在这里显式列上。
    "i18n",
    # 构建期生成的语言常量（build_exe.py 写入，构建完即清理）。
    # 英文版 exe 就是靠它把默认语言定死成 en。
    "_lang_build",
]

# 明显用不到又特别占体积的，排除掉
excludes = [
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "PyInstaller",
    "pytest", "setuptools", "pip", "wheel", "IPython", "notebook",
    "sqlite3", "distutils", "test",
]

_ver = os.path.join(HERE, "assets", "version_info.txt")
_icon = os.path.join(HERE, "assets", "app.ico")

a = Analysis(
    [os.path.join(HERE, "cast_gui.py")],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX 压过的 exe 极易被杀软误报，关掉
    runtime_tmpdir=None,
    # GUI 程序：双击不弹黑框。带参数运行时 cast_gui 会自己 AllocConsole()
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon if os.path.isfile(_icon) else None,
    version=_ver if os.path.isfile(_ver) else None,
)
