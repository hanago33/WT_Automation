#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tab 焦点链验证脚本（只读，不改动主流程）

目的
----
针对「用常规 RawViewWalker 采集不到、但键盘 Tab 能到达」的输入框，
验证两件事：
  1. Tab 焦点链能否覆盖这些控件（即它们是否键盘可达）；
  2. 每次 Tab 后能否通过 IUIA().iuia.GetFocusedElement() 拿到「可操作的 wrapper」
     —— 这正是树外控件也能被定位/操作的关键。

做法
----
  1. 连接目标窗口（UIA backend，按关键字模糊匹配）。
  2. 用 RawViewWalker 做一遍全量 BFS，作为「常规采集基准」集合。
  3. 从窗口根 set_focus，循环 SendKeys("{TAB}")，每步用 GetFocusedElement()
     实时取焦点 wrapper，提取字段并记录 tabOrder / 是否 keyboardFocusable。
  4. 计算差集：Tab 可达但 RawView 基准漏掉的控件（重点看输入框类型）。
  5. 输出人类可读摘要 + 可选 JSON 报告（--out）。

注意
----
  * Tab 会真的移动焦点（有副作用，可能触发 focus 事件 / 展开下拉 / 校验），
    本脚本只采集快照、不主动操作任何控件；运行结束会把焦点交还窗口。
  * 焦点若跑到其它进程窗口（如弹出的确认对话框），脚本会立即停止，避免失控。
  * 仅新增本文件，不修改任何既有代码。

用法
----
  python tools/verify_tab_focus_chain.py --window "Global Mapper"
  python tools/verify_tab_focus_chain.py --window "记事本" --max-steps 120 --out report.json
  python tools/verify_tab_focus_chain.py --window "xxx" --no-raw      # 只走焦点链
"""
import sys

# 必须在 import pywinauto / 项目模块前设置 MTA
sys.coinit_flags = 0

import os
import json
import time
import argparse
import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import build_control_map_library as bcl  # noqa: E402


# 视为「输入框」的控件类型（pywinauto normalized control type name）
EDIT_LIKE = {"Edit", "ComboBox", "SpinBox", "Document"}


def _try_reconfigure_stdout():
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_METEO_KEYWORDS = ("meteo", "universe", "mup", "wt_", "wind", "climat", "oasis")


def _pid_to_name(pid):
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        pass
    try:
        import win32api
        import win32process
        handle = win32api.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        exe = win32process.GetModuleFileNameEx(handle, 0)
        win32api.CloseHandle(handle)
        return exe.rsplit("\\", 1)[-1]
    except Exception:
        return ""


def _cmd_list_all(args):
    """枚举系统中所有窗口（含隐藏/工具窗口），按进程分组，标出 Meteodyn 相关进程。

    比 pywinauto 的 .windows() 更底层：能捕捉到子窗口、tool 窗口、无标题窗口，
    避免「GUI 主窗口扫不到」的情况。
    """
    import win32gui
    import win32con

    rows = []

    def _enum(hwnd, _):
        # 整段防御：任何一个窗口异常都不应中断整轮枚举（否则会静默丢失全部结果）
        try:
            if not win32gui.IsWindow(hwnd):
                return
            tid, pid = win32gui.GetWindowThreadProcessId(hwnd)
            cls = win32gui.GetClassName(hwnd) or ""
            title = win32gui.GetWindowText(hwnd) or ""
            visible = bool(win32gui.IsWindowVisible(hwnd))
            parent = win32gui.GetParent(hwnd) or 0
            rows.append((hwnd, pid, cls, title, visible, parent))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception as exc:
        print(f"[WARN] EnumWindows 抛异常（仍使用已收集到的 {len(rows)} 个窗口）: {exc}")

    # 若指定了 PID，只保留该进程相关窗口（直接命中用户提供的进程）
    if args.pid:
        target_pid = int(args.pid)
        rows = [r for r in rows if r[1] == target_pid]
        print(f"[*] 已按 --pid={target_pid} 过滤，匹配窗口数: {len(rows)}")

    # 进程名映射
    pid_names = {}
    for hwnd, pid, cls, title, visible, parent in rows:
        if pid not in pid_names:
            pid_names[pid] = _pid_to_name(pid)

    # 标出 Meteodyn 相关进程
    def _is_meteo(pid, cls, title):
        name = (pid_names.get(pid) or "").lower()
        blob = (name + " " + cls.lower() + " " + title.lower())
        return any(k in blob for k in _METEO_KEYWORDS)

    meteo_rows = [r for r in rows if _is_meteo(*r[:3])]
    other_rows = [r for r in rows if not _is_meteo(*r[:3])]

    if args.pid:
        # 直接按 PID 过滤时，完整打印该进程所有窗口（含隐藏/子/无标题），确保 GUI 不被漏掉
        print("\n>>> 按 --pid 过滤的全部窗口（含隐藏/子/无标题，定位 GUI 用）:")
        if rows:
            for hwnd, pid, cls, title, visible, parent in rows:
                vis = "可见" if visible else "隐藏"
                par = f" parent={parent}" if parent else ""
                print(f"  hwnd={hwnd:<10} | {vis:<4} | class={cls:<28} | title={title!r}{par}")
        else:
            print("  （该 PID 下未枚举到任何窗口）")

    print("=" * 78)
    print("全部窗口枚举（含隐藏/工具窗口）— 按进程分组")
    print("=" * 78)
    print("\n>>> Meteodyn / WT 相关进程窗口:")
    if meteo_rows:
        for hwnd, pid, cls, title, visible, parent in meteo_rows:
            vis = "可见" if visible else "隐藏"
            par = f" parent={parent}" if parent else ""
            print(f"  hwnd={hwnd:<10} | pid={pid:<7} | {vis:<4} | class={cls:<28} | "
                  f"proc={pid_names.get(pid)!r} | title={title!r}{par}")
    else:
        print("  （未找到任何 Meteodyn/WT 相关进程窗口 —— 说明 Meteodyn 当前未运行，或进程名不在关键词表中）")

    print(f"\n>>> 其它进程窗口共 {len(other_rows)} 个（仅列可见且有标题的，便于对照）:")
    shown = 0
    for hwnd, pid, cls, title, visible, parent in other_rows:
        if not visible or not title.strip():
            continue
        if shown >= 40:
            print("  ... (其余省略，用 --list-all 全量已收集)")
            break
        print(f"  hwnd={hwnd:<10} | pid={pid:<7} | class={cls:<22} | proc={pid_names.get(pid)!r} | title={title!r}")
        shown += 1

    print("\n>>> 全部出现的进程名（去重，便于确认 Meteodyn 进程叫什么）:")
    seen_procs = {}
    for hwnd, pid, cls, title, visible, parent in rows:
        name = pid_names.get(pid) or f"pid={pid}"
        seen_procs[name] = seen_procs.get(name, 0) + 1
    for name, cnt in sorted(seen_procs.items()):
        print(f"  {name:<40} 窗口数={cnt}")

    print("\n提示：找到真正的 GUI 窗口 hwnd 后，用 --hwnd <句柄> --verbose 跑焦点链验证。")
    print("      若上方「Meteodyn 相关进程窗口」为空，请把「全部进程名」里像 Meteodyn 的那个告诉我。")
    return 0


def collect_raw_view_identities(center_wrapper, max_elements=8000):
    """用 RawViewWalker 全量 BFS，返回 identity 集合（常规采集基准）。"""
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper

    walker = IUIA().iuia.RawViewWalker
    root = center_wrapper.element_info.element
    identities = set()
    queue = [root]
    while queue and len(identities) < max_elements:
        elem = queue.pop(0)
        try:
            info = UIAElementInfo(elem)
        except Exception:
            continue
        try:
            identities.add(bcl._build_wrapper_identity(UIAWrapper(info)))
        except Exception:
            pass
        child = walker.GetFirstChildElement(elem)
        while child:
            queue.append(child)
            child = walker.GetNextSiblingElement(child)
    return identities


def collect_focus_chain(window_wrapper, max_steps=200, step_delay=0.05, verbose=False):
    """沿 Tab 焦点顺序遍历，返回每个获得焦点的控件快照列表。

    每个元素为 _extract_wrapper_info 的结果 dict，并追加：
      tabOrder            : 第几次 Tab 到达（0 为初始焦点）
      _identity           : 与基准一致的 identity 字符串（用于差集对比）
    """
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper

    try:
        from pywinauto_recorder.player import send_keys
    except Exception:
        from pywinauto.keyboard import send_keys

    # 强制把窗口提到前台，确保 Tab 真正进入目标窗口（否则 SendInput 会发到别的窗口）
    try:
        import win32gui
        win32gui.SetForegroundWindow(window_wrapper.handle)
    except Exception:
        pass
    window_wrapper.set_focus()
    time.sleep(0.3)

    iuia = IUIA().iuia
    target_window = bcl._build_target_window_info(window_wrapper)
    try:
        target_pid = int(window_wrapper.process_id())
    except Exception:
        target_pid = None

    chain = []
    seen_ids = set()
    advanced = False  # Tab 是否真的让焦点前进过
    for step in range(max_steps):
        try:
            element = iuia.GetFocusedElement()
        except Exception:
            break
        if element is None:
            break
        try:
            info = UIAElementInfo(element)
            wrapper = UIAWrapper(info)
        except Exception:
            break
        # 进程保护：焦点跑到别的进程窗口就停（防弹出对话框失控）
        if target_pid is not None:
            try:
                if int(info.process_id) != target_pid:
                    break
            except Exception:
                pass
        try:
            ident = bcl._build_wrapper_identity(wrapper)
        except Exception:
            ident = f"step{step}"
        if ident in seen_ids:
            break  # 焦点环闭合
        if step > 0:
            advanced = True
        seen_ids.add(ident)
        try:
            detail = bcl._extract_wrapper_info(wrapper, step, step + 1, [], target_window)
        except Exception:
            detail = {
                "automationId": getattr(info, "automation_id", ""),
                "name": getattr(info, "name", ""),
                "controlType": getattr(info, "control_type", ""),
                "className": getattr(info, "class_name", ""),
            }
        detail["tabOrder"] = step
        detail["_identity"] = ident
        chain.append(detail)
        if verbose:
            box = detail.get("boundingBox") or {}
            box_str = (
                f"[{box.get('left')},{box.get('top')},{box.get('right')},{box.get('bottom')}]"
                if box else "[]"
            )
            print(f"    Tab#{step:>3} | {detail.get('controlType'):<10} | "
                  f"id={detail.get('automationId')!r:<24} | name={detail.get('name')!r:<20} | "
                  f"{box_str} | class={detail.get('className')}")
        try:
            send_keys("{TAB}")
        except Exception:
            break
        time.sleep(step_delay)
    return chain, advanced


def _summarize(chain, raw_ids, no_raw):
    chain_ids = {c.get("_identity") for c in chain}
    missing_in_raw = [c for c in chain if no_raw or c.get("_identity") not in raw_ids]
    missing_inputs = [c for c in missing_in_raw if c.get("controlType") in EDIT_LIKE]
    return {
        "focus_chain_total": len(chain),
        "raw_view_baseline_total": (None if no_raw else len(raw_ids)),
        "tab_reachable_not_in_raw": len(missing_in_raw),
        "tab_reachable_inputs_not_in_raw": len(missing_inputs),
        "missing_in_raw": missing_in_raw,
        "missing_inputs": missing_inputs,
    }


def _print_summary(summary, window_title):
    line = "=" * 64
    print(line)
    print(f"Tab 焦点链验证报告 — 窗口: {window_title}")
    print(line)
    print(f"  焦点链可达控件总数        : {summary['focus_chain_total']}")
    if summary["raw_view_baseline_total"] is not None:
        print(f"  RawView 基准控件总数      : {summary['raw_view_baseline_total']}")
        print(f"  Tab 可达但 RawView 漏掉   : {summary['tab_reachable_not_in_raw']}")
        print(f"    其中输入框类            : {summary['tab_reachable_inputs_not_in_raw']}")
    print(line)
    if summary["missing_inputs"]:
        print("  ★ 以下输入框 Tab 可达、但常规采集(RawView)漏掉了 —— 适合用 Tab 轮换定位:")
        for c in summary["missing_inputs"]:
            box = c.get("boundingBox") or {}
            box_str = (
                f"[{box.get('left')},{box.get('top')},{box.get('right')},{box.get('bottom')}]"
                if box else "[]"
            )
            print(
                f"    Tab#{c.get('tabOrder'):>3} | {c.get('controlType'):<10} | "
                f"id={c.get('automationId')!r:<24} | name={c.get('name')!r:<20} | "
                f"{box_str} | class={c.get('className')}"
            )
    else:
        print("  未发现「Tab 可达但 RawView 漏掉」的输入框。")
    print(line)


def main():
    _try_reconfigure_stdout()
    parser = argparse.ArgumentParser(
        description="验证 Tab 焦点链能否覆盖常规采集(RawView)漏掉的输入框（只读）"
    )
    parser.add_argument("--window", default=None, help="目标窗口标题关键字(模糊匹配)")
    parser.add_argument("--hwnd", default=None, help="目标窗口句柄(精确匹配，绕开同名歧义)")
    parser.add_argument("--pid", default=None, help="仅枚举该 PID 的窗口(如 --pid 14064)，用于精准定位某进程的所有窗口")
    parser.add_argument("--list-windows", action="store_true",
                        help="列出当前所有可见窗口(含 hwnd 与 class)，用于确定 --hwnd/--window")
    parser.add_argument("--list-all", action="store_true",
                        help="枚举系统全部窗口(含隐藏/子/工具窗口)，按进程分组并高亮 Meteodyn 相关进程")
    parser.add_argument("--max-steps", type=int, default=200, help="Tab 遍历最大步数(默认200)")
    parser.add_argument("--max-elements", type=int, default=8000, help="RawView BFS 控件上限")
    parser.add_argument("--out", default=None, help="可选：把完整报告写到该 JSON 路径")
    parser.add_argument("--no-raw", action="store_true", help="跳过 RawView 基准对比，只走焦点链")
    parser.add_argument("--verbose", action="store_true", help="逐步打印每个 Tab 到达的控件（调试用）")
    args = parser.parse_args()

    if args.list_windows:
        try:
            from pywinauto import Desktop
            wins = Desktop(backend="uia").windows()
        except Exception as exc:
            print(f"[FAIL] 枚举窗口失败: {exc}")
            return 1
        print("当前可见窗口（挑真正的 GUI 窗口，注意 class=TermControl 是终端/日志窗口，应避开）:")
        for w in wins:
            title = w.window_text()
            if not title.strip():
                continue
            try:
                hwnd = int(w.handle)
                cls = w.element_info.class_name or ""
            except Exception:
                hwnd, cls = -1, ""
            print(f"  hwnd={hwnd:<10} | class={cls:<22} | title={title!r}")
        print("提示：复制目标 GUI 窗口的 hwnd，然后用 --hwnd <句柄> 精确指定（可绕过同名歧义）。")
        return 0

    if args.list_all or args.pid:
        return _cmd_list_all(args)

    if args.hwnd:
        try:
            from pywinauto import Desktop
            window_wrapper = Desktop(backend="uia").window(handle=int(args.hwnd))
            window_wrapper.window_text()  # 触发一下，确认窗口存在
        except Exception as exc:
            print(f"[FAIL] 未能通过 hwnd={args.hwnd} 连接窗口: {exc}")
            return 1
    elif args.window:
        try:
            window_wrapper = bcl._find_window_by_keyword(args.window, "uia")
        except Exception as exc:
            print(f"[FAIL] 未找到匹配窗口: {exc}")
            return 1
    else:
        print("[FAIL] 请用 --window 指定窗口关键字，或用 --hwnd 指定句柄，或先用 --list-windows 查看")
        return 1

    window_title = str(bcl._safe_get_value(lambda: window_wrapper.window_text(), "")).strip()
    print(f"[OK] 已连接窗口: {window_title!r}")

    raw_ids = set()
    if not args.no_raw:
        print("[*] 正在用 RawViewWalker 做常规采集基准 ...")
        raw_ids = collect_raw_view_identities(window_wrapper, args.max_elements)
        print(f"[*] RawView 基准控件数: {len(raw_ids)}")

    print(f"[*] 正在走 Tab 焦点链 (最多 {args.max_steps} 步, 有副作用: 会移动焦点) ...")
    chain, advanced = collect_focus_chain(window_wrapper, args.max_steps, verbose=args.verbose)
    # 结束还原焦点
    try:
        window_wrapper.set_focus()
    except Exception:
        pass

    if not advanced:
        print("[WARN] Tab 焦点链未能让焦点前进（始终停在同一个控件）。")
        print("       可能原因：(1) 该窗口是 3D/自绘视口，没有可 Tab 导航的子控件；")
        print("                 (2) 焦点未真正进入窗口。")
        print("       => 请确认目标输入框所在的具体对话框/子窗口，而不是主视口本身；")
        print("          先打开那个子窗口，再 --list-windows 找到它的标题，单独对它跑本脚本。")

    summary = _summarize(chain, raw_ids, args.no_raw)
    _print_summary(summary, window_title)

    if args.out:
        payload = {
            "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
            "windowTitle": window_title,
            "summary": {k: v for k, v in summary.items() if k not in ("missing_in_raw", "missing_inputs")},
            "focusChain": [{k: v for k, v in c.items() if k != "_identity"} for c in chain],
            "missingInRaw": [{k: v for k, v in c.items() if k != "_identity"} for c in summary["missing_in_raw"]],
        }
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[OK] 完整报告已写出: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
