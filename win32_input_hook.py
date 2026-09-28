# encoding: utf-8
"""Windows 低级别鼠标 / 键盘钩子（基于 ctypes，零第三方依赖）。

作为 ``pynput`` 不可用时的降级方案，供 ``WT_Flow_Editor`` 的“半自动采集”
交互点击采集使用：监听全局左键按下与 F8 热键，回调中给出屏幕坐标。

- 仅依赖标准库 ``ctypes``，仅 Windows 平台有效。
- 在独立守护线程中安装钩子并运行消息泵（低级别钩子的硬性要求）。
- 关闭时卸载钩子并向线程投递 WM_QUIT，确保线程干净退出。
"""
import ctypes
import threading
from ctypes import wintypes

# 钩子类型
WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13

# 窗口消息
WM_LBUTTONDOWN = 0x0201
WM_KEYDOWN = 0x0100
WM_QUIT = 0x0012

# 虚拟键码
VK_F8 = 0x77


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


# 钩子回调原型（与 SetWindowsHookEx 要求一致）
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HINSTANCE, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class Win32InputHook:
    """全局鼠标 / 键盘低级别钩子。

    :param on_click: 左键按下回调 ``callable(x, y)``
    :param on_hotkey: 热键（默认 F8）回调 ``callable(x, y)``，坐标为当前光标位置
    :param hotkey_vk: 热键虚拟键码，默认 ``VK_F8``
    """

    def __init__(self, on_click=None, on_hotkey=None, hotkey_vk=VK_F8):
        self.on_click = on_click
        self.on_hotkey = on_hotkey
        self._hotkey_vk = hotkey_vk

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        self._mouse_hook = None
        self._kb_hook = None
        self._mouse_proc = None
        self._kb_proc = None
        self._thread = None
        self._thread_id = None
        self._running = False

    # ── 公共接口 ─────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        # 卸载钩子（即便线程尚未处理消息也能立即失效）
        if self._mouse_hook:
            self._user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
        if self._kb_hook:
            self._user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        # 向钩子线程投递 WM_QUIT，唤醒 GetMessage 消息泵并退出
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    @property
    def running(self):
        return self._running

    # ── 内部实现 ─────────────────────────────────────────────
    def _run(self):
        self._thread_id = self._kernel32.GetCurrentThreadId()

        @HOOKPROC
        def mouse_proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param == WM_LBUTTONDOWN and self.on_click:
                try:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self.on_click(info.pt.x, info.pt.y)
                except Exception:
                    pass
            return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

        @HOOKPROC
        def kb_proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param == WM_KEYDOWN and self.on_hotkey:
                try:
                    vk = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                    if vk == self._hotkey_vk:
                        x, y = self._cursor_pos()
                        self.on_hotkey(x, y)
                except Exception:
                    pass
            return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._mouse_proc = mouse_proc
        self._kb_proc = kb_proc

        h_mod = self._kernel32.GetModuleHandleW(None)
        self._mouse_hook = self._user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, h_mod, 0)
        self._kb_hook = self._user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_proc, h_mod, 0)

        msg = wintypes.MSG()
        while self._running:
            ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:  # WM_QUIT 或错误 -> 退出
                break

        # 兜底清理（stop() 可能已在别处调用过）
        if self._mouse_hook:
            self._user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
        if self._kb_hook:
            self._user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None

    @staticmethod
    def _cursor_pos():
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y


if __name__ == "__main__":
    import time

    print("Win32 输入钩子测试：点击鼠标或按 F8 查看坐标，Ctrl+C 退出。")
    hook = Win32InputHook(
        on_click=lambda x, y: print(f"[click]  ({x}, {y})"),
        on_hotkey=lambda x, y: print(f"[F8]     ({x}, {y})"),
    )
    hook.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hook.stop()
        print("已停止。")
