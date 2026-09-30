# encoding: utf-8
"""服务器端一键校验/修复 WT 程序路径配置：.lnk 快捷方式 -> 直接可执行 .exe。

背景：resources/project_config.resource 里的 ${GM_EXE} 是 Global Mapper 时代的
遗留变量名，如今承载的是当前目标程序（Meteodyn MUP SmartClient，即「WT 程序」，
对应流程运行配置里的 gmExe 键）。Worker 启动 MUP 时若该值指向桌面 .lnk 快捷方式，
存在可靠性与 UIPI 权限差异问题。本脚本先校验现值：已是直接 .exe 则不做任何修改；
仅当指向 .lnk、值为空或文件缺失时，才改写为标准安装路径。

用法：python fix_server_gm_exe.py [--target 目标exe路径]
退出码：0=已修复或校验通过（无需修复），1=修复失败/目标不存在/配置行缺失。
"""
import argparse
import os
import re
import sys

RESOURCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "resources", "project_config.resource")
DEFAULT_GM_EXE = r"C:\Program Files\Meteodyn\MeteodynUniverse\MUPSmartClient.exe"

# [ \t]+ 而非 \s+：防止值缺失时 \s 吞掉换行、把下一行内容误当路径。
GM_EXE_PATTERN = re.compile(r"(\$\{GM_EXE\}[ \t]+)(.*)", re.MULTILINE)


def read_gm_exe_value(content):
    """从 resource 文本中读取 ${GM_EXE} 的现值（反转义双写反斜杠）。

    返回 None 表示配置行不存在；空字符串表示配置行存在但未设值。
    """
    match = GM_EXE_PATTERN.search(content)
    if not match:
        return None
    return match.group(2).strip().replace("\\\\", "\\")


def fix_gm_exe(resource_path=None, target=None):
    """校验并按需修复 resource 中 ${GM_EXE}。返回 (changed: bool, ok: bool, message: str)。

    - 校验通过（现值是已存在的直接 .exe）：changed=False, ok=True，不写文件；
    - 现值指向 .lnk / 为空 / 文件缺失：改写为 target，changed=True, ok=True；
    - target 不存在或配置行缺失：ok=False。
    """
    resource_path = resource_path or RESOURCE_PATH
    target = target or DEFAULT_GM_EXE
    if not os.path.isfile(resource_path):
        return False, False, "未找到配置文件：{}".format(resource_path)
    if not os.path.isfile(target):
        return False, False, "目标不存在：{}（请先确认 MUP 安装路径）".format(target)
    with open(resource_path, "r", encoding="utf-8") as f:
        content = f.read()
    current = read_gm_exe_value(content)
    if current is not None and current \
            and not current.lower().endswith(".lnk") and os.path.isfile(current):
        return False, True, "[OK] WT 程序路径已是直接可执行 .exe：{}（无需修复）".format(current)
    if current is None:
        reason = "资源中未找到 ${GM_EXE} 配置行"
    elif (current or "").lower().endswith(".lnk"):
        reason = "当前指向 .lnk 快捷方式：{}".format(current)
    else:
        reason = "当前值无效或文件缺失：{}".format(current or "(空)")
    escaped_target = target.replace("\\", "\\\\")
    new_content, count = GM_EXE_PATTERN.subn(
        lambda m: m.group(1) + escaped_target, content
    )
    if count == 0:
        return False, False, "[WARN] {}，无法自动修复：{}".format(reason, resource_path)
    with open(resource_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True, True, "[OK] WT 程序路径已修复为 {}（原因：{}）".format(target, reason)


def main():
    parser = argparse.ArgumentParser(description="校验/修复 WT 程序(MUP)路径配置为直接可执行 .exe")
    parser.add_argument("--target", default=DEFAULT_GM_EXE,
                        help="目标 exe 路径（默认官方安装路径）")
    args = parser.parse_args()
    changed, ok, message = fix_gm_exe(target=args.target)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
