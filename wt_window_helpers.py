# encoding: utf-8

import ctypes
import ctypes.wintypes  # 需显式导入，否则 ctypes.wintypes 运行时抛 AttributeError
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


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _get_window_owning_process_name(hwnd):
    """取窗口所属进程的可执行文件名（跨 UIPI/权限隔离可用）。

    GetWindowTextW/GetClassNameW 对提权窗口会被 UIPI 挡成空串，
    但 GetWindowThreadProcessId（内核态取 PID）+ PROCESS_QUERY_LIMITED_INFORMATION
    的 QueryFullProcessImageNameW（任务管理器同款）可在任意完整性级别读回进程名，
    用于在管理员/普通权限混搭时仍能正确定位 MUP 主窗口。
    """
    process_id = ctypes.wintypes.DWORD()
    get_wtid = _USER32.GetWindowThreadProcessId
    get_wtid.restype = ctypes.wintypes.DWORD
    get_wtid.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
    try:
        if not get_wtid(hwnd, ctypes.byref(process_id)):
            return ""
    except Exception:
        return ""
    pid = int(process_id.value or 0)
    if not pid:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32
        open_process = kernel32.OpenProcess
        open_process.restype = ctypes.wintypes.HANDLE
        open_process.argtypes = [ctypes.wintypes.DWORD, ctypes.c_int, ctypes.wintypes.DWORD]
        handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            # 注意：QueryFullProcessImageNameW 的第 4 个参数是"字符数"而非字节数，
            # buffer 容量为 32768 字符。传字节数（sizeof(buffer)=65536）会让内核认为
            # 缓冲区更大，路径足够长时可到写越界并破坏堆（0xc0000374 的潜在来源）。
            size = ctypes.wintypes.DWORD(len(buffer))
            query_name = getattr(kernel32, "QueryFullProcessImageNameW", None)
            if query_name:
                query_name.restype = ctypes.c_int
                query_name.argtypes = [
                    ctypes.wintypes.HANDLE,
                    ctypes.wintypes.DWORD,
                    ctypes.wintypes.LPWSTR,
                    ctypes.POINTER(ctypes.wintypes.DWORD),
                ]
                if query_name(handle, 0, buffer, ctypes.byref(size)):
                    return buffer.value
        finally:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [ctypes.wintypes.HANDLE]
            close_handle(handle)
    except Exception:
        pass
    return ""


def find_main_windows(main_window_title_re, class_name_keywords=()):
    """枚举目标软件主窗口候选。

    - 默认按标题正则匹配（`main_window_title_re`），保持既有行为。
    - 传入 `class_name_keywords`（如 ("MUPSmartClient",)）时，标题为空或
      不匹配正则的可见窗口，若其窗口类名**或所属进程名**包含任一关键词
      （不区分大小写）也纳入候选——用于 MUP 主窗口标题为空、仅靠类名可识别
      的场景；进程名匹配不受 UIPI 影响（普通权限 WT 也能读到提权 MUP 主窗口），
      避免前置顶误把 PowerShell 类后台窗口当主窗口置顶。
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
        process_matched = False
        if not title_matched and keywords:
            class_buf = ctypes.create_unicode_buffer(256)
            if _USER32.GetClassNameW(hwnd, class_buf, 256):
                class_name = class_buf.value or ""
                class_matched = any(k.lower() in class_name.lower() for k in keywords)
            # 标题/类名被 UIPI 挡空（提权 MUP 窗口）时，退级按进程名识别：
            # MUPSmartClient.exe 的窗口即使普通权限也能被正确定位。
            if not class_matched:
                process_name = _get_window_owning_process_name(hwnd)
                process_matched = bool(process_name) and any(
                    k.lower() in process_name.lower() for k in keywords
                )
        if not title_matched and not class_matched and not process_matched:
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


# 主窗口最小可接受尺寸门槛。用于过滤 276x45 这类残留/后台小窗口，
# 避免它们在真主窗口最小化（或标题为空无法识别）时被当作主窗口前置。
MIN_MAIN_WINDOW_WIDTH = 400
MIN_MAIN_WINDOW_HEIGHT = 300


def choose_best_window(windows):
    if not windows:
        return None

    def sort_key(window):
        return (
            0 if window["minimized"] else 1,
            window["width"] * window["height"],
        )

    # 存在足够大的窗口时，只在这些达标窗口中择优，排除 276x45 这类小窗口；
    # 全部窗口都偏小时回退取面积最大的，保证有候选可用。
    substantial = [
        w for w in windows
        if w["width"] >= MIN_MAIN_WINDOW_WIDTH
        and w["height"] >= MIN_MAIN_WINDOW_HEIGHT
    ]
    pool = substantial or windows
    return max(pool, key=sort_key)


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

    找到达标（达到 MIN_MAIN_WINDOW_* 门槛）的目标主窗口后：若最小化则恢复
    （SW_RESTORE，保留原窗口布局），再置顶（SetForegroundWindow + BringWindowToTop）。
    不强制最大化、不改变窗口尺寸/位置，避免引入相对区域定位偏移。

    找不到窗口，或轮询超时后仍只有尺寸偏小的候选（如 276x45 的未就绪窗口，
    不可能是正确主窗口）时抛异常——由调用方决定中止启动，绝不把偏小窗口
    当作主窗口置顶后继续（避免第一步在错窗口上假成功、后续全部空转）。

    返回窗口信息 dict：
    {"hwnd": int, "title": str, "minimized": bool, "width": int, "height": int, "rect": {...} | None}
    """
    deadline = time.time() + timeout_seconds
    last_reason = "尚未开始检测"
    last_resort = None  # 全部候选都偏小（未达门槛）时的兜底窗口
    while time.time() < deadline:
        windows = find_main_windows(main_window_title_re, class_name_keywords=class_name_keywords)
        best = choose_best_window(windows)
        if best is None:
            last_reason = "未找到匹配标题的目标软件主窗口"
            time.sleep(0.5)
            continue
        # 只有偏小的候选（如 276x45 残留窗口）时不急于置顶：继续轮询等待
        # 更大的主窗口出现，避免把错误的小窗口提升到前台并误报健康检查通过。
        if best["width"] < MIN_MAIN_WINDOW_WIDTH or best["height"] < MIN_MAIN_WINDOW_HEIGHT:
            last_resort = best
            last_reason = (
                f"仅找到尺寸偏小的候选窗口，等待更大的主窗口: "
                f"hwnd={best['hwnd']} size={best['width']}x{best['height']}"
            )
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
    # 轮询超时后：若仅存在偏小候选（主窗口未就绪），明确中止，
    # 绝不把 276x45 这类未就绪窗口当作主窗口置顶继续。
    if last_resort is not None:
        raise RuntimeError(
            f"目标软件主窗口未就绪：仅找到尺寸偏小的候选窗口 "
            f"hwnd={last_resort['hwnd']} size={last_resort['width']}x{last_resort['height']}。"
            f"请先打开目标软件并等待主窗口加载到正常尺寸后重新运行"
        )
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
