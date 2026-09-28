# -*- coding: utf-8 -*-
"""生成「去 WT 化」的通用化副本。

仅复制 wt_flow_locator.py / wt_flow_executor.py 到本目录，并把其中写死
WT/MUP(Meteodyn) 的窗口识别逻辑改成「按配置注入目标软件」的形式。

本项目原始文件不做任何修改。运行一次即可生成 generic_flow_locator.py /
generic_flow_executor.py。

用法（在项目根目录执行）：
    python tools/generic_flow/make_generic_copy.py
"""
import io
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_DIR = os.path.join(ROOT, "WT_Automation")
if not os.path.isdir(SRC_DIR):
    # 兼容直接放在项目根下的情况
    SRC_DIR = ROOT
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def read_text(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def make_locator(src):
    out = src

    # 1) 模块级关键词常量 -> 可配置变量（默认空：匹配所有可见顶层窗）
    out = out.replace(
        '_MUP_WINDOW_KEYWORDS = ("mupsmartclient", "smartclient", "meteodyn", "univwrse")',
        '_TARGET_WINDOW_KEYWORDS = ()  # 通用化：目标软件窗口关键词，运行时由 '
        'config_generic_target() 注入；空元组=匹配所有可见顶层窗口',
    )

    # 2) 枚举函数改名 + 关键词引用改名
    out = out.replace(
        "def _enum_visible_mup_win32_windows():",
        "def _enum_target_win32_windows():",
    )
    out = out.replace(
        '    """MUP 关键词过滤的可见顶层窗口（复用 iter_visible_top_level_windows，纯 Win32）。"""',
        '    """目标软件关键词过滤的可见顶层窗口（通用化；关键词为空时返回全部可见顶层窗）。"""',
    )
    out = out.replace(
        "        matched = any(keyword in class_name for keyword in _MUP_WINDOW_KEYWORDS) or any(\n"
        "            keyword in process_name for keyword in _MUP_WINDOW_KEYWORDS\n"
        "        )\n",
        "        if not _TARGET_WINDOW_KEYWORDS:\n"
        "            matched = True\n"
        "        else:\n"
        "            matched = any(keyword in class_name for keyword in _TARGET_WINDOW_KEYWORDS) or any(\n"
        "                keyword in process_name for keyword in _TARGET_WINDOW_KEYWORDS\n"
        "            )\n",
    )

    # 3) 调用点改名
    out = out.replace(
        "result = _wrap_hwnd_candidates(_enum_visible_mup_win32_windows())",
        "result = _wrap_hwnd_candidates(_enum_target_win32_windows())",
    )

    # 4) 无标题 WPF 窗口识别里的 MUPSmartClient 子串匹配 -> 可配置进程关键词
    out = out.replace(
        "            # HwndWrapper[MUPSmartClient.exe;;<GUID>] 的 GUID 随安装/机器变化，\n"
        "            # 只按进程名子串匹配，避免换机后窗口匹配静默失效\n"
        '            or "MUPSmartClient" in actual_class_name\n',
        "            # HwndWrapper[<目标进程>.exe;;<GUID>] 的 GUID 随安装/机器变化，\n"
        "            # 只按进程名子串匹配，避免换机后窗口匹配静默失效（通用化：用 _TARGET_PROCESS_KEYWORDS）\n"
        "            or _TARGET_PROCESS_KEYWORDS\n"
        "            and any(k in actual_class_name for k in _TARGET_PROCESS_KEYWORDS)\n",
    )

    # 5) _activate_process_main_window 默认参数
    out = out.replace(
        'def _activate_process_main_window(process_name="MUPSmartClient"):',
        'def _activate_process_main_window(process_name=None):',
    )
    out = out.replace(
        '        keyword = str(process_name or "MUPSmartClient").lower()',
        '        keyword = str(process_name or "").lower()  # 为空则不按类名过滤（交给调用方先激活顶层窗）',
    )

    # 6) 硬编码调用点
    out = out.replace(
        '            _activate_process_main_window("MUPSmartClient")',
        "            _activate_process_main_window(_TARGET_ACTIVATE_PROCESS_NAME)",
    )

    # 7) 模块注释里的 MUP 主窗说明 -> 通用说明
    out = out.replace(
        "# 目标软件主窗口候选提供者：运行时注入（如 WT_AUT_recorded 用 find_main_windows 按进程名\n"
        "# 找 MUPSmartClient 主窗）。fallback 用它的 hwnd 包装成 UIA wrapper，比枚举解析进程名可靠。",
        "# 目标软件主窗口候选提供者：运行时注入（使用方用 find_main_windows 按进程名找目标软件主窗）。\n"
        "# fallback 用它的 hwnd 包装成 UIA wrapper，比枚举解析进程名可靠。",
    )

    # 8) 新增通用配置 API（插在 configure_flow_locator 之前）
    cfg_api = '''
# ---------------------------------------------------------------------------
# 通用化配置入口（去 WT/MUP 绑定）
# ---------------------------------------------------------------------------
_TARGET_PROCESS_KEYWORDS = ()          # 无标题 WPF 窗口识别用的进程名子串
_TARGET_ACTIVATE_PROCESS_NAME = ""     # _activate_process_main_window 默认类名关键词
_TARGET_MAIN_WINDOW_PROCESS = ""       # 注入用：find_main_windows 按此进程名找主窗


def config_generic_target(window_keywords=(), process_keywords=(),
                          activate_process_name="", main_window_process=""):
    """配置目标软件识别参数（通用化，不绑定 WT/MUP）。

    Args:
        window_keywords: 可见顶层窗口类名/进程名子串元组，如 ("myapp",)。
                         为空则匹配所有可见顶层窗口（最宽松）。
        process_keywords: 无标题 WPF 主窗识别用的进程名子串元组。
        activate_process_name: 双保险窗口激活时按类名查找的关键词；
                               为空则仅依赖锚点控件顶层窗激活。
        main_window_process: 注入运行时主窗口候选时按此进程名查找。
    """
    global _TARGET_WINDOW_KEYWORDS, _TARGET_PROCESS_KEYWORDS
    global _TARGET_ACTIVATE_PROCESS_NAME, _TARGET_MAIN_WINDOW_PROCESS
    _TARGET_WINDOW_KEYWORDS = tuple(window_keywords or ())
    _TARGET_PROCESS_KEYWORDS = tuple(process_keywords or ())
    _TARGET_ACTIVATE_PROCESS_NAME = activate_process_name or ""
    _TARGET_MAIN_WINDOW_PROCESS = main_window_process or ""


def get_generic_target_config():
    return {
        "window_keywords": _TARGET_WINDOW_KEYWORDS,
        "process_keywords": _TARGET_PROCESS_KEYWORDS,
        "activate_process_name": _TARGET_ACTIVATE_PROCESS_NAME,
        "main_window_process": _TARGET_MAIN_WINDOW_PROCESS,
    }

'''
    # 在 configure_flow_locator 定义前插入
    marker = "def configure_flow_locator(get_step_definition=None, log_step=None, get_main_window_candidates=None):"
    out = out.replace(marker, cfg_api + "\n" + marker, 1)

    # 9) 运行时主窗口候选注入：若 _GET_MAIN_WINDOW_CANDIDATES 为空且配置了
    #    main_window_process，则自动按进程名回退（可选，给没注入的人兜底）
    #    在 iter_flow_search_windows 使用 _GET_MAIN_WINDOW_CANDIDATES 处补充兜底
    out = out.replace(
        "            result = _wrap_hwnd_candidates(_GET_MAIN_WINDOW_CANDIDATES())\n",
        "            result = _wrap_hwnd_candidates(_GET_MAIN_WINDOW_CANDIDATES())\n"
        "            if not result and _TARGET_MAIN_WINDOW_PROCESS:\n"
        "                try:\n"
        "                    from .generic_main_window import find_main_windows_by_process\n"
        "                    result = _wrap_hwnd_candidates(\n"
        "                        find_main_windows_by_process(_TARGET_MAIN_WINDOW_PROCESS))\n"
        "                except Exception:\n"
        "                    result = []\n",
    )
    return out


def make_executor(src):
    # executor 里 MUP 相关仅投影历史校验（由调用方注入 mup_user_config 触发），
    # 窗口检测无 WT 硬编码，这里只把注释里的 WT_AUT_recorded 提示保留（仍可用）。
    # 无需文本替换，原样复制即可；仅把文件名含义通过输出文件名体现。
    return src


def main():
    locator_src = os.path.join(SRC_DIR, "wt_flow_locator.py")
    executor_src = os.path.join(SRC_DIR, "wt_flow_executor.py")
    loc_text = read_text(locator_src)
    exe_text = read_text(executor_src)

    loc_out = make_locator(loc_text)
    exe_out = make_executor(exe_text)

    write_text(os.path.join(OUT_DIR, "generic_flow_locator.py"), loc_out)
    write_text(os.path.join(OUT_DIR, "generic_flow_executor.py"), exe_out)

    # 注意：本副本面向「已部署内网运行版」的目标机，其环境内已包含
    # wt_flow_editor_utils / wt_action_schema / 全部第三方依赖，故不在此复制，
    # 也不生成 requirements.txt。副本运行时依赖目标机项目根的 sys.path。

    # 主窗口查找兜底辅助模块
    helper = '''# -*- coding: utf-8 -*-
"""通用化：按进程名查找目标软件主窗口（替代原 WT 的 find_main_windows 写死 MUP）。"""
import os
import psutil

try:
    from uiautomation import WindowControl, Process, TreeWalker
except Exception:  # pragma: no cover
    WindowControl = Process = TreeWalker = None


def _pid_of(process_name):
    pname = process_name.lower()
    pids = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info.get("name") or "").lower() == pname:
                pids.append(p.info.get("pid"))
        except Exception:
            continue
    return pids


def find_main_windows_by_process(process_name):
    """返回目标进程主窗口的 hwnd 字符串列表（供 locator 包装成 UIA wrapper）。"""
    pids = _pid_of(process_name)
    wins = []
    if WindowControl is None:
        return wins
    for pid in pids:
        try:
            pc = Process(pid)
            main = pc.GetMainWindow()
            if main:
                wins.append(str(main.NativeWindowHandle))
        except Exception:
            continue
    return wins
'''
    write_text(os.path.join(OUT_DIR, "generic_main_window.py"), helper)

    # 使用说明
    readme = '''# 通用化自动化副本（去 WT/MUP 绑定）

本目录由 `make_generic_copy.py` 从项目根目录的
`wt_flow_locator.py` / `wt_flow_executor.py` **复制并改造**而来，**不修改原项目文件**。

## 与原版的区别（仅窗口识别层）
- 原版写死 WT/MUP(Meteodyn) 窗口关键词、进程名、主窗口查找；
- 本副本改为由 `config_generic_target(...)` 配置目标软件，未配置时默认匹配所有可见窗口。

## 使用方如何接入（以你的目标软件为例）
```python
import sys
sys.path.insert(0, r"路径/to/generic_flow")

from generic_flow_locator import config_generic_target, configure_flow_locator
from generic_flow_executor import configure_flow_executor

# 1) 声明目标软件识别参数
config_generic_target(
    window_keywords=("myapp",),          # 可见顶层窗类名/进程名子串；空=匹配全部
    process_keywords=("myapp",),         # 无标题 WPF 主窗识别用
    activate_process_name="myapp",       # 双保险激活用的类名关键词
    main_window_process="myapp.exe",     # 按进程名找主窗兜底
)

# 2) 注入步骤定义 / 流程包 / 日志回调（同原 configure_flow_locator / configure_flow_executor）
configure_flow_locator(get_step_definition=..., log_step=...)
configure_flow_executor(get_step_definition=..., get_flow_package=..., log_step=...)

# 3) 重新用 build_control_map_library.py 针对目标软件采集控件库
#    （控件定义的 frameworkId / automationId 是 WT 的，换软件必须重采）
```

## 重要提醒
- 控件库（control_maps/）是 WT 专属的，换软件必须用
  `build_control_map_library.py` 重新采集目标软件，否则控件定位必然失败。
- frameworkId 过滤：若目标软件框架与 WT 不同（如 Win32/WinForms/Qt/网页），
  采集时记录的就是新框架，定位按新框架扫描，无需额外改代码。
- executor 的「投影历史校验」等业务功能依赖可选注入的 `mup_user_config`，
  通用化场景下不注入即可，不影响定位与执行。

重新生成副本：在项目根目录执行
    python tools/generic_flow/make_generic_copy.py
'''
    write_text(os.path.join(OUT_DIR, "README.md"), readme)

    # 示例启动脚本
    example = '''# -*- coding: utf-8 -*-
"""通用化副本的最小可运行示例（解压后改一改即可用）。

面向已部署内网运行版的目标机：项目根已在 sys.path，依赖齐全。
演示：配置目标软件 -> 注入步骤定义/流程包 -> 执行单步。
控件库(control_maps/)需先用 build_control_map_library.py 针对目标软件重新采集。
"""
import os
import sys

# 让本目录的副本模块可被 import（项目根依赖由目标机环境提供）
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from generic_flow_locator import config_generic_target, configure_flow_locator
from generic_flow_executor import configure_flow_executor, execute_flow_step

# ===== 1) 改成你的目标软件 =====
TARGET_APP_EXE = "notepad.exe"      # 仅作示例；实际换成你的软件进程名
config_generic_target(
    window_keywords=("notepad",),   # 可见顶层窗类名/进程名子串；留空 () 匹配全部
    process_keywords=("notepad",),
    activate_process_name="notepad",
    main_window_process=TARGET_APP_EXE,
)

# ===== 2) 注入步骤定义 / 流程包 / 日志（这里用占位的示意实现）=====
MY_STEPS = {
    # "step_1": {"action": "click", "controlId": "btn_ok", ...}
}
MY_FLOW = {"steps": []}


def get_step_definition(step_id):
    return MY_STEPS.get(step_id, {})


def log_step(message):
    print("[step]", message)


configure_flow_locator(get_step_definition=get_step_definition, log_step=log_step)
configure_flow_executor(
    get_step_definition=get_step_definition,
    get_flow_package=lambda: MY_FLOW,
    log_step=log_step,
)

# ===== 3) 执行（示例：什么也不做，仅验证配置与导入链路通顺）=====
if __name__ == "__main__":
    print("通用化副本导入成功。配置 =", __import__("generic_flow_locator").get_generic_target_config())
    print("请按 README 接入真实步骤定义与控件库后调用 execute_flow_step(...) 。")
'''
    write_text(os.path.join(OUT_DIR, "run_example.py"), example)

    print("done: generic_flow_locator.py / generic_flow_executor.py / generic_main_window.py "
          "/ run_example.py / README.md / make_generic_copy.py")



if __name__ == "__main__":
    main()
