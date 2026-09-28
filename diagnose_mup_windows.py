# encoding: utf-8
"""诊断：枚举本机可见的 Meteodyn/MUP 相关窗口（标题/类名/尺寸）+ 相关路径与会话信息。

用法：python diagnose_mup_windows.py
输出关键信息，用于定位"健康检查只找到 160x28 小窗口"的问题。
"""
import ctypes
import ctypes.wintypes
import os
import sys


def main():
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    print("== 当前进程 ==")
    print("pid={} session={}".format(os.getpid(), _session_id()))
    print()

    print("== 可见的 Meteodyn/MUP 窗口 ==")
    found = []

    @WNDENUMPROC
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        title = buf.value or ""
        class_name = cls.value or ""
        if "meteodyn" in title.lower() or "mup" in class_name.lower():
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            found.append((hwnd, title, class_name, width, height))
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        print("(未找到任何标题/类名含 Meteodyn/MUP 的可见窗口)")
    for hwnd, title, class_name, width, height in found:
        print("hwnd={} title={!r} class={!r} size={}x{}".format(
            hwnd, title[:60], class_name, width, height
        ))
    print()

    print("== Meteodyn 安装探测 ==")
    candidates = [
        r"C:\Program Files\Meteodyn\MeteodynUniverse\MUPSmartClient.exe",
        r"C:\Users\14830\Desktop\Meteodyn Universe.lnk",
    ]
    for path in candidates:
        print("{} -> {}".format(path, "存在" if os.path.exists(path) else "不存在"))
    print()

    print("== 提示 ==")
    print("若有 >=400x300 的大窗口且标题含 Meteodyn Universe -> worker 应能匹配到，"
          "问题在标题/会话；若只有小窗口 -> 会话隔离或窗口未就绪。")


def _session_id():
    try:
        process = ctypes.windll.kernel32.GetCurrentProcessId()
        return ctypes.windll.kernel32.ProcessIdToSessionId(process)
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
