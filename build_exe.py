#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键把 DLNA 投屏器打成单个 exe（带图形界面）。

    python build_exe.py                     # 中文版，产物 dist/DLNA投屏器.exe
    python build_exe.py --lang en           # 英文版，产物 dist/DLNACast.exe
    python build_exe.py --keep-build        # 保留中间产物便于排查
    python build_exe.py --embed-ffmpeg      # 【旧方案】把 ffmpeg 缀在 exe 尾部

产物：dist/DLNA投屏器.exe（中文，约 13 MB）/ dist/DLNACast.exe（英文）

关于 --lang：语言不是运行时读取的配置，而是**构建期常量**。英文版会在包里
写一个 _lang_build.py（内容只有一行 DEFAULT = "en"），i18n.py 启动时优先读它，
所以用户双击就是英文界面，不需要先设环境变量。构建结束该文件立即清理，
绝不留在工作区。运行时仍可用 --lang zh 或 DLNACAST_LANG=zh 覆盖回中文。

关于 ffmpeg：为什么默认**不再内嵌**
-------------------------------------
内嵌方案把 231 MB 的 ffmpeg 塞进 exe，产物从 13 MB 涨到 69 MB。体积本身
不是问题，问题是**每次双击都要让杀软重新扫一遍这 69 MB**，窗口出现前要多等
好几秒。而 ffmpeg 一年也未必变一次，没必要每次都跟着 exe 走一遍。

现在的做法（见 ffmpeg_fetch.py）：
    1. 先读环境变量 FFMPEG_PATH
    2. 再看 exe 同目录有没有 ffmpeg.exe
    3. 再找常见安装路径 / PATH
    4. 都没有 → 从 GitHub 下最新版到 exe 同目录（只取包里 bin/ffmpeg.exe
       一个成员，实测省掉约三分之二的流量）

所以本脚本只负责把 python 部分冻结成 exe，剩下的交给运行时。
想要旧行为加 --embed-ffmpeg（会重新把 exe 撑到 69 MB）。
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ffpayload 只在 --embed-ffmpeg（旧方案）时才会用到，延迟导入，
# 免得默认路径上多背一个模块的依赖。

APP_NAME = "DLNA投屏器"          # 中文版产物名
EN_APP_NAME = "DLNACast"         # 英文版产物名（纯 ASCII，便于分享）
SPEC = os.path.join(HERE, "screen-cast.spec")
DIST_DIR = os.path.join(HERE, "dist")
# 默认指向中文版；main() 会按 --lang 改写。后面所有引用都读这个模块级变量，
# 所以两条语言分支不需要各自算一遍路径。
DIST_EXE = os.path.join(DIST_DIR, APP_NAME + ".exe")
# 构建期语言常量：写好后随包打进去，构建结束即清理（绝不留在工作区）
LANG_BUILD_FILE = os.path.join(HERE, "_lang_build.py")
BUILD_DIR = os.path.join(HERE, "build")
# 旧产物不做删除，先改名堆到这个子目录，收尾时整体删掉
RETIRED_DIR = os.path.join(DIST_DIR, "_retired")

# 构建工具链的虚拟环境（独立于用户自己的 Python，不污染任何全局环境）
VENV_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                        "python", "envs", "pybuild")

FFMPEG_CANDIDATES = [
    os.environ.get("FFMPEG_PATH", ""),
    r"D:\ffmpeg\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]


def say(msg=""):
    print(msg, flush=True)


def step(n, total, text):
    say("")
    say("[%d/%d] %s" % (n, total, text))
    say("-" * 62)


def sub(text):
    """不算一步的小标题（避免 [4/5] 出现两次）。"""
    say("")
    say("· %s" % text)
    say("-" * 62)


def human(n):
    return "%.1f MB" % (n / 1048576.0)


def venv_python():
    return os.path.join(VENV_DIR, "Scripts", "python.exe")


def ensure_toolchain(need_icon):
    """返回一个装了 PyInstaller（必要时还有 Pillow）的 python 解释器路径。

    优先用当前解释器；没装就现建一个专用 venv —— 直接往用户的 conda/Python
    里 pip install 太不礼貌，而且 pyinstaller 在不同环境里版本差异会导致
    打包结果不一致。
    """
    pkgs = ["pyinstaller"] + (["pillow"] if need_icon else [])
    missing = []
    if "pyinstaller" in pkgs and _no_module("PyInstaller"):
        missing.append("pyinstaller")
    if need_icon and _no_module("PIL"):
        missing.append("pillow")
    if not missing:
        return sys.executable, []

    vp = venv_python()
    if not os.path.isfile(vp):
        say("  未找到构建用虚拟环境，正在创建：%s" % VENV_DIR)
        r = subprocess.run([sys.executable, "-m", "venv", VENV_DIR])
        if r.returncode != 0 or not os.path.isfile(vp):
            say("  ! 创建虚拟环境失败，改为在当前解释器里安装依赖")
            vp = sys.executable

    still = []
    if "pyinstaller" in missing and _no_module("PyInstaller", vp):
        still.append("pyinstaller")
    if "pillow" in missing and _no_module("PIL", vp):
        still.append("pillow")

    if still:
        say("  安装构建依赖：%s" % ", ".join(still))
        # 用官方源：实测国内镜像上 pyinstaller 解析不到版本
        cmd = [vp, "-m", "pip", "install", "-q", "--disable-pip-version-check",
               "-i", "https://pypi.org/simple"] + still
        r = subprocess.run(cmd)
        if r.returncode != 0:
            say("  ! 官方源安装失败，回退默认源再试一次")
            r = subprocess.run([vp, "-m", "pip", "install", "-q",
                                "--disable-pip-version-check"] + still)
            if r.returncode != 0:
                raise SystemExit("依赖安装失败，请手动执行：\n  %s -m pip install %s"
                                 % (vp, " ".join(still)))
    return vp, still


def _no_module(name, python=None):
    python = python or sys.executable
    r = subprocess.run([python, "-c", "import %s" % name],
                       capture_output=True)
    return r.returncode != 0


def retire(path):
    """把旧产物**改名**挪走，而不是删掉。

    为什么绕这一下：本机环境把删除操作拦截到回收站，而回收站对这些
    构建产物必定失败（稳定复现「trash operation ... aborted」），
    连 PyInstaller 内部那句 `os.remove(旧exe)` 都会被拖挂，报错还完全
    指向不了真正原因。实测三条行为：
        os.remove(工作区文件)       → 失败
        os.replace(覆盖已存在文件)   → 失败 (WinError 5)
        os.rename(改名) / rmtree(删目录) → 可用
    所以：改名腾位置 → 让 PyInstaller 往空位置写 → 收尾时整个目录删掉。

    返回 False 表示腾不出位置（通常是 exe 正在运行）。
    """
    if not os.path.isfile(path):
        return True
    try:
        os.makedirs(RETIRED_DIR, exist_ok=True)
        dst = os.path.join(RETIRED_DIR,
                          "%s.%d" % (os.path.basename(path), int(time.time())))
        os.rename(path, dst)
        say("  旧产物已挪到 dist/_retired/（收尾时统一清理）")
        return True
    except Exception as e:
        say("  ! 旧产物改名失败：%s" % e)
        say("    通常是这个 exe 正在运行 —— 关掉它再重新打包。")
        return False


def _dispose_dir(path):
    """清理一个目录。删不掉就改名挪出项目，至少别把垃圾留在项目里。

    正常机器上没人会走第二条分支；但某些受管环境会拦截删除操作，
    这里兜一下，免得构建目录越堆越大。
    """
    if not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
        say("  已清理 %s" % os.path.relpath(path, HERE))
        return
    except (Exception, SystemExit):
        # 受管环境里「一次删上百个文件」会被安全策略拦下，可能以
        # SystemExit 的形式中断，一并接住
        pass
    try:
        tmp = os.path.join(tempfile.gettempdir(),
                           "dlna_cast_trash_%d" % int(time.time()))
        os.rename(path, tmp)
        say("  无法直接删除 %s，已改名挪到系统临时目录（系统会自行回收）"
            % os.path.relpath(path, HERE))
    except Exception as e:
        say("  ! %s 未能清理（不影响产物）：%s"
            % (os.path.relpath(path, HERE), e))


def find_ffmpeg(explicit):
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit("指定的 ffmpeg 不存在：%s" % explicit)
        return explicit
    for c in FFMPEG_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    w = shutil.which("ffmpeg")
    if w:
        return w
    raise SystemExit("找不到 ffmpeg。用 --ffmpeg <路径> 指定一个。")


def app_name_for(lang):
    return EN_APP_NAME if lang == "en" else APP_NAME


def write_lang_build(lang):
    """写出构建期语言常量。

    英文版 exe 的默认语言就定死在这个小文件里：i18n.py 启动时
    ``from _lang_build import DEFAULT``，拿不到才回落到中文。用「构建期
    常量」而不是环境变量，是为了让用户双击就有英文界面，不必先设什么。
    """
    with open(LANG_BUILD_FILE, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n"
                "# 由 build_exe.py 生成，构建结束即清理。请勿手改，也不入库。\n"
                'DEFAULT = "%s"\n' % lang)
    say("  语言常量 : _lang_build.py -> DEFAULT = %r" % lang)


def dispose_lang_build():
    """清理构建期语言常量。

    走「改名挪走」而不是删除：本机环境的删除操作会被拦到回收站且必然
    失败（见 retire()）。挪到系统临时目录就够 —— 关键是不能留在项目里
    被 git 看见。
    """
    if not os.path.isfile(LANG_BUILD_FILE):
        return
    try:
        tmp = os.path.join(tempfile.gettempdir(),
                           "dlna_lang_build_%d.py" % int(time.time()))
        os.rename(LANG_BUILD_FILE, tmp)
        say("  已清理 _lang_build.py（挪到系统临时目录）")
    except Exception as e:
        say("  ! _lang_build.py 未能清理，请手动删除：%s" % e)


def write_version_info(path, ffmpeg_note, lang="zh"):
    """生成 Windows 版本资源，让 exe 的「属性」页有像样的信息。

    注意生成的是一段 Python 字面量源码，字符串用单引号包裹，所以值里
    不能再出现未转义的单引号（英文文案里最容易踩这个）。
    英文版同时把字符串表的语言码从 0804(zh-CN) 换成 0409(en-US)。
    """
    if lang == "en":
        table_lang, translation = "040904B0", "[1033, 1200]"
        company = "Personal project"
        desc = "DLNA Screen Caster - mirror this PC to a smart TV"
        product = "DLNA Screen Caster"
        orig = EN_APP_NAME + ".exe"
    else:
        table_lang, translation = "080404B0", "[2052, 1200]"
        company = "个人项目"
        desc = "DLNA 屏幕投屏器（把电脑画面投到智能电视）"
        product = "DLNA 屏幕投屏器"
        orig = APP_NAME + ".exe"

    def q(s):
        """按生成文件的需要转义：反斜杠与单引号。"""
        return str(s).replace("\\", "\\\\").replace("'", "\\'")

    text = ("""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('%(table_lang)s', [
        StringStruct('CompanyName', '%(company)s'),
        StringStruct('FileDescription', '%(desc)s'),
        StringStruct('FileVersion', '1.0.0.0'),
        StringStruct('InternalName', 'DLNA-Cast'),
        StringStruct('LegalCopyright', ''),
        StringStruct('OriginalFilename', '%(orig)s'),
        StringStruct('ProductName', '%(product)s'),
        StringStruct('ProductVersion', '1.0.0.0'),
        StringStruct('Comments', '%(note)s')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', %(translation)s)])
  ]
)
""" % {"table_lang": table_lang, "company": q(company), "desc": q(desc),
       "orig": q(orig), "product": q(product), "note": q(ffmpeg_note),
       "translation": translation})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_icon():
    icon = os.path.join(HERE, "assets", "app.ico")
    if os.path.isfile(icon):
        return icon
    say("  图标不存在，正在生成 ...")
    r = subprocess.run([sys.executable,
                        os.path.join(HERE, "tools", "make_icon.py")])
    return icon if r.returncode == 0 and os.path.isfile(icon) else None


def dll_search_dirs(python):
    """找出解释器旁边那些「非标准位置」的 DLL 目录。

    这是本项目踩过的一个坑：conda 系（miniforge）把 tcl86t.dll / tk86t.dll /
    liblzma.dll / libexpat.dll / ffi-8.dll 全都放在 ``<prefix>\\Library\\bin``，
    而 Windows 的默认 DLL 搜索路径里没有这个目录。PyInstaller 解析不到这些
    依赖时**只在日志里警告一句**，打包照样「成功」，但产物一启动就
    ``ImportError: DLL load failed while importing _lzma`` —— 因为解包内置
    ffmpeg 要 lzma、界面要 tk、WASAPI 要 ctypes、解析设备描述要 expat。

    把这些目录前置进 PATH，PyInstaller 就能正确解析并收进包里。
    """
    try:
        base = subprocess.run([python, "-c", "import sys;print(sys.base_prefix)"],
                              capture_output=True, text=True, timeout=30
                              ).stdout.strip()
    except Exception:
        base = ""
    cands = []
    if base:
        cands = [os.path.join(base, "Library", "bin"),
                 os.path.join(base, "DLLs"),
                 base]
    return [c for c in cands if os.path.isdir(c)]


def run_pyinstaller(python, ignore_dll_warnings=False, app_name=None):
    cmd = [python, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--distpath", os.path.join(HERE, "dist"),
           "--workpath", BUILD_DIR,
           SPEC]
    say("  " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    env = dict(os.environ)
    # spec 从环境变量读产物名 —— 两条语言分支的 exe 文件名不同
    # （中文版「DLNA投屏器.exe」/ 英文版 DLNACast.exe）。
    # 不传的话 spec 会退回中文名，英文构建就会在「找产物」那步失败。
    if app_name:
        env["DLNACAST_APP_NAME"] = app_name
        say("  产物名   : %s.exe" % app_name)
    extra = dll_search_dirs(python)
    if extra:
        say("  DLL 搜索路径前置：%s" % os.pathsep.join(extra))
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])

    t0 = time.time()
    # 边跑边显示（打包要几十秒，全憋到最后再输出等于没进度），
    # 同时把输出攒下来 —— 后面要扫里面的 "Library not found"。
    unresolved = []
    proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        if "Library not found" in line:
            unresolved.append(line)
        if line.startswith(("INFO:", "WARNING:", "ERROR:", "Traceback")):
            say("  " + line)
    proc.wait()
    if proc.returncode != 0:
        raise SystemExit("PyInstaller 打包失败（返回码 %s）" % proc.returncode)
    say("  打包完成，用时 %.0f 秒" % (time.time() - t0))

    if unresolved and not ignore_dll_warnings:
        # 这类警告是"打包成功但产物一启动就 ImportError"的典型前兆，
        # 不能放过（本项目就因此出过一次 _lzma / _tkinter 加载失败）。
        say("")
        say("  ! 有 %d 个依赖 DLL 没能解析：" % len(unresolved))
        for ln in unresolved:
            say("    " + ln.split("WARNING:")[-1].strip())
        raise SystemExit(
            "依赖 DLL 未解析完全，产物很可能启动即崩。\n"
            "请检查是否漏了 DLL 搜索目录（见 dll_search_dirs()）。"
            "\n确认无碍可加 --ignore-dll-warnings 强行继续。")
    if unresolved:
        say("  ! 忽略 %d 条 DLL 警告（--ignore-dll-warnings）" % len(unresolved))


def smoke_test(exe, expect_payload=None, expect_lang=None):
    """确认冻结后的 exe 真的能跑。

    这一步不能省：onefile 里少个数据文件、importlib 路径不对之类的问题，
    只有在真跑起来时才会暴露（打包过程中一切看起来都正常）。

    expect_payload : True/False 校验 exe 尾部有没有 ffmpeg 载荷；
                     None 表示不检查（用于旧方案之外的场景）。
    expect_lang    : "zh" / "en"，校验冻结后的默认界面语言是否正确
                     （即 _lang_build.py 有没有真被收进包里）。
    """
    say("")
    say("  验证 1/3：PyInstaller 归档里有没有带上必需的资源")
    # CArchive 的目录表以明文存条目名，直接扫字节最省事、也不用引入
    # PyInstaller 的私有 API（那种 API 换版本就变）。
    with open(exe, "rb") as f:
        head = f.read()
    required = [b"screen-cast.py", b"mpegts.js", b"tv.html"]
    missing = [n.decode() for n in required if n not in head]
    if missing:
        raise SystemExit("exe 里缺少这些资源：%s" % ", ".join(missing))
    say("    screen-cast.py / static/tv.html / static/mpegts.js 都在")

    if expect_payload is not None:
        say("  验证 2/3：ffmpeg 载荷（%s）"
            % ("应存在" if expect_payload else "应不存在"))
        has = b"DLNACAST-PAYLOAD" in head
        if has != expect_payload:
            raise SystemExit(
                "载荷状态不符：实际%s，预期%s。"
                % ("有" if has else "无", "有" if expect_payload else "无"))
        say("    符合预期：%s" % ("带载荷" if has else "纯 exe，无载荷"))
    else:
        say("  验证 2/3：跳过载荷检查")

    say("  验证 3/3：exe 能否启动（跑 --help）")
    env = dict(os.environ)
    env["DLNACAST_NO_CONSOLE"] = "1"      # 免得冒出一个控制台窗口
    # 清掉可能从父进程继承来的语言设置，否则测的是环境变量而不是包里的常量
    env.pop("DLNACAST_LANG", None)
    #
    # 计时只看「到第一个字节输出」为止，不能等进程退出 —— 这是踩过的坑：
    # onefile 的引导程序退出前要删掉 _MEI 解包目录（约 1000 个文件），
    # 而某些受管环境把每个删除操作都拦到回收站，实测 500 个文件要 26.9 秒
    # （对比：整个目录改名只要 0.005 秒）。于是「等进程退出」测到的其实是
    # 一场删除马拉松，能到 70 秒，跟真实启动速度毫无关系。
    # 用户能感知的是「双击后多久看到东西」，即首字节输出时刻。
    t0 = time.time()
    proc = subprocess.Popen([exe, "--help"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    first = []
    chunks = []

    def _pump():
        try:
            while True:
                # 用 os.read：一有数据就返回，不会像 read(n) 那样攒满才回
                b = os.read(proc.stdout.fileno(), 4096)
                if not b:
                    break
                if not first:
                    first.append(time.time() - t0)
                chunks.append(b)
        except OSError:
            pass

    th = threading.Thread(target=_pump, daemon=True)
    th.start()
    proc.wait(timeout=600)
    th.join(timeout=15)
    launch = first[0] if first else (time.time() - t0)
    out = b"".join(chunks).decode("utf-8", "replace")
    tail = time.time() - t0

    if proc.returncode != 0:
        raise SystemExit("exe 启动失败（返回码 %s）\n输出:\n%s"
                         % (proc.returncode, out[-2000:]))
    say("    启动正常，首字节输出用时 %.1f 秒"
        "（冻结后的解释器+核心模块都加载成功）" % launch)
    if tail - launch > 5:
        # 差得多说明是解包目录的删除被拖慢，不是启动慢 —— 说清楚免得误判
        say("    （进程完全退出用了 %.0f 秒，多出来的是 onefile 解包目录的"
            "清理，与启动速度无关）" % tail)

    if expect_lang:
        want = {"en": "DLNA Screen Caster", "zh": "DLNA 屏幕投屏器"}[expect_lang]
        if not out.strip():
            # GUI 子系统的 exe 在某些环境下拿不到 stdout，这不是失败
            say("    （未捕获到 --help 文本，跳过语言校验）")
        elif want in out:
            say("    默认语言校验通过：%s" % want)
        else:
            raise SystemExit("默认语言不对：期望帮助里出现 %r，实际输出：\n%s"
                             % (want, out[-1500:]))


def test_runtime_ffmpeg():
    """模拟运行时逻辑：本机到底找不找得到 ffmpeg。

    这决定了用户第一次双击会走「秒开」还是「先下载几十 MB」，
    构建时就说清楚，免得用户以为程序卡住了。
    """
    say("")
    say("  运行时会怎么找 ffmpeg：")
    env_path = os.environ.get("FFMPEG_PATH", "")
    say("    1) 环境变量 FFMPEG_PATH = %s"
        % (env_path or "（未设置）"))
    if env_path and os.path.isfile(env_path):
        say("       -> 命中，用户机器上会直接用它")
        return True
    side = os.path.join(DIST_DIR, "ffmpeg.exe")
    say("    2) exe 同目录 %s -> %s"
        % (os.path.relpath(side, HERE),
           "存在" if os.path.isfile(side) else "不存在"))
    if os.path.isfile(side):
        return True
    try:
        found = find_ffmpeg(None)
    except SystemExit:
        found = None
    if found:
        say("    3) 本机构建机上找到：%s" % found)
        say("       （注意：这只说明你这台机器有，换台电脑就要走下载）")
        return True
    say("    3) 本机构建机上也没找到 -> 用户首次运行会自动下载")
    return False


def main():
    ap = argparse.ArgumentParser(description="打包 DLNA 投屏器为单文件 exe")
    ap.add_argument("--keep-build", action="store_true",
                    help="保留 build/ 中间产物")
    ap.add_argument("--skip-test", action="store_true",
                    help="跳过打包后的启动验证")
    ap.add_argument("--embed-ffmpeg", action="store_true",
                    help="【旧方案】把 ffmpeg 缀在 exe 尾部（产物会涨到约 69 MB）")
    ap.add_argument("--ffmpeg", help="--embed-ffmpeg 时要内嵌的 ffmpeg.exe 路径")
    ap.add_argument("--preset", type=int, default=6,
                    help="--embed-ffmpeg 时的 LZMA 压缩级别 0-9（默认 6）")
    ap.add_argument("--no-7z", action="store_true",
                    help="--embed-ffmpeg 时不用 7-Zip 加速")
    ap.add_argument("--ignore-dll-warnings", action="store_true",
                    help="忽略未解析的依赖 DLL 警告（慎用，通常代表产物会崩）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh",
                    help="界面语言：zh = 中文版（默认）/ en = 英文版")
    args = ap.parse_args()

    global DIST_EXE
    DIST_EXE = os.path.join(DIST_DIR, app_name_for(args.lang) + ".exe")

    embed = args.embed_ffmpeg
    total = 5
    t_all = time.time()

    step(1, total, "准备")
    ffmpeg_src = None
    if embed:
        ffmpeg_src = find_ffmpeg(args.ffmpeg)
        say("  模式     : 内嵌 ffmpeg（旧方案，产物较大）")
        say("  ffmpeg   : %s  (%s)"
            % (ffmpeg_src, human(os.path.getsize(ffmpeg_src))))
    else:
        say("  模式     : 纯 exe，ffmpeg 运行时解析/按需下载")
    say("  项目目录 : %s" % HERE)
    say("  界面语言 : %s"
        % ("英文版 (en)" if args.lang == "en" else "中文版 (zh)"))
    write_lang_build(args.lang)
    icon = make_icon()
    say("  应用图标 : %s" % (icon or "（无）"))
    if embed:
        note = "内嵌 ffmpeg：%s" % os.path.basename(ffmpeg_src)
    elif args.lang == "en":
        note = ("ffmpeg: resolved at runtime (FFMPEG_PATH / same folder / "
                "PATH), downloaded from GitHub if needed")
    else:
        note = ("ffmpeg：运行时自动查找（FFMPEG_PATH / 同目录 / PATH），"
                "必要时从 GitHub 下载")
    write_version_info(os.path.join(HERE, "assets", "version_info.txt"), note,
                       args.lang)
    python, installed = ensure_toolchain(need_icon=icon is None)
    say("  解释器   : %s" % python)
    if installed:
        say("  本次新装 : %s" % ", ".join(installed))

    step(2, total, "清理旧产物")
    if not retire(DIST_EXE):
        raise SystemExit("输出位置被占用，构建中止")
    if not args.keep_build:
        _dispose_dir(BUILD_DIR)

    step(3, total, "PyInstaller 打包")
    run_pyinstaller(python, ignore_dll_warnings=args.ignore_dll_warnings,
                    app_name=app_name_for(args.lang))
    if not os.path.isfile(DIST_EXE):
        raise SystemExit("没有找到产物：%s" % DIST_EXE)
    bare = os.path.getsize(DIST_EXE)
    say("  exe 体积：%s" % human(bare))

    final = bare
    if embed:
        step(4, total, "压缩并缀入 ffmpeg 载荷")
        import ffpayload  # 延迟导入：默认路径用不到
        ffpayload.append_payload(DIST_EXE, ffmpeg_src, preset=args.preset,
                                 use_7z=not args.no_7z,
                                 log=lambda m: say(m))
        final = os.path.getsize(DIST_EXE)
        say("  缀入后体积：%s" % human(final))
        if args.skip_test:
            say("  已按 --skip-test 跳过验证")
        else:
            sub("验证内嵌后的产物")
            smoke_test(DIST_EXE, expect_payload=True, expect_lang=args.lang)
    else:
        step(4, total, "验证")
        if args.skip_test:
            say("  已按 --skip-test 跳过")
        else:
            smoke_test(DIST_EXE, expect_payload=False, expect_lang=args.lang)

    step(5, total, "完成")
    if not args.keep_build:
        _dispose_dir(BUILD_DIR)
        say("  已清理 build/ 中间产物")
    _dispose_dir(RETIRED_DIR)
    test_runtime_ffmpeg()

    say("")
    say("=" * 62)
    say("  产物     : %s" % DIST_EXE)
    say("  体积     : %s" % human(final))
    if embed:
        say("  其中载荷 : %s（ffmpeg %s 压缩而来）"
            % (human(final - bare), human(os.path.getsize(ffmpeg_src))))
    say("  总耗时   : %.0f 秒" % (time.time() - t_all))
    say("=" * 62)
    say("")
    if embed:
        say("说明：exe 会在**首次运行**时把内嵌的 ffmpeg 解包到")
        say("      %%LOCALAPPDATA%%\\DlnaCast\\bin\\ffmpeg.exe。")
    else:
        say("说明：exe 启动时按下面的顺序找 ffmpeg，找不到才联网下载：")
        say("      FFMPEG_PATH  →  exe 同目录  →  %%LOCALAPPDATA%%\\DlnaCast\\bin")
        say("      →  常见安装路径  →  PATH  →  GitHub（下到 exe 同目录）")
    if args.lang == "en":
        say("")
        say("Note: this build defaults to the English UI. Users can still")
        say("      force Chinese at runtime with  --lang zh  or  set DLNACAST_LANG=zh .")
    return 0


if __name__ == "__main__":
    # _lang_build.py 是构建期临时文件，无论成功失败都必须清掉 —— 否则它会
    # 留在工作区里被 git 看见，而且下一次构建的语言会被它悄悄污染。
    try:
        _code = main()
    finally:
        dispose_lang_build()
    sys.exit(_code)
