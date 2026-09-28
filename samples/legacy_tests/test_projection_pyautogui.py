# encoding: utf-8
"""
完全基于 pyautogui 的投影配置脚本
使用键盘快捷键 + 坐标定位，不依赖控件识别
"""
import os
import time
import pyautogui
import ctypes
import re
from ctypes import wintypes

# === 配置部分 ===
GM_EXE = r"D:\工作\软件\GM22免安装版\GM22免安装版\global_mapper.exe"
PROJECTION_FILE_PATH = r"C:\Users\14830\Desktop\测试\40投影.prj"
MAIN_WINDOW_TITLE_RE = re.compile(r"Global Mapper v22\.1 .*中文注册版")

# 安全设置
pyautogui.PAUSE = 0.5  # 每次操作后暂停 0.5 秒
pyautogui.FAILSAFE = True  # 鼠标移到左上角（0,0）可以紧急停止

# === Windows API 窗口操作 ===
user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_SHOW = 5

def _get_window_text(hwnd):
	length = user32.GetWindowTextLengthW(hwnd)
	if length <= 0:
		return ""
	buffer = ctypes.create_unicode_buffer(length + 1)
	user32.GetWindowTextW(hwnd, buffer, len(buffer))
	return buffer.value

def _get_window_rect(hwnd):
	rect = wintypes.RECT()
	if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
		return None
	return rect

def _find_main_windows():
	windows = []

	@EnumWindowsProc
	def callback(hwnd, _lparam):
		if not user32.IsWindowVisible(hwnd):
			return True
		title = _get_window_text(hwnd)
		if not title or not MAIN_WINDOW_TITLE_RE.search(title):
			return True
		rect = _get_window_rect(hwnd)
		width = (rect.right - rect.left) if rect else 0
		height = (rect.bottom - rect.top) if rect else 0
		windows.append(
			{
				"hwnd": int(hwnd),
				"title": title,
				"minimized": bool(user32.IsIconic(hwnd)),
				"width": width,
				"height": height,
			}
		)
		return True

	user32.EnumWindows(callback, 0)
	return windows

def _choose_best_window(windows):
	if not windows:
		return None

	def sort_key(window):
		title = window["title"]
		return (
			1 if "[64-bit]" in title else 0,
			1 if "[+LIDAR]" in title else 0,
			0 if window["minimized"] else 1,
			window["width"] * window["height"],
		)

	return max(windows, key=sort_key)

def activate_and_maximize_window():
	print("正在查找并激活主窗口...")
	windows = _find_main_windows()
	best = _choose_best_window(windows)
	
	if not best:
		print("  ✗ 未找到 GM 主窗口")
		return False
	
	print(f"  ✓ 找到窗口，句柄: {best['hwnd']}")
	hwnd = best["hwnd"]
	
	if best["minimized"]:
		user32.ShowWindow(hwnd, SW_RESTORE)
		time.sleep(0.5)
	
	user32.ShowWindow(hwnd, SW_MAXIMIZE)
	time.sleep(0.5)
	user32.ShowWindow(hwnd, SW_MAXIMIZE)
	time.sleep(0.5)
	
	user32.SetForegroundWindow(hwnd)
	time.sleep(0.5)
	
	print("  ✓ 窗口已最大化并置顶")
	return True

def test_projection_loading():
	print("=" * 60)
	print("GM 投影文件加载（pyautogui 版）")
	print("=" * 60)
	print()
	
	if not os.path.exists(GM_EXE):
		print(f"错误: GM 软件不存在: {GM_EXE}")
		return
	if not os.path.exists(PROJECTION_FILE_PATH):
		print(f"错误: 投影文件不存在: {PROJECTION_FILE_PATH}")
		return
	
	print(f"GM 软件: {GM_EXE}")
	print(f"投影文件: {PROJECTION_FILE_PATH}")
	print()
	
	print("步骤 1: 启动 Global Mapper")
	os.startfile(GM_EXE)
	print("  ✓ 软件启动命令已发送，等待 10 秒...")
	time.sleep(10)
	
	print("\n步骤 2: 最大化并置顶窗口")
	if not activate_and_maximize_window():
		return
	
	print("\n步骤 3: 使用键盘快捷键打开配置")
	print("  按 Alt+F 打开文件菜单...")
	pyautogui.hotkey("alt", "f")
	time.sleep(0.5)
	
	print("  按 'c' 选择配置...")
	pyautogui.press("c")
	time.sleep(0.5)
	
	print("\n步骤 4: 在配置窗口选择常规")
	print("  按 Tab 切换焦点...")
	pyautogui.press("tab", presses=3)
	time.sleep(0.3)
	
	print("  按 Home 到顶部，再按向下到常规...")
	pyautogui.press("home")
	time.sleep(0.2)
	pyautogui.press("down")
	time.sleep(0.2)
	
	print("  按 Enter 确认...")
	pyautogui.press("enter")
	time.sleep(0.5)
	
	print("\n步骤 5: 再向下到投影")
	pyautogui.press("tab", presses=3)
	time.sleep(0.3)
	pyautogui.press("down")
	time.sleep(0.2)
	pyautogui.press("enter")
	time.sleep(0.5)
	
	print("\n步骤 6: 选择从文件加载")
	print("  按 Tab 切换到从文件加载按钮...")
	pyautogui.press("tab", presses=10)
	time.sleep(0.3)
	
	print("  按 Enter 点击从文件加载...")
	pyautogui.press("enter")
	time.sleep(1)
	
	print("\n步骤 7: 在文件对话框输入路径")
	print("  按 Alt+N 定位到文件名输入框...")
	pyautogui.hotkey("alt", "n")
	time.sleep(0.3)
	
	print(f"  输入投影文件路径: {PROJECTION_FILE_PATH}")
	pyautogui.write(PROJECTION_FILE_PATH)
	time.sleep(0.5)
	
	print("  按 Enter 打开文件...")
	pyautogui.press("enter")
	time.sleep(1)
	
	print("\n步骤 8: 应用和确定")
	print("  按 Alt+A 点击应用...")
	pyautogui.hotkey("alt", "a")
	time.sleep(0.5)
	
	print("  按 Alt+O 点击确定...")
	pyautogui.hotkey("alt", "o")
	time.sleep(0.5)
	
	print("\n" + "=" * 60)
	print("✓✓✓ 投影配置完成！✓✓✓")
	print("=" * 60)

if __name__ == "__main__":
	input("按回车键开始测试（确保没有其他干扰程序）...")
	test_projection_loading()
