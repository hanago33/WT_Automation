# encoding: utf-8
"""
局部测试脚本：
只测试“按属性拆分图层后，点击图层展开/折叠图标”这一小段。

使用方式：
1. 先手动把 Global Mapper 跑到“按属性拆分完成、准备展开图层”的界面。
2. 准备一个展开图标或折叠图标的小模板截图。
3. 运行本脚本，仅做模板匹配和点击测试。
"""

import argparse
import os
import time
from datetime import datetime

import pyautogui
from PIL import Image

from GM_AUT_recorded import activate_and_maximize_main_window


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAYER_TREE_TEMPLATE_DIR = os.path.join(BASE_DIR, "image_templates", "layer_tree")
ACTION_TEMPLATE_MAP = {
    "expand": os.path.join(LAYER_TREE_TEMPLATE_DIR, "展开图标.png"),
    "collapse": os.path.join(LAYER_TREE_TEMPLATE_DIR, "折叠图标.png"),
}
DEBUG_DIR = os.path.join(BASE_DIR, "debug_screenshots")

pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True


def log(message):
    print(message, flush=True)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def save_debug_screenshot(tag):
    ensure_dir(DEBUG_DIR)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(DEBUG_DIR, f"{timestamp}_{tag}.png")
    pyautogui.screenshot(file_path)
    log(f"[debug] 已保存截图: {file_path}")
    return file_path


def locate_template_center(template_path, timeout_seconds=8, confidence=0.8):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            with Image.open(template_path) as template_image:
                try:
                    center = pyautogui.locateCenterOnScreen(template_image, confidence=confidence)
                except Exception as confidence_error:
                    last_error = confidence_error
                    center = pyautogui.locateCenterOnScreen(template_image)
            if center:
                return center
        except Exception as exc:
            last_error = exc
        time.sleep(0.4)

    raise RuntimeError(f"模板未匹配到: {template_path}; last_error={last_error}")


def main():
    parser = argparse.ArgumentParser(description="测试图层展开/折叠图标的模板匹配与点击")
    parser.add_argument(
        "--action",
        choices=sorted(ACTION_TEMPLATE_MAP.keys()),
        default="expand",
        help="测试展开图标还是折叠图标",
    )
    parser.add_argument("--template", default="", help="模板路径；不填时按 action 自动选择")
    parser.add_argument("--timeout", type=float, default=8.0, help="模板匹配超时秒数")
    parser.add_argument("--confidence", type=float, default=0.8, help="模板匹配置信度")
    parser.add_argument("--wait", type=float, default=3.0, help="激活窗口后等待秒数")
    parser.add_argument("--clicks", type=int, default=1, help="点击次数")
    parser.add_argument("--offset-x", type=int, default=0, help="点击时的 x 偏移")
    parser.add_argument("--offset-y", type=int, default=0, help="点击时的 y 偏移")
    parser.add_argument("--dry-run", action="store_true", help="仅定位，不执行点击")
    args = parser.parse_args()

    template_path = args.template or ACTION_TEMPLATE_MAP[args.action]
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"未找到图标模板: {template_path}")

    log("请先手动把 GM 停在“按属性拆分完成、准备展开图层”的界面。")
    log(f"测试动作: {args.action}")
    log(f"使用模板: {template_path}")
    log("正在激活 Global Mapper 主窗口...")
    activate_and_maximize_main_window(timeout_seconds=15)

    if args.wait > 0:
        log(f"等待 {args.wait:.1f} 秒，便于你确认当前界面状态...")
        time.sleep(args.wait)

    before_tag = f"layer_{args.action}_before"
    after_tag = f"layer_{args.action}_after"
    dry_run_tag = f"layer_{args.action}_dry_run"

    save_debug_screenshot(before_tag)
    center = locate_template_center(template_path, timeout_seconds=args.timeout, confidence=args.confidence)
    target_x = center.x + args.offset_x
    target_y = center.y + args.offset_y
    log(f"模板命中位置: ({center.x}, {center.y}), 实际点击位置: ({target_x}, {target_y})")

    pyautogui.moveTo(target_x, target_y, duration=0.2)
    if args.dry_run:
        log("dry-run 模式，不执行点击。")
        save_debug_screenshot(dry_run_tag)
        return

    pyautogui.click(clicks=args.clicks, interval=0.15)
    log(f"已执行点击，clicks={args.clicks}")
    time.sleep(1)
    save_debug_screenshot(after_tag)


if __name__ == "__main__":
    main()
