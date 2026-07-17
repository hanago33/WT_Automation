# encoding: utf-8
"""UiaPeek 适配器 —— 通过 HTTP 调用本地 UiaPeek 服务获取控件祖先链。

UiaPeek (MIT, https://github.com/g4-api/uia-peek) 是第三方开源的 Windows UI
Automation 检查/录制工具，源码见 ``vendor/uia-peek``。它以 REST + SignalR 暴露
UIA 信息，本模块只做 HTTP 客户端，不依赖 .NET。

运行前提（一次性）：
  1. 从 https://github.com/g4-api/uia-peek/releases 下载压缩包解压；
  2. 以管理员身份运行 ``UiaPeek.exe``（监听 http://localhost:9955）；
  3. 验证：``curl http://localhost:9955/api/v4/g4/ping`` 返回 Pong。

本模块【完全独立】，不影响现有 pywinauto 采集（build_control_map_library.py）。
输出 control_maps/*_uiapeek_control_map.json，格式与现有快照一致，
tools/merge_standard_control_library.py 可直接合并。

用法（命令行）::
    python -m tools.external_capture.uiapeek_client --x 250 --y 300
    python -m tools.external_capture.uiapeek_client --focused
    python -m tools.external_capture.uiapeek_client --record 10   # 录制 10 秒
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import normalize as nz

DEFAULT_BASE_URL = "http://localhost:9955"
DEFAULT_TIMEOUT = 6

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONTROL_MAP_DIR = os.path.join(REPO_ROOT, "control_maps")


# ---------------------------------------------------------------------------
# 底层 HTTP
# ---------------------------------------------------------------------------
def ping(base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT):
    """健康检查，返回 True/False。"""
    url = base_url.rstrip("/") + "/api/v4/g4/ping"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:
        return False


def _get_json(url, timeout=DEFAULT_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except ValueError:
        # peek 接口正常返回 chain 对象；个别情况可能返回裸字符串
        return {"_raw": raw}


def peek_at(x, y, base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT):
    """按屏幕坐标 peek，返回 chain 对象（dict）或 None。"""
    url = "{}/api/v4/g4/peek?x={}&y={}".format(base_url.rstrip("/"), int(x), int(y))
    return _get_json(url, timeout)


def peek_focused(base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT):
    """按当前焦点元素 peek，返回 chain 对象或 None。"""
    url = base_url.rstrip("/") + "/api/v4/g4/peek?focused=true"
    return _get_json(url, timeout)


# ---------------------------------------------------------------------------
# 数据转换：uia-peek chain -> WT control_definition
# ---------------------------------------------------------------------------
def bounds_to_rect(bounds):
    """uia-peek bounds {X,Y,width,height} -> WT 的 boundingRectangle 字符串 + dict。"""
    bounds = bounds if isinstance(bounds, dict) else {}
    x = int(bounds.get("X", bounds.get("x", 0)) or 0)
    y = int(bounds.get("Y", bounds.get("y", 0)) or 0)
    w = int(bounds.get("width", bounds.get("Width", 0)) or 0)
    h = int(bounds.get("height", bounds.get("Height", 0)) or 0)
    rect_str = "[l={},t={},r={},b={}]".format(x, y, x + w, y + h)
    box = {"left": x, "top": y, "right": x + w, "bottom": y + h}
    return rect_str, box


def node_to_control_definition(node, window_title="", framework_id="", depth=0, index=0,
                                ui_path="", parent_path="", is_trigger=False):
    """把 uia-peek chain 节点转成 control_definition（经 normalize 层统一规整）。
    与 build_control_map_library 输出同构，可被 merge_standard_control_library 合并。
    """
    _rect_str, box = bounds_to_rect(node.get("bounds"))
    extra = {
        "controlTypeId": str(node.get("controlTypeId", "")).strip(),
        "isTopWindow": str(node.get("isTopWindow", "")).strip(),
    }
    return nz.build_control_definition(
        name=node.get("name", ""),
        automation_id=node.get("automationId", ""),
        class_name=node.get("className", ""),
        control_type=node.get("controlType", ""),
        framework_id=framework_id,
        window_title=window_title,
        process_id=node.get("processId", ""),
        native_window_handle=node.get("nativeWindowHandle", ""),
        patterns=None,
        source="uia-peek",
        extra=extra,
        is_trigger=is_trigger,
        depth=depth,
        index=index,
        ui_path=ui_path,
        parent_path=parent_path,
    )


def chain_to_payload(chain, source_label="uia-peek"):
    """把 uia-peek 的 chain 对象转成 WT control_map 兼容 payload。

    chain.path 是 top-down 祖先链，末位是目标元素（isTriggerElement=true）。
    会生成一个扁平 controlsTree（depth 0=根）+ controlDefinitions 列表。
    """
    chain = chain if isinstance(chain, dict) else {}
    # REST peek 直接返回 chain；SignalR ReceivePeek 包在 {"value": {...}} 里
    if "chain" in chain and isinstance(chain["chain"], dict):
        chain = chain["chain"]
    path = chain.get("path") or []
    if not isinstance(path, list) or not path:
        return None

    # 推断窗口标题/框架：取 isTopWindow 节点，否则取第一个 Window 类型节点，否则 path[0]
    window_title = ""
    framework_id = ""
    top_node = None
    for node in path:
        if node.get("isTopWindow"):
            top_node = node
            break
    if top_node is None:
        for node in path:
            if str(node.get("controlType", "")).strip().lower() == "window":
                top_node = node
                break
    if top_node is None:
        top_node = path[0]
    window_title = str(top_node.get("name", "")).strip() or "UiaPeekWindow"
    pid = str(top_node.get("processId", "")).strip()

    control_defs = []
    tree_children = []
    for idx, node in enumerate(path):
        cdef = node_to_control_definition(
            node, window_title=window_title, framework_id=framework_id,
            depth=idx, index=idx + 1,
            ui_path=" > ".join(
                str(p.get("name", "") or p.get("controlType", "")).strip() or "node"
                for p in path[: idx + 1]
            ),
            parent_path=" > ".join(
                str(p.get("name", "") or p.get("controlType", "")).strip() or "node"
                for p in path[:idx]
            ),
            is_trigger=node.get("isTriggerElement"),
        )
        control_defs.append(cdef)

    # controlsTree：根 = top_node，链上其余作为嵌套 children（保持树形外观）
    def _build_tree(idx):
        if idx >= len(path):
            return None
        node = path[idx]
        cdef = control_defs[idx]
        tree = {
            "depth": idx,
            "index": idx + 1,
            "displayName": str(node.get("name", "") or node.get("controlType", "")).strip() or "node",
            "windowTitle": window_title,
            "name": cdef["name"],
            "className": cdef["className"],
            "controlType": cdef["controlType"],
            "localizedControlType": "",
            "automationId": cdef["inspectData"]["automationId"],
            "frameworkId": framework_id,
            "processId": pid,
            "boundingRectangle": cdef["inspectData"]["boundingRectangle"],
            "boundingBox": cdef["boundingBox"],
            "locatorScore": cdef["locatorScore"],
            "locatorReason": cdef["locatorReason"],
            "recommendedTargetMethod": cdef["targetMethod"],
            "recommendedTargetValue": cdef["targetValue"],
            "uiPath": cdef["uiPath"],
            "parentPath": cdef["parentPath"],
            "auxChecks": cdef["auxChecks"],
            "inspectData": cdef["inspectData"],
        }
        child = _build_tree(idx + 1)
        if child is not None:
            tree["children"] = [child]
        return tree

    controls_tree = _build_tree(0)

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": "uiapeek",
            "source": source_label,
            "locator": chain.get("locator", ""),
            "trigger": chain.get("trigger", ""),
            "pathDepth": len(path),
            "totalControls": len(control_defs),
        },
        "targetWindow": {
            "title": window_title,
            "className": str(top_node.get("className", "")).strip(),
            "processId": pid,
            "handle": "",
            "frameworkId": framework_id,
        },
        "controlsTree": controls_tree,
        "flatControls": nz.definitions_to_flat_controls(control_defs, window_title),
        "controlDefinitions": control_defs,
    }


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def _slugify(text, fallback="window"):
    text = str(text or "").strip()
    if not text:
        return fallback
    import re
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


def save_payload(payload, control_map_dir=CONTROL_MAP_DIR):
    """把 payload 落盘到 control_maps/，返回文件路径。"""
    if not payload:
        return None
    os.makedirs(control_map_dir, exist_ok=True)
    title = (payload.get("targetWindow") or {}).get("title", "window")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = "{}_{}_uiapeek_control_map.json".format(ts, _slugify(title))
    fp = os.path.join(control_map_dir, fname)
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return fp


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def capture_at(x, y, base_url=DEFAULT_BASE_URL, save=True, control_map_dir=CONTROL_MAP_DIR):
    """peek 屏幕坐标并（可选）落盘，返回 (payload, file_path)。"""
    if not ping(base_url):
        raise RuntimeError(
            "UiaPeek 服务未运行（{} 不可达）。请先以管理员身份运行 UiaPeek.exe。".format(base_url)
        )
    chain = peek_at(x, y, base_url=base_url)
    payload = chain_to_payload(chain)
    if payload is None:
        raise RuntimeError("UiaPeek peek 返回空链：x={}, y={}".format(x, y))
    fp = save_payload(payload, control_map_dir) if save else None
    return payload, fp


def capture_focused(base_url=DEFAULT_BASE_URL, save=True, control_map_dir=CONTROL_MAP_DIR):
    """peek 当前焦点元素并（可选）落盘。"""
    if not ping(base_url):
        raise RuntimeError(
            "UiaPeek 服务未运行（{} 不可达）。请先以管理员身份运行 UiaPeek.exe。".format(base_url)
        )
    chain = peek_focused(base_url=base_url)
    payload = chain_to_payload(chain)
    if payload is None:
        raise RuntimeError("UiaPeek focused peek 返回空链")
    fp = save_payload(payload, control_map_dir) if save else None
    return payload, fp


# ---------------------------------------------------------------------------
# 可选：SignalR 实时录制流（依赖 signalrcore，未安装则降级提示）
# ---------------------------------------------------------------------------
def record_events(duration_seconds=10, base_url=DEFAULT_BASE_URL, on_event=None):
    """连接 UiaPeek SignalR Hub，录制 duration_seconds 秒的键鼠事件（带 UI 上下文）。

    需 ``pip install signalrcore``。返回事件列表。未安装则抛出带提示的 RuntimeError。
    on_event(event) 回调可实时处理每个事件。
    """
    try:
        from signalrcore.hub_connection_builder import HubConnectionBuilder
    except ImportError:
        raise RuntimeError(
            "录制需要 signalrcore：pip install signalrcore。或改用 REST peek（--x/--y 或 --focused）。"
        )

    hub_url = base_url.rstrip("/") + "/hub/v4/g4/peek"
    events = []

    conn = (
        HubConnectionBuilder()
        .with_url(hub_url)
        .configure_logging(None)
        .with_automatic_reconnect({"type": "raw", "keep_alive_interval": 10, "reconnect_interval": 5, "max_attempts": 5})
        .build()
    )

    def _on_recording(ev):
        events.append(ev)
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    conn.on("ReceiveRecordingEvent", _on_recording)
    conn.on("ReceivePeek", events.append)

    conn.start()
    try:
        time.sleep(max(1, int(duration_seconds)))
    finally:
        try:
            conn.stop()
        except Exception:
            pass
    return events


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def _main():
    import argparse

    ap = argparse.ArgumentParser(description="UiaPeek 控件采集适配器（HTTP 客户端）")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="UiaPeek 服务地址")
    ap.add_argument("--x", type=int, help="peek 的屏幕 X 坐标")
    ap.add_argument("--y", type=int, help="peek 的屏幕 Y 坐标")
    ap.add_argument("--focused", action="store_true", help="peek 当前焦点元素")
    ap.add_argument("--record", type=int, metavar="SECONDS", help="SignalR 录制 N 秒事件流")
    ap.add_argument("--no-save", action="store_true", help="不落盘，仅打印")
    ap.add_argument("--ping", action="store_true", help="仅健康检查")
    args = ap.parse_args()

    if args.ping:
        ok = ping(args.base_url)
        print("UiaPeek {} -> {}".format(args.base_url, "OK (Pong)" if ok else "不可达"))
        return 0 if ok else 1

    if args.record:
        print("录制 {} 秒（需 signalrcore）...".format(args.record))
        evs = record_events(args.record, base_url=args.base_url)
        print("收到 {} 个事件".format(len(evs)))
        if evs and not args.no_save:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = os.path.join(CONTROL_MAP_DIR, "{}_uiapeek_recording.json".format(ts))
            os.makedirs(CONTROL_MAP_DIR, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(evs, fh, ensure_ascii=False, indent=2)
            print("已保存: {}".format(fp))
        return 0

    if args.focused:
        payload, fp = capture_focused(base_url=args.base_url, save=not args.no_save)
    elif args.x is not None and args.y is not None:
        payload, fp = capture_at(args.x, args.y, base_url=args.base_url, save=not args.no_save)
    else:
        ap.print_help()
        return 2

    meta = payload.get("scanMeta", {})
    tw = payload.get("targetWindow", {})
    print("窗口: {} | 控件数: {} | 链深: {}".format(
        tw.get("title", ""), meta.get("totalControls", 0), meta.get("pathDepth", 0)))
    for c in payload.get("controlDefinitions", []):
        flag = " <- 触发元素" if c.get("isTriggerElement") else ""
        print("  [{:>2}] {:<16} aid={!r:<30} name={!r}{}".format(
            c.get("index"), c.get("controlType"), c.get("inspectData", {}).get("automationId"),
            c.get("name"), flag))
    if fp:
        print("已保存: {}".format(fp))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
