# encoding: utf-8
"""
测试投影文件加载的独立脚本
兜底顺序：
1. 原录制流程
2. 图片识别
3. AI 辅助
"""
import ctypes
import os
import re
import subprocess
import time
from ctypes import wintypes

import pyautogui
from PIL import Image
from pywinauto_recorder.player import *

# 配置
GM_EXE = r"D:\工作\软件\GM22免安装版\GM22免安装版\global_mapper.exe"
PROJECTION_FILE_PATH = r"C:\Users\14830\Desktop\测试\40投影.prj"
MAIN_WINDOW_TITLE_RE = re.compile(r"Global Mapper v22\.1 .*中文注册版")
MAIN_WINDOW_UIPATH = u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"
UI_TARS_RUNNER = os.path.join(os.path.dirname(__file__), "ui_tars_runner.js")
IMAGE_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "image_templates", "projection")

pyautogui.PAUSE = 0.4
pyautogui.FAILSAFE = True

# Windows API 用于窗口操作（和主脚本完全一致）
user32 = ctypes.windll.user32
ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)
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
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ULONG_PTR),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
SW_RESTORE = 9
SW_MAXIMIZE = 3

IMAGE_TEMPLATES = {
    "config_button": "配置_按钮.png",
    "general_tree_item": "常规_按钮.png",
    "projection_tree_item": "投影_按钮.png",
    "load_from_file_button": "从文件加载_按钮.png",
    "file_name_input": "文件名(N)_按钮.png",
    "apply_button": "应用_按钮.png",
    "ok_button": "确定_按钮.png",
}


def _log(message):
    print(message, flush=True)


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


def _is_window_responsive(hwnd, timeout_ms=1000):
    result = ULONG_PTR()
    response = user32.SendMessageTimeoutW(
        hwnd,
        WM_NULL,
        0,
        0,
        SMTO_ABORTIFHUNG,
        timeout_ms,
        ctypes.byref(result),
    )
    return bool(response)


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


def _wait_until_main_window_ready(timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        windows = _find_main_windows()
        best = _choose_best_window(windows)
        if best and _is_window_responsive(best["hwnd"]):
            return best
        time.sleep(0.5)
    return None


def activate_and_maximize_window():
    _log("正在查找并激活主窗口...")
    best = _wait_until_main_window_ready(timeout_seconds=30)
    if not best:
        _log("  ✗ 未找到 GM 主窗口")
        return False

    _log(f"  ✓ 找到窗口，句柄: {best['hwnd']}")
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

    _log("  ✓ 窗口已最大化并置顶")
    return True


def _run_ui_tars(prompt, step_name="AI配置投影"):
    api_key = os.environ.get("VOLC_API_KEY") or os.environ.get("UI_TARS_API_KEY")
    if not api_key:
        raise RuntimeError("未获取到 VOLC_API_KEY 或 UI_TARS_API_KEY，无法执行 AI 辅助。")
    if not os.path.exists(UI_TARS_RUNNER):
        raise FileNotFoundError(f"未找到 UI-TARS Runner: {UI_TARS_RUNNER}")

    stdout_log = os.path.join(os.path.dirname(__file__), f"ui_tars_{step_name}_stdout.log")
    stderr_log = os.path.join(os.path.dirname(__file__), f"ui_tars_{step_name}_stderr.log")
    _log(f"  -> 开始执行 {step_name}")

    result = subprocess.run(
        ["node", UI_TARS_RUNNER, prompt],
        cwd=os.path.dirname(__file__),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="ignore",
        text=True,
        env={
            **os.environ,
            "VOLC_API_KEY": api_key,
            "UI_TARS_VLM_BASE_URL": os.environ.get("UI_TARS_VLM_BASE_URL", ""),
            "MODEL_NAME": os.environ.get("MODEL_NAME", ""),
            "UI_TARS_REPO_ROOT": os.environ.get("UI_TARS_REPO_ROOT", ""),
            "UI_TARS_CLI_CONFIG": os.environ.get("UI_TARS_CLI_CONFIG", ""),
        },
    )

    with open(stdout_log, "w", encoding="utf-8", errors="ignore") as file_obj:
        if result.stdout:
            file_obj.write(result.stdout)
    with open(stderr_log, "w", encoding="utf-8", errors="ignore") as file_obj:
        if result.stderr:
            file_obj.write(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"{step_name} 执行失败 rc={result.returncode}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )

    _log(f"  ✓ {step_name} 完成，日志已保存")


def _get_template_path(template_key):
    template_name = IMAGE_TEMPLATES[template_key]
    return os.path.join(IMAGE_TEMPLATE_DIR, template_name)


def _locate_template_center(template_key, timeout_seconds=8, confidence=0.8):
    template_path = _get_template_path(template_key)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"缺少图片模板: {template_path}")

    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            # OpenCV 在 Windows 下直接读取中文路径模板时可能失败，这里先用 PIL 打开。
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
        time.sleep(0.5)

    raise RuntimeError(f"未找到图片模板: {template_path}; last_error={last_error}")


def _click_template(template_key, timeout_seconds=8, confidence=0.8, double_click=False):
    center = _locate_template_center(template_key, timeout_seconds=timeout_seconds, confidence=confidence)
    pyautogui.moveTo(center.x, center.y, duration=0.2)
    if double_click:
        pyautogui.doubleClick()
    else:
        pyautogui.click()
    time.sleep(0.5)


def _open_config_with_image():
    _click_template("config_button", timeout_seconds=5)
    _log("  ✓ 图片识别点击配置成功")
    time.sleep(1)


def _run_recorded_projection_flow():
    _log("\n步骤 3/6: 先执行原录制路径")
    with UIPath(MAIN_WINDOW_UIPATH):
        click(u"||Pane->文件||Pane->配置||Button")
        _log("  ✓ 点击配置成功")
        time.sleep(0.8)

        with UIPath(u"配置 - 常规||Window->||Tree"):
            click(u"常规||TreeItem")
            _log("  ✓ 点击常规成功")
            time.sleep(0.5)

        click(u"配置 - 常规||Window->||Tree->投影||TreeItem")
        _log("  ✓ 点击投影成功")
        time.sleep(0.8)

        with UIPath(u"配置 - 投影||Window"):
            click(u"从文件加载...||Button")
            _log("  ✓ 点击从文件加载成功")
            time.sleep(1)

            with UIPath(u"配置 - 投影||Window->打开||Window->文件名(N):||ComboBox"):
                click(u"文件名(N):||Edit")
                time.sleep(0.3)
                send_keys("^a")
                time.sleep(0.2)
                send_keys(PROJECTION_FILE_PATH)
                _log("  ✓ 输入投影路径成功")
                time.sleep(0.3)
                send_keys("{ENTER}")
                _log("  ✓ 确认打开成功")
                time.sleep(1)

            with UIPath(u"配置 - 投影||Window"):
                click(u"应用||Button")
                _log("  ✓ 点击应用成功")
                time.sleep(0.5)
                click(u"确定||Button")
                _log("  ✓ 点击确定成功")
                time.sleep(0.5)


def _run_image_projection_flow():
    _log("\n步骤 4/6: 录制路径失败，切换图片识别")
    _log(f"  模板目录: {IMAGE_TEMPLATE_DIR}")

    _open_config_with_image()

    try:
        _click_template("general_tree_item", timeout_seconds=5)
        _log("  ✓ 图片识别点击常规成功")
    except Exception as exc:
        _log(f"  ! 常规项未匹配，继续尝试投影项: {exc}")

    _click_template("projection_tree_item", timeout_seconds=8)
    _log("  ✓ 图片识别点击投影成功")
    time.sleep(0.8)

    _click_template("load_from_file_button", timeout_seconds=8)
    _log("  ✓ 图片识别点击从文件加载成功")
    time.sleep(1)

    try:
        _click_template("file_name_input", timeout_seconds=5)
        _log("  ✓ 图片识别点击文件名输入框成功")
    except Exception as exc:
        _log(f"  ! 文件名输入框图片未匹配，改用 Alt+N: {exc}")
        pyautogui.hotkey("alt", "n")
        time.sleep(0.5)

    send_keys("^a")
    time.sleep(0.2)
    send_keys(PROJECTION_FILE_PATH)
    _log("  ✓ 已输入投影文件路径")
    time.sleep(0.3)
    send_keys("{ENTER}")
    _log("  ✓ 已确认打开文件")
    time.sleep(1)

    _click_template("apply_button", timeout_seconds=8)
    _log("  ✓ 图片识别点击应用成功")
    time.sleep(0.5)

    _click_template("ok_button", timeout_seconds=8)
    _log("  ✓ 图片识别点击确定成功")
    time.sleep(0.5)


def _run_ai_projection_flow():
    _log("\n步骤 5/6: 图片识别也失败，切换 AI 辅助")
    _run_ui_tars(
        "你现在只负责在 Global Mapper 中加载投影配置文件，不要做其他额外操作。"
        "请严格按以下顺序执行：\n"
        "1. 如果“配置 - 投影”窗口还没打开，先点击主界面的“配置”按钮，再在左侧树里点击“常规”，然后点击“投影”。\n"
        "2. 在“配置 - 投影”窗口中，点击“从文件加载...”按钮。\n"
        "3. 在打开文件对话框中，点击“文件名(N)”输入框，输入完整路径："
        "C:\\Users\\14830\\Desktop\\测试\\40投影.prj，然后按回车。\n"
        "4. 回到“配置 - 投影”窗口后，点击“应用”按钮，再点击“确定”按钮。\n"
        "5. 如果某一步按钮无响应，可以重新识别界面后继续，但不要偏离以上目标。",
        step_name="AI配置投影",
    )


def test_projection_loading():
    print("=" * 60)
    print("GM 投影文件加载完整测试流程")
    print("兜底顺序：原录制 -> 图片识别 -> AI辅助")
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

    print("步骤 1/6: 启动 Global Mapper")
    os.startfile(GM_EXE)
    print("  ✓ 软件启动命令已发送，等待 10 秒...")
    time.sleep(10)

    print("\n步骤 2/6: 最大化并置顶窗口")
    if not activate_and_maximize_window():
        return

    recorded_error = None
    image_error = None

    try:
        _run_recorded_projection_flow()
        strategy = "原录制流程"
    except Exception as exc:
        recorded_error = exc
        _log(f"  ✗ 原录制流程失败: {exc}")
        time.sleep(1)
        activate_and_maximize_window()

        try:
            _run_image_projection_flow()
            strategy = "图片识别"
        except Exception as image_exc:
            image_error = image_exc
            _log(f"  ✗ 图片识别失败: {image_exc}")
            time.sleep(1)
            activate_and_maximize_window()
            _run_ai_projection_flow()
            strategy = "AI辅助"

    print("\n步骤 6/6: 输出结果")
    if recorded_error:
        print(f"  - 原录制流程失败原因: {recorded_error}")
    if image_error:
        print(f"  - 图片识别失败原因: {image_error}")

    print("\n" + "=" * 60)
    print(f"✓✓✓ 投影文件加载测试成功，最终执行方式：{strategy} ✓✓✓")
    print("=" * 60)


if __name__ == "__main__":
    input("按回车键开始完整测试流程...")
    test_projection_loading()
