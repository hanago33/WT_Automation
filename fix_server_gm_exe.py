# encoding: utf-8
"""服务器端一键修复 GM_EXE 配置：.lnk 快捷方式 -> 直接可执行 .exe。

背景：resources/project_config.resource 里 ${GM_EXE} 若指向桌面 .lnk，
Worker 启动 MUP 时用 .lnk 存在可靠性与 UIPI 差异问题；统一改为
C:\\Program Files\\Meteodyn\\MeteodynUniverse\\MUPSmartClient.exe。

用法：python fix_server_gm_exe.py [--target 目标exe路径]
退出码：0=已修复/无需修复，1=修复失败/目标不存在。
"""
import argparse
import os
import re
import sys

RESOURCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "resources", "project_config.resource")
DEFAULT_GM_EXE = r"C:\Program Files\Meteodyn\MeteodynUniverse\MUPSmartClient.exe"


def fix_gm_exe(resource_path=None, target=None, log=print):
    """将 resource 中 ${GM_EXE} 替换为目标 .exe。返回 (changed: bool, message: str)。"""
    resource_path = resource_path or RESOURCE_PATH
    target = target or DEFAULT_GM_EXE
    if not os.path.isfile(resource_path):
        return False, "未找到配置文件：{}".format(resource_path)
    if not os.path.isfile(target):
        return False, "目标不存在：{}（请先确认 MUP 安装路径）".format(target)
    with open(resource_path, "r", encoding="utf-8") as f:
        content = f.read()
    # resource 文件为 RobotFramework 格式，反斜杠需双写
    escaped_target = target.replace("\\", "\\\\")
    pattern = re.compile(r"(\$\{GM_EXE\}\s+).*", re.MULTILINE)
    new_content = pattern.sub(lambda m: m.group(1) + escaped_target, content)
    if new_content == content:
        return False, "[OK] GM_EXE 已是 {}（无需修改）".format(target)
    with open(resource_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True, "[OK] GM_EXE 已修复为 {}".format(target)


def main():
    parser = argparse.ArgumentParser(description="修复 GM_EXE 配置为直接可执行 .exe")
    parser.add_argument("--target", default=DEFAULT_GM_EXE,
                        help="目标 exe 路径（默认官方安装路径）")
    args = parser.parse_args()
    changed, message = fix_gm_exe(target=args.target)
    print(message)
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main())
