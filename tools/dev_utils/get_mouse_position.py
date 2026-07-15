# encoding: utf-8
import pyautogui
import time

print("=== 鼠标坐标获取工具 ===")
print("将鼠标移动到要点击的位置，等待 3 秒")
print("按 Ctrl+C 停止\n")

try:
    while True:
        x, y = pyautogui.position()
        pos_str = f"X: {x}, Y: {y}"
        print(f"\r当前位置: {pos_str}", end="", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\n\n退出工具")
