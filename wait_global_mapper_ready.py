# encoding: utf-8
import ctypes
import io
import re
import sys
import time
from ctypes import wintypes

# Force UTF-8 logs in PowerShell and Robot captured output.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

MAIN_WINDOW_TITLE_RE = re.compile(r"Global Mapper v22\.1 .*中文注册版")
DEFAULT_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2
READY_CONFIRMATION_COUNT = 3
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002

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


def get_window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def get_window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def is_window_responsive(hwnd, timeout_ms=1000):
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


def find_main_windows():
    windows = []

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_text(hwnd)
        if not title or not MAIN_WINDOW_TITLE_RE.search(title):
            return True
        rect = get_window_rect(hwnd)
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


def choose_best_window(windows):
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


def wait_until_ready(timeout_seconds):
    deadline = time.time() + timeout_seconds
    consecutive_ready = 0
    last_reason = "尚未开始检测"

    while time.time() < deadline:
        windows = find_main_windows()
        best = choose_best_window(windows)
        if best is None:
            consecutive_ready = 0
            last_reason = "未找到匹配标题的 Global Mapper 主窗口"
        else:
            responsive = is_window_responsive(best["hwnd"])
            ready = (
                responsive
                and not best["minimized"]
                and best["width"] > 0
                and best["height"] > 0
            )
            if ready:
                consecutive_ready += 1
                print(
                    f"[gm-ready] ready-check {consecutive_ready}/{READY_CONFIRMATION_COUNT} "
                    f"hwnd={best['hwnd']} title={best['title']}"
                )
                if consecutive_ready >= READY_CONFIRMATION_COUNT:
                    print(
                        f"[gm-ready] ready hwnd={best['hwnd']} title={best['title']} "
                        f"size={best['width']}x{best['height']}"
                    )
                    return
                last_reason = "窗口已响应，继续做稳定性确认"
            else:
                consecutive_ready = 0
                last_reason = (
                    f"窗口存在但未就绪: hwnd={best['hwnd']}, minimized={best['minimized']}, "
                    f"size={best['width']}x{best['height']}, responsive={responsive}"
                )

        titles = [window["title"] for window in windows]
        print(f"[gm-ready] waiting: {last_reason}; candidates={titles}")
        time.sleep(POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"等待 Global Mapper 恢复响应超时: {last_reason}")


def main():
    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    if len(sys.argv) > 1:
        timeout_seconds = int(sys.argv[1])
    print(f"[gm-ready] start timeout={timeout_seconds}s")
    wait_until_ready(timeout_seconds)


if __name__ == "__main__":
    main()
