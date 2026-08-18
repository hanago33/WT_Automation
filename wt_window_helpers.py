# encoding: utf-8

import ctypes
import re
import time

from pywinauto import Desktop
from pywinauto_recorder.player import send_keys


_USER32 = None
_ENUM_WINDOWS_PROC = None
_WM_NULL = 0
_SMTO_ABORTIFHUNG = 0
_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_GET_WINDOW_TEXT = lambda hwnd: ""
_GET_WINDOW_RECT = lambda hwnd: None
_LOG_STEP = lambda message: None
_CLICK_FLOW_CONTROL = lambda *args, **kwargs: False
_FOCUS_FLOW_CONTROL = lambda *args, **kwargs: False


def configure_wt_window_helpers(
    user32=None,
    enum_windows_proc=None,
    wm_null=0,
    smto_abort_if_hung=0,
    sw_restore=9,
    sw_maximize=3,
    get_window_text=None,
    get_window_rect=None,
    log_step=None,
    click_flow_control=None,
    focus_flow_control=None,
):
    global _USER32, _ENUM_WINDOWS_PROC, _WM_NULL, _SMTO_ABORTIFHUNG, _SW_RESTORE, _SW_MAXIMIZE
    global _GET_WINDOW_TEXT, _GET_WINDOW_RECT, _LOG_STEP, _CLICK_FLOW_CONTROL, _FOCUS_FLOW_CONTROL
    _USER32 = user32
    _ENUM_WINDOWS_PROC = enum_windows_proc
    _WM_NULL = wm_null
    _SMTO_ABORTIFHUNG = smto_abort_if_hung
    _SW_RESTORE = sw_restore
    _SW_MAXIMIZE = sw_maximize
    if callable(get_window_text):
        _GET_WINDOW_TEXT = get_window_text
    if callable(get_window_rect):
        _GET_WINDOW_RECT = get_window_rect
    if callable(log_step):
        _LOG_STEP = log_step
    if callable(click_flow_control):
        _CLICK_FLOW_CONTROL = click_flow_control
    if callable(focus_flow_control):
        _FOCUS_FLOW_CONTROL = focus_flow_control


def is_window_responsive(hwnd, timeout_ms=1000):
    result = ctypes.c_void_p()
    response = _USER32.SendMessageTimeoutW(
        hwnd,
        _WM_NULL,
        0,
        0,
        _SMTO_ABORTIFHUNG,
        timeout_ms,
        ctypes.byref(result),
    )
    return bool(response)


def find_main_windows(main_window_title_re, class_name_keywords=()):
    """枚举目标软件主窗口候选。

    - 默认按标题正则匹配（`main_window_title_re`），保持既有行为。
    - 传入 `class_name_keywords`（如 ("MUPSmartClient",)）时，标题为空或
      不匹配正则的可见窗口，若其窗口类名包含任一关键词（不区分大小写）
      也纳入候选——用于 MUP 主窗口标题为空、仅靠类名可识别的场景。
    """
    windows = []
    keywords = tuple(str(k) for k in (class_name_keywords or ()) if str(k).strip())

    @_ENUM_WINDOWS_PROC
    def callback(hwnd, _lparam):
        if not _USER32.IsWindowVisible(hwnd):
            return True
        title = _GET_WINDOW_TEXT(hwnd)
        title_matched = bool(title) and main_window_title_re.search(title)
        class_matched = False
        if not title_matched and keywords:
            class_buf = ctypes.create_unicode_buffer(256)
            if _USER32.GetClassNameW(hwnd, class_buf, 256):
                class_name = class_buf.value or ""
                class_matched = any(k.lower() in class_name.lower() for k in keywords)
        if not title_matched and not class_matched:
            return True
        rect = _GET_WINDOW_RECT(hwnd)
        width = (rect.right - rect.left) if rect else 0
        height = (rect.bottom - rect.top) if rect else 0
        windows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "minimized": bool(_USER32.IsIconic(hwnd)),
                "width": width,
                "height": height,
                "classMatch": class_matched and not title_matched,
            }
        )
        return True

    _USER32.EnumWindows(callback, 0)
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


def _restore_if_minimized(hwnd):
    """若窗口处于最小化状态则恢复（SW_RESTORE），返回是否执行了恢复。

    就绪判定要求"未最小化"，若恢复动作放在就绪判定之后，最小化窗口永远等不到就绪
    （恢复分支被门槛挡住）。必须在就绪判定前完成恢复。
    """
    try:
        if _USER32.IsIconic(hwnd):
            _USER32.ShowWindow(hwnd, _SW_RESTORE)
            time.sleep(0.4)
            return True
    except Exception:
        pass
    return False


def wait_until_main_window_ready(main_window_title_re, timeout_seconds=30, class_name_keywords=()):
    deadline = time.time() + timeout_seconds
    consecutive_ready = 0
    ready_confirmation_count = 3
    poll_interval_seconds = 0.5
    last_reason = "尚未开始检测"
    last_logged_reason = ""

    while time.time() < deadline:
        windows = find_main_windows(main_window_title_re, class_name_keywords=class_name_keywords)
        best = choose_best_window(windows)
        if best is None:
            consecutive_ready = 0
            last_reason = "未找到匹配标题的目标软件主窗口"
        else:
            responsive = is_window_responsive(best["hwnd"])
            # 最小化窗口必须先恢复：就绪判定要求"未最小化"，若等它就绪再恢复，
            # 恢复动作永远执行不到（死锁），activate_and_maximize 也会空等超时。
            if best["minimized"]:
                _restore_if_minimized(best["hwnd"])
                best["minimized"] = bool(_USER32.IsIconic(best["hwnd"]))
            ready = responsive and not best["minimized"] and best["width"] > 0 and best["height"] > 0
            if ready:
                consecutive_ready += 1
                _LOG_STEP(
                    f"[gm-ready] ready-check {consecutive_ready}/{ready_confirmation_count} "
                    f"hwnd={best['hwnd']} title={best['title']}"
                )
                if consecutive_ready >= ready_confirmation_count:
                    _LOG_STEP(
                        f"[gm-ready] ready hwnd={best['hwnd']} title={best['title']} "
                        f"size={best['width']}x{best['height']}"
                    )
                    return best["hwnd"]
                last_reason = "窗口已响应，继续做稳定性确认"
            else:
                consecutive_ready = 0
                last_reason = (
                    f"窗口存在但未就绪: hwnd={best['hwnd']}, minimized={best['minimized']}, "
                    f"size={best['width']}x{best['height']}, responsive={responsive}"
                )

        titles = [window["title"] for window in windows]
        waiting_message = f"[gm-ready] waiting: {last_reason}; candidates={titles}"
        if waiting_message != last_logged_reason:
            _LOG_STEP(waiting_message)
            last_logged_reason = waiting_message
        time.sleep(poll_interval_seconds)

    raise RuntimeError(f"等待目标软件恢复响应超时: {last_reason}")


def activate_and_maximize_main_window(main_window_title_re, timeout_seconds=60, class_name_keywords=()):
    hwnd = wait_until_main_window_ready(
        main_window_title_re,
        timeout_seconds=timeout_seconds,
        class_name_keywords=class_name_keywords,
    )
    if _USER32.IsIconic(hwnd):
        _USER32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.5)
    _USER32.ShowWindow(hwnd, _SW_MAXIMIZE)
    time.sleep(0.3)
    _USER32.ShowWindow(hwnd, _SW_MAXIMIZE)
    time.sleep(0.3)
    _USER32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    _USER32.ShowWindow(hwnd, _SW_MAXIMIZE)
    time.sleep(0.5)
    return hwnd


def ensure_main_window_foreground(main_window_title_re, timeout_seconds=15, class_name_keywords=()):
    """运行前窗口健康检查 + 置顶（bring to front）。

    找到目标主窗口后：若最小化则恢复（SW_RESTORE，保留原窗口布局），
    再置顶（SetForegroundWindow + BringWindowToTop）。
    不强制最大化、不改变窗口尺寸/位置，避免引入相对区域定位偏移。
    找不到则持续重试直到超时并抛异常（供调用方决定是否中止启动）。

    返回窗口信息 dict：
    {"hwnd": int, "title": str, "minimized": bool, "width": int, "height": int, "rect": {...} | None}
    """
    deadline = time.time() + timeout_seconds
    last_reason = "尚未开始检测"
    while time.time() < deadline:
        windows = find_main_windows(main_window_title_re, class_name_keywords=class_name_keywords)
        best = choose_best_window(windows)
        if best is None:
            last_reason = "未找到匹配标题的目标软件主窗口"
            time.sleep(0.5)
            continue
        hwnd = best["hwnd"]
        if best["minimized"]:
            _USER32.ShowWindow(hwnd, _SW_RESTORE)
            time.sleep(0.4)
        _USER32.SetForegroundWindow(hwnd)
        if hasattr(_USER32, "BringWindowToTop"):
            _USER32.BringWindowToTop(hwnd)
        time.sleep(0.4)
        rect = _GET_WINDOW_RECT(hwnd)
        info = {
            "hwnd": hwnd,
            "title": best["title"],
            "minimized": bool(_USER32.IsIconic(hwnd)),
            "width": best["width"],
            "height": best["height"],
            "rect": None,
        }
        if rect is not None:
            info["rect"] = {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            }
        _LOG_STEP(
            f"[mup-preflight] 窗口健康检查通过，已置顶: hwnd={hwnd} "
            f"title={best['title']} size={best['width']}x{best['height']}"
        )
        return info
    raise RuntimeError(f"运行前窗口健康检查失败: {last_reason}")


def click_unknown_projection_if_present(timeout_seconds=8):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            dialog = Desktop(backend="uia").window(title="Loading DWG File... (100%)")
            if dialog.exists(timeout=0.5):
                ok_button = dialog.child_window(title="确定", control_type="Button")
                if ok_button.exists(timeout=0.5):
                    ok_button.click_input()
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def find_open_dialog(timeout_seconds=5):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            dialog = Desktop(backend="uia").window(title="打开", visible_only=True)
            if dialog.exists(timeout=0.5):
                return dialog
        except Exception:
            pass
        time.sleep(0.3)
    return None


def confirm_open_file_dialog(timeout_seconds=5):
    dialog = find_open_dialog(timeout_seconds=timeout_seconds)
    if dialog is None:
        # 防误触发：没有找到对话框时绝不能盲发 ENTER——回车会落到当前焦点窗口的按钮上，
        # 可能确认了无关对话框。抛错交外层处理（type_path_into_open_dialog 的 pywinauto 兜底）。
        raise RuntimeError("未找到打开文件对话框，已放弃确认（避免盲发 ENTER）")
    try:
        if _CLICK_FLOW_CONTROL("dwg_projection_confirm", "dwg_open_ok", timeout_seconds=1.0, window_title_hint="打开"):
            _LOG_STEP("打开文件对话框确认成功: method=flow_control")
            return
        dialog.set_focus()
        open_button = dialog.child_window(title="打开(O)", control_type="Button")
        if open_button.exists(timeout=0.5):
            open_button.click_input()
            _LOG_STEP("打开文件对话框确认成功: method=pywinauto_button")
            time.sleep(0.8)
            return
    except Exception as exc:
        _LOG_STEP(f"打开文件对话框确认失败，准备回退到 Enter: error={exc}")
    # 对话框已确认存在：ENTER 落在该对话框的默认按钮（打开/确定）上是安全的
    send_keys("{ENTER}")
    _LOG_STEP("打开文件对话框确认成功: method=send_keys_enter")
    time.sleep(0.8)


def type_path_into_open_dialog(file_path, step_id="open_source_dwg", control_id="open_dialog_filename"):
    time.sleep(1)
    try:
        if not _FOCUS_FLOW_CONTROL(step_id, control_id, timeout_seconds=1.5, window_title_hint="打开"):
            _LOG_STEP("文件对话框文件名框未直接命中，尝试 Alt+N 聚焦")
            send_keys("%n")
            time.sleep(0.3)
        send_keys("^a")
        time.sleep(0.2)
        send_keys(file_path)
        time.sleep(0.5)
        _LOG_STEP(f"已通过 send_keys 输入文件路径: step={step_id}, control={control_id}")
        confirm_open_file_dialog(timeout_seconds=3)
    except Exception as exc:
        _LOG_STEP(f"文件路径输入失败，尝试用 pywinauto 处理: {exc}")
        dlg = find_open_dialog(timeout_seconds=10)
        if dlg is None:
            raise RuntimeError("未找到打开文件对话框")
        dlg.set_focus()
        file_edit = dlg.child_window(auto_id="1148", control_type="Edit")
        if not file_edit.exists(timeout=5):
            file_edit = dlg.child_window(title_re="文件名.*", control_type="Edit")
        file_edit.set_text(file_path)
        time.sleep(0.5)
        open_button = dlg.child_window(title="打开(O)", control_type="Button")
        if open_button.exists(timeout=1):
            open_button.click_input()
            _LOG_STEP("已通过 pywinauto 输入并确认文件路径: method=edit_set_text+button")
        else:
            send_keys("{ENTER}")
            _LOG_STEP("已通过 pywinauto 输入文件路径，并用 Enter 确认")
        time.sleep(0.8)
