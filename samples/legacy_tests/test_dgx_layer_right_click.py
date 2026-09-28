# encoding: utf-8
"""
局部测试脚本：
只测试“按属性拆分后，点击展开/折叠图标，再右键 DGX 图层”这一小段。

优先复用现有代码：
1. 展开/折叠图标定位逻辑复用 test_layer_expand_click.py
2. DGX 图层右键逻辑复用 WT_AUT_recorded.py
"""

import argparse
import os
import re
import time

import pyautogui

from GM_AUT_recorded import SOURCE_FILE_PATH, _right_click_tree_item_by_title_re, activate_and_maximize_main_window
from test_layer_expand_click import ACTION_TEMPLATE_MAP, locate_template_center, log, save_debug_screenshot


pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True


def click_layer_tree_icon(action, template_path, timeout_seconds, confidence, offset_x, offset_y, clicks):
    center = locate_template_center(template_path, timeout_seconds=timeout_seconds, confidence=confidence)
    target_x = center.x + offset_x
    target_y = center.y + offset_y
    log(f"{action} 模板命中位置: ({center.x}, {center.y}), 实际点击位置: ({target_x}, {target_y})")
    pyautogui.moveTo(target_x, target_y, duration=0.2)
    pyautogui.click(clicks=clicks, interval=0.15)
    log(f"已执行 {action} 图标点击，clicks={clicks}")
    time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="测试拆分后到右键 DGX 图层的局部流程")
    parser.add_argument(
        "--action",
        choices=["expand", "collapse", "skip"],
        default="expand",
        help="先点击展开、折叠，或直接跳过图标点击",
    )
    parser.add_argument("--template", default="", help="图标模板路径；不填时按 action 自动选择")
    parser.add_argument("--source-name", default=os.path.basename(SOURCE_FILE_PATH), help="源文件 basename，用于拼 DGX 图层名")
    parser.add_argument("--timeout", type=float, default=8.0, help="模板匹配超时秒数")
    parser.add_argument("--confidence", type=float, default=0.8, help="模板匹配置信度")
    parser.add_argument("--wait", type=float, default=3.0, help="激活窗口后等待秒数")
    parser.add_argument("--clicks", type=int, default=1, help="图标点击次数")
    parser.add_argument("--offset-x", type=int, default=0, help="点击时的 x 偏移")
    parser.add_argument("--offset-y", type=int, default=0, help="点击时的 y 偏移")
    args = parser.parse_args()

    if args.action != "skip":
        template_path = args.template or ACTION_TEMPLATE_MAP[args.action]
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"未找到图标模板: {template_path}")
    else:
        template_path = ""

    log("请先手动把 GM 停在“按属性拆分完成、准备展开图层”的界面。")
    log(f"测试动作: {args.action}")
    if template_path:
        log(f"使用模板: {template_path}")
    log(f"目标源文件: {args.source_name}")
    log("正在激活 Global Mapper 主窗口...")
    activate_and_maximize_main_window(timeout_seconds=15)

    if args.wait > 0:
        log(f"等待 {args.wait:.1f} 秒，便于你确认当前界面状态...")
        time.sleep(args.wait)

    save_debug_screenshot("dgx_right_click_before")

    if args.action != "skip":
        click_layer_tree_icon(
            action=args.action,
            template_path=template_path,
            timeout_seconds=args.timeout,
            confidence=args.confidence,
            offset_x=args.offset_x,
            offset_y=args.offset_y,
            clicks=args.clicks,
        )
        save_debug_screenshot(f"dgx_right_click_after_{args.action}")
    else:
        log("已跳过图标点击，直接测试右键 DGX 图层。")

    dgx_layer_re = re.escape(args.source_name) + r" - DGX \[\d+ Features\]"
    log(f"开始右键 DGX 图层，title_re={dgx_layer_re}")
    _right_click_tree_item_by_title_re(dgx_layer_re)
    time.sleep(1)
    save_debug_screenshot("dgx_right_click_after_menu")
    log("DGX 图层右键成功，已弹出上下文菜单。")


if __name__ == "__main__":
    main()
