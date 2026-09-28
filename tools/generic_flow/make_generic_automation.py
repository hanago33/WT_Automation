# -*- coding: utf-8 -*-
"""从 WT_AUT_recorded.py 生成通用化副本 generic_automation.py（去 WT/MUP 绑定）。

只复制 + 文本替换，**不修改原项目文件**。通用化要点：
- import wt_flow_locator/wt_flow_executor -> alias 成通用副本 generic_flow_locator/generic_flow_executor
- DEFAULT_GM_EXE / MAIN_WINDOW_TITLE_RE / MAIN_WINDOW_UIPATH / ("MUPSmartClient",)
  全部改为由 config_generic_target_app(...) 注入；未配置时纯靠进程名关键词识别目标窗口
- preflight 日志去 mup- 前缀
原项目其余 wt_* 通用基础设施模块直接复用（同环境已存在）。

运行：python tools/generic_flow/make_generic_automation.py
"""
import io
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(ROOT, "WT_AUT_recorded.py")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generic_automation.py")


def read_text(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def write_text(p, t):
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(t)


def main():
    t = read_text(SRC)

    # 1) alias 通用副本（其余 2700 行逻辑不动，仍引用 wt_flow_locator / wt_flow_executor 名字）
    #    注意先处理带 `as` 的形式，避免 blanket 替换产生 `... as wt_flow_locator as flow_locator` 非法语法
    t = t.replace("import wt_flow_locator as flow_locator", "import generic_flow_locator as flow_locator")
    t = t.replace("import wt_flow_locator", "import generic_flow_locator as wt_flow_locator")
    t = t.replace("import wt_flow_executor as flow_locator", "import generic_flow_executor as flow_locator")
    t = t.replace("import wt_flow_executor", "import generic_flow_executor as wt_flow_executor")

    # 2) 常量去 WT 写死
    t = t.replace(
        'DEFAULT_GM_EXE = r"C:\\Program Files\\Meteodyn\\MeteodynUniverse\\MUPSmartClient.exe"',
        'DEFAULT_GM_EXE = ""  # 通用化：默认空，由 config_generic_target_app 注入目标软件路径',
    )
    t = t.replace(
        '# 目标软件：WT Meteodyn Universe（MUPSmartClient.exe）\n'
        '# 主窗口标题实测为 "Meteodyn Universe / v1.10.1.0"（窗口类 CASCADIA_HOSTING_WINDOW_CLASS）\n'
        'MAIN_WINDOW_TITLE_RE = re.compile(r"Meteodyn Universe")',
        '# 目标软件（通用化）：由 config_generic_target_app 配置，不再写死 WT/MUP\n'
        '# 默认标题正则永不命中，纯靠 class_name_keywords（进程名/窗口类名）识别目标主窗\n'
        'MAIN_WINDOW_TITLE_RE = re.compile(r"(?!)")',
    )
    t = t.replace(
        'MAIN_WINDOW_UIPATH = u"Meteodyn Universe / v1.10.1.0||Window"',
        'MAIN_WINDOW_UIPATH = u""  # 通用化：默认空（投影业务专用，通用场景不依赖）',
    )

    # 3) 注入通用配置入口 + TARGET_CLASS_KEYWORDS（插在 MAIN_WINDOW_UIPATH 之后）
    cfg_block = '''

# ───────────────────────────────────────────────────────────────────────────
# 通用化配置入口（去 WT/MUP 绑定）
# ───────────────────────────────────────────────────────────────────────────
TARGET_CLASS_KEYWORDS = ()  # 目标进程名/窗口类名关键词，由 config_generic_target_app 注入


def config_generic_target_app(exe=None, title_re=None, uipath=None, class_keywords=()):
    """通用化：配置目标软件识别参数（替代原 WT/MUP 写死值）。

    Args:
        exe: 目标软件可执行文件路径（如 r"C:\\\\MyApp\\\\app.exe"），用于进程名识别与提权检测。
        title_re: 主窗口标题正则字符串（可选）；不传则纯靠 class_keywords 识别。
        uipath: 主窗口 UIPath（投影等业务专用，通用场景一般不传）。
        class_keywords: 进程名/窗口类名关键词元组，如 ("myapp",)。定位与预检都靠它。
    """
    global GM_EXE, MAIN_WINDOW_TITLE_RE, MAIN_WINDOW_UIPATH, TARGET_CLASS_KEYWORDS
    if exe is not None:
        GM_EXE = exe
    if title_re:
        MAIN_WINDOW_TITLE_RE = re.compile(title_re)
    if uipath is not None:
        MAIN_WINDOW_UIPATH = uipath
    TARGET_CLASS_KEYWORDS = tuple(class_keywords or ())
    # 同步通用化定位器（generic_flow_locator 已 alias 为 wt_flow_locator）
    try:
        wt_flow_locator.config_generic_target(
            window_keywords=TARGET_CLASS_KEYWORDS,
            process_keywords=TARGET_CLASS_KEYWORDS,
            activate_process_name=(TARGET_CLASS_KEYWORDS[0] if TARGET_CLASS_KEYWORDS else ""),
            main_window_process=(GM_EXE or ""),
        )
    except Exception:
        pass

'''
    t = t.replace(
        'MAIN_WINDOW_UIPATH = u""  # 通用化：默认空（投影业务专用，通用场景不依赖）',
        'MAIN_WINDOW_UIPATH = u""  # 通用化：默认空（投影业务专用，通用场景不依赖）' + cfg_block,
        1,
    )

    # 4) 进程名关键词去 MUP 写死
    t = t.replace('("MUPSmartClient",)', "TARGET_CLASS_KEYWORDS")

    # 5) 注释/文案去 MUP（仅文档与提示，不影响逻辑）
    t = t.replace(
        '"""提供目标软件(MUP)主窗口候选（hwnd 字典列表），供定位器 fallback 权威取窗。',
        '"""提供目标软件主窗口候选（hwnd 字典列表），供定位器 fallback 权威取窗。',
    )
    t = t.replace(
        "\t复用预检同款 find_main_windows：按进程名 MUPSmartClient 识别主窗，UIPI 免疫，",
        "\t复用预检同款 find_main_windows：按目标进程名识别主窗，UIPI 免疫，",
    )
    t = t.replace(
        '比定位器自身的进程名枚举更可靠（实测运行中 MUP 枚举偶发为空导致误回退到自己 Tk 窗）。',
        '比定位器自身的进程名枚举更可靠（实测运行中目标枚举偶发为空导致误回退到自己 Tk 窗）。',
    )

    # 6) preflight 日志去 mup- 前缀 + 文案通用化
    t = t.replace("[mup-preflight]", "[preflight]")
    t = t.replace("[mup-data]", "[data]")
    t = t.replace(
        'GM_EXE or "Meteodyn Universe"',
        'GM_EXE or "目标软件"',
    )
    t = t.replace(
        'log_step(f"[mup-data] 运行后数据差异检测失败（跳过）: {exc}")',
        'log_step(f"[data] 运行后数据差异检测失败（跳过）: {exc}")',
    )
    t = t.replace(
        'run_report["mupDataDiffError"] = str(exc)',
        'run_report["dataDiffError"] = str(exc)',
    )

    write_text(OUT, t)
    print("done:", OUT)


if __name__ == "__main__":
    main()
