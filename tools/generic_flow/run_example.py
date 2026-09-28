# -*- coding: utf-8 -*-
"""通用化副本的最小可运行示例（程序化调用，非 GUI）。

面向已部署内网运行版的目标机：项目根已在 sys.path，依赖齐全。
演示：配置目标软件 -> 运行 generic_automation.run_automation。

更常见的用法是双击 generic_launcher.py 打开极简 GUI 总控台配置并运行。
本文件用于无界面/嵌入调用的场景。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import generic_automation


def main():
    # ===== 1) 配置目标软件（替代原 WT/MUP 写死值）=====
    # exe 路径用于进程名识别与提权检测；class_keywords 是定位/预检的识别关键词。
    # 通常直接用 exe 文件名（去 .exe）即可，如 "notepad" / "targetapp"。
    TARGET_EXE = r"C:\Windows\System32\notepad.exe"   # 示例：记事本，换成你的软件
    base = os.path.basename(TARGET_EXE).lower()
    kw = base[:-4] if base.endswith(".exe") else base

    generic_automation.config_generic_target_app(
        exe=TARGET_EXE,
        title_re=None,            # 留空：纯靠进程名识别主窗
        class_keywords=[kw] if kw else [],
    )

    # ===== 2) 指定流程定义文件（也可设环境变量 WT_FLOW_DEFINITION_FILE）=====
    flow_file = os.path.join(ROOT, "workspace", "flow_definition.json")
    generic_automation.FLOW_DEFINITION_FILE = flow_file

    # ===== 3) 运行 =====
    print("通用化副本导入成功。目标配置 =", generic_automation.get_generic_target_config()
          if hasattr(generic_automation, "get_generic_target_config") else generic_automation.TARGET_CLASS_KEYWORDS)
    print("请确认 flow_definition.json 已针对目标软件采集控件库后调用 run_automation() 。")
    # generic_automation.run_automation(pre_raise=True)


if __name__ == "__main__":
    main()
