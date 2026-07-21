# encoding: utf-8
"""Axe.Windows 适配器 —— 调用 AxeWindowsCLI 或 C# bridge 扫描进程，提取元素属性/Patterns。

Axe.Windows (MIT, https://github.com/microsoft/axe-windows) 是微软官方开源的无障碍
测试引擎，也是 AccessibilityInsights 的底层，源码见 ``vendor/axe-windows``。
它能拿到 UIA 元素的 Properties + Patterns（Invoke/Value/Toggle/Selection...），
正好补充 WT_Automation 评分所需的"控件模式校验"维度。

两种接入方式（按成熟度排序）：

1. **CLI 模式**（开箱，但信息有限）
   需先安装 AxeWindowsCLI（MSI，见 vendor/axe-windows/src/CLI/README.MD）。
   调 ``AxeWindowsCLI.exe --processid <pid> --verbosity verbose --alwayssavetestfile``，
   生成 .a11ytest 文件（可用 AccessibilityInsights 打开）+ 控制台摘要。
   CLI 不直接输出结构化元素树，本模块尽力解析 stdout，主要价值是登记 a11ytest。

2. **Bridge 模式**（需 .NET SDK 编译，信息最全）
   用 ``axewindows_bridge/`` 下的 C# 源码（引用 Axe.Windows NuGet 包），
   ``dotnet run --project axewindows_bridge -- <pid>`` 输出 JSON，
   含每个违规元素的 Properties + Patterns。本模块解析 JSON 转 control_definition。

本模块【完全独立】，不影响现有 pywinauto 采集。输出 control_maps/*_axewindows_control_map.json。

用法（命令行）::
    python -m tools.external_capture.axewindows_client --pid 1234
    python -m tools.external_capture.axewindows_client --pid 1234 --bridge
    python -m tools.external_capture.axewindows_client --find-cli
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONTROL_MAP_DIR = os.path.join(REPO_ROOT, "control_maps")
BRIDGE_DIR = os.path.join(os.path.dirname(__file__), "axewindows_bridge")

# AxeWindowsCLI.exe 常见安装路径
CLI_SEARCH_PATHS = [
    r"C:\Program Files (x86)\AxeWindowsCLI",
    r"C:\Program Files\AxeWindowsCLI",
    os.path.join(REPO_ROOT, "vendor", "axe-windows", "tools"),
]


# ---------------------------------------------------------------------------
# exe 查找
# ---------------------------------------------------------------------------
def find_cli_exe():
    """在常见路径查找 AxeWindowsCLI.exe，返回路径或 None。"""
    for base in CLI_SEARCH_PATHS:
        if not base or not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if f.lower() == "axewindowscli.exe":
                    return os.path.join(dirpath, f)
    # PATH 兜底
    from shutil import which
    return which("AxeWindowsCLI.exe") or which("AxeWindowsCLI")


def find_bridge_exe():
    """查找已编译的 AxeBridge.exe，返回路径或 None。"""
    candidates = []
    if os.path.isdir(BRIDGE_DIR):
        for dirpath, _dirs, files in os.walk(BRIDGE_DIR):
            for f in files:
                if f.lower() in ("axebridge.exe", "axewindows_bridge.exe"):
                    candidates.append(os.path.join(dirpath, f))
    from shutil import which
    w = which("AxeBridge.exe")
    if w:
        candidates.append(w)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# 数据转换：axe bridge JSON -> WT control_definition
# ---------------------------------------------------------------------------
def _rect_from_props(props):
    """从 properties dict 提取 BoundingRectangle，返回 WT 格式字符串 + box。"""
    rect = str(props.get("BoundingRectangle", props.get("boundingRectangle", "")) or "").strip()
    if not rect:
        return "", {}
    m = re.search(r"l\s*[:=]\s*(-?\d+).*?t\s*[:=]\s*(-?\d+).*?r\s*[:=]\s*(-?\d+).*?b\s*[:=]\s*(-?\d+)", rect, re.I)
    if not m:
        m = re.match(r"\[?(-?\d+)[,\s]+(-?\d+)[,\s]+(-?\d+)[,\s]+(-?\d+)\]?", rect)
    if m:
        l, t, r, b = (int(m.group(i)) for i in range(1, 5))
        return "[l={},t={},r={},b={}]".format(l, t, r, b), {"left": l, "top": t, "right": r, "bottom": b}
    return rect, {}


def _normalize_patterns(patterns):
    """Patterns 列表归一化为字符串列表。"""
    if not patterns:
        return []
    out = []
    for p in patterns:
        s = str(p or "").strip()
        s = re.sub(r"^I?(\w+)Pattern$", r"\1", s)
        if s:
            out.append(s)
    return sorted(set(out))


# UIA ControlType 程序化 ID 取值范围（50000~50040，新版可能略增）。
# 仅当括号里的数字是该范围内的控件类型 ID 时才剥离，避免误伤控件名里
# 恰好带括号数字的情况（例如名为 "Edit(2)" 的控件会被错误规整为 "Edit"）。
_UIA_CONTROL_TYPE_ID_MIN = 50000
_UIA_CONTROL_TYPE_ID_MAX = 50199


def _looks_like_control_type_id(num_text):
    try:
        num = int(str(num_text).strip())
    except (TypeError, ValueError):
        return False
    return _UIA_CONTROL_TYPE_ID_MIN <= num <= _UIA_CONTROL_TYPE_ID_MAX


def _strip_control_type_id(ctype):
    """把 Axe.Windows 的 'Button(50000)' 规整为 'Button'。

    axe-windows 的 ControlType 是 'Name(数字ID)' 形式（UIA 程序化名），
    而运行时 pywinauto UIA 后端返回干净名 'Button'，带 ID 会导致
    ``wrapper_matches_locator`` 的 control_type 匹配直接失败。必须剥离。
    但只剥离真正的控件类型 ID（数字落在 UIA ControlType ID 区间内）。
    """
    if not ctype:
        return ctype
    text = str(ctype).strip()
    m = re.match(r"^(.*?)\s*\(\s*(\d+)\s*\)$", text)
    if m and _looks_like_control_type_id(m.group(2)):
        return m.group(1).strip() or text
    return text


def _strip_control_type_in_path(path):
    """把 uiPath/parentPath 中每段 'X(数字ID)' 规整为 'X'（仅限控件类型 ID）。"""
    if not path:
        return path

    def _repl(match):
        name, num = match.group(1), match.group(2)
        if _looks_like_control_type_id(num):
            return name
        return match.group(0)

    return re.sub(r"(\w+)\s*\(\s*(\d+)\s*\)", _repl, str(path))


def bridge_element_to_control_definition(elem, window_title="", framework_id=""):
    """把 bridge 输出的单个元素转成 control_definition（含 patterns）。"""
    props = elem.get("properties") or elem.get("Properties") or {}
    if not isinstance(props, dict):
        props = {}
    name = str(props.get("Name", props.get("name", "")) or "").strip()
    aid = str(props.get("AutomationId", props.get("automationId", "")) or "").strip()
    ctype = _strip_control_type_id(props.get("ControlType", props.get("controlType", "")))
    cls = str(props.get("ClassName", props.get("className", "")) or "").strip()
    pid = str(props.get("ProcessId", props.get("processId", "")) or "").strip()
    fw = str(props.get("FrameworkId", props.get("frameworkId", "")) or framework_id).strip()
    rect_str, box = _rect_from_props(props)
    patterns = _normalize_patterns(elem.get("patterns") or elem.get("Patterns"))
    lct = str(props.get("LocalizedControlType", props.get("localizedControlType", "")) or "").strip()
    ikf = str(props.get("IsKeyboardFocusable", props.get("isKeyboardFocusable", "")) or "").strip()
    hkf = str(props.get("HasKeyboardFocus", props.get("hasKeyboardFocus", "")) or "").strip()

    # locator 推荐（与 uiapeek_client 对齐）
    method, value, score, reason = "", "", 0, "no_stable_locator"
    if aid and ctype:
        method, value, score, reason = "automation_id,control_type", "{},{}".format(aid, ctype), 100, "automation_id + control_type"
    elif aid:
        method, value, score, reason = "automation_id", aid, 92, "automation_id"
    elif name and ctype:
        method, value, score, reason = "name,control_type", "{},{}".format(name, ctype), 88, "name + control_type"
    elif name:
        method, value, score, reason = "name", name, 78, "name"

    aux = ["ProcessId={}".format(pid)] if pid else []
    if cls:
        aux.append("ClassName={}".format(cls))
    if fw:
        aux.append("FrameworkId={}".format(fw))

    ui_path = _strip_control_type_in_path(elem.get("uiPath", ""))
    parent_path = _strip_control_type_in_path(elem.get("parentPath", ""))

    inspect_data = {
        "name": name, "controlType": ctype, "localizedControlType": lct,
        "boundingRectangle": rect_str, "isEnabled": str(props.get("IsEnabled", "")),
        "isVisible": str(props.get("IsVisible", "")), "isOffscreen": str(props.get("IsOffscreen", "")),
        "isKeyboardFocusable": ikf, "hasKeyboardFocus": hkf,
        "processId": pid, "runtimeId": str(props.get("RuntimeId", "")),
        "frameworkId": fw, "className": cls, "automationId": aid,
        "nativeWindowHandle": str(props.get("NativeWindowHandle", props.get("HWND", ""))),
        "helpText": str(props.get("HelpText", "")), "providerDescription": "",
        "patterns": patterns, "source": "axe-windows",
    }
    # 生成唯一 id：自动化流程的定位 key，控件库消费方通过它去重。
    # 兜底策略（对齐 normalize.generate_control_id）：aid 为空时按
    #   className(+controlType) 组合，避免自定义 WPF 控件（常共用空/相同
    #   className 如 "Control"）仅靠 className 产生大量撞名 id。
    if aid:
        ctl_id = aid
    elif cls and ctype:
        ctl_id = "{}_{}".format(cls, ctype)
    elif cls:
        ctl_id = cls
    elif ctype:
        ctl_id = ctype
    else:
        ctl_id = "Control"
    # 生成可读名称：优先 automationId 拆分，fallback 到 className
    if aid:
        ctl_name = aid
    elif name:
        ctl_name = name
    else:
        ctl_name = cls or "Unnamed"

    return {
        "id": ctl_id,
        "name": ctl_name, "windowTitle": window_title, "frameworkId": fw,
        "controlType": ctype, "className": cls,
        "targetMethod": method, "targetValue": value,
        "locatorScore": score, "locatorReason": reason,
        "auxChecks": aux, "inspectData": inspect_data, "boundingBox": box,
        "uiPath": ui_path, "parentPath": parent_path
    }


def bridge_json_to_payload(doc):
    """把 bridge 输出的整份 JSON 转成 WT control_map 兼容 payload。"""
    doc = doc if isinstance(doc, dict) else {}
    pid = str(doc.get("processId", doc.get("ProcessId", "")) or "").strip()
    window_scans = doc.get("windowScans") or doc.get("WindowScans") or []
    control_defs = []
    window_title = ""
    a11ytest_files = []
    rule_count = 0
    for ws in window_scans:
        if isinstance(ws, dict):
            of = ws.get("outputFile") or ws.get("OutputFile")
            if isinstance(of, dict):
                of = of.get("path") or of.get("Path")
            if of:
                a11ytest_files.append(of)
            elements = ws.get("elements") or ws.get("Elements") or []
            for elem in elements:
                cdef = bridge_element_to_control_definition(elem, window_title=window_title)
                rule = str(elem.get("rule", elem.get("Rule", "")) or "").strip()
                if rule:
                    cdef["inspectData"]["axeRule"] = rule
                    rule_count += 1
                control_defs.append(cdef)

    # 去重：同一 automationId 可能在不同容器下重复出现；
    # 给重复 id 追加 _N 后缀，确保每个控件唯一。
    seen_ids = set()
    for i, cd in enumerate(control_defs):
        raw_id = cd.get("id", "")
        uid = raw_id
        n = 2
        while uid in seen_ids:
            uid = "{}_{}".format(raw_id, n)
            n += 1
        seen_ids.add(uid)
        if uid != raw_id:
            cd["id"] = uid

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": "axewindows",
            "source": "axe-windows-bridge",
            "processId": pid,
            "totalControls": len(control_defs),
            "ruleResults": rule_count,
            "a11ytestFiles": a11ytest_files,
            "note": "bridge 模式：元素来自 axe-windows 规则扫描的违规元素集合，含 Patterns。",
        },
        "targetWindow": {"title": window_title or "AxeWindowsScan", "processId": pid, "frameworkId": ""},
        "controlDefinitions": control_defs,
    }


# ---------------------------------------------------------------------------
# CLI 模式
# ---------------------------------------------------------------------------
def scan_process(pid, hwnd=None, output_dir=None, cli_exe=None, verbosity="verbose", delay=0,
                 save_a11ytest=True, timeout=120):
    """调用 AxeWindowsCLI 扫描进程，返回 control_map 兼容 payload。

    CLI 不输出结构化元素树，本函数：
      - 生成 .a11ytest 文件（output_dir，可用 AccessibilityInsights 打开）；
      - 捕获 stdout（scanMeta.rawStdout 截断）；
      - 尽力正则解析元素行（可能为空）。
    完整元素/Patterns 请用 bridge 模式（scan_via_bridge）。
    """
    cli_exe = cli_exe or find_cli_exe()
    if not cli_exe:
        raise RuntimeError(
            "未找到 AxeWindowsCLI.exe。请安装 MSI（见 vendor/axe-windows/src/CLI/README.MD），"
            "或改用 bridge 模式（--bridge）。"
        )
    output_dir = output_dir or os.path.join(CONTROL_MAP_DIR, "_axewindows_a11ytest")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [cli_exe, "--processid", str(int(pid)), "--outputdirectory", output_dir,
           "--verbosity", verbosity]
    if hwnd:
        cmd.extend(["--scanrootwindowhandle", str(int(hwnd))])
    if save_a11ytest:
        cmd.append("--alwayssavetestfile")
    if delay:
        cmd.extend(["--delayinseconds", str(int(delay))])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    a11y_files = []
    for dirpath, _d, files in os.walk(output_dir):
        for f in files:
            if f.lower().endswith(".a11ytest"):
                a11y_files.append(os.path.join(dirpath, f))

    control_defs = _parse_cli_verbose(stdout)

    payload = {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": "axewindows",
            "source": "axe-windows-cli",
            "processId": str(pid),
            "hwnd": str(hwnd) if hwnd else "",
            "cliExitCode": proc.returncode,
            "totalControls": len(control_defs),
            "a11ytestFiles": a11y_files,
            "rawStdout": stdout[:4000],
            "rawStderr": stderr[:2000],
            "note": "CLI 模式：仅生成 a11ytest + 控制台摘要，元素信息有限。完整 Patterns 用 bridge 模式。",
        },
        "targetWindow": {"title": "AxeWindowsCLI", "processId": str(pid), "frameworkId": ""},
        "controlDefinitions": control_defs,
    }
    return payload


_ELE_RE = re.compile(
    r"(?:ControlType|Control Type)\s*[:=]\s*(?P<ctype>[^,\n]+?)(?:[,]\s*Name\s*[:=]\s*(?P<name>[^,\n]+?))?"
    r"(?:[,]\s*AutomationId\s*[:=]\s*(?P<aid>[^,\n]+?))?(?:[,]\s*ClassName\s*[:=]\s*(?P<cls>[^,\n]+?))?",
    re.I,
)


def _parse_cli_verbose(stdout):
    """尽力从 CLI verbose stdout 解析元素行，返回 control_definitions（可能为空）。"""
    defs = []
    seen = set()
    for m in _ELE_RE.finditer(stdout or ""):
        ctype = (m.group("ctype") or "").strip()
        name = (m.group("name") or "").strip().strip("'\"")
        aid = (m.group("aid") or "").strip().strip("'\"")
        cls = (m.group("cls") or "").strip().strip("'\"")
        if not ctype and not name and not aid:
            continue
        key = (aid.lower(), name.lower(), ctype.lower())
        if key in seen:
            continue
        seen.add(key)
        defs.append(bridge_element_to_control_definition(
            {"properties": {"Name": name, "ControlType": ctype, "AutomationId": aid, "ClassName": cls}}))
    return defs


# ---------------------------------------------------------------------------
# Bridge 模式
# ---------------------------------------------------------------------------
def _is_admin():
    """当前进程是否以管理员（高完整性）运行。"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_elevated_capture(cmd, timeout=180):
    """以管理员提权运行 cmd（弹 UAC），并把 stdout/stderr 重定向到临时文件后读取。

    返回 (stdout, stderr)。依赖 PowerShell 的 Start-Process -Verb RunAs。
    """
    import tempfile
    import time as _time

    out = os.path.join(tempfile.gettempdir(), "axebridge_out.txt")
    err = os.path.join(tempfile.gettempdir(), "axebridge_err.txt")
    for f in (out, err):
        try:
            os.remove(f)
        except OSError:
            pass

    args_literal = ", ".join("'{}'".format(a.replace("'", "''")) for a in cmd[1:])
    ps = (
        "Start-Process -FilePath '{exe}' -ArgumentList @({args}) "
        "-Verb RunAs -Wait -WindowStyle Hidden "
        "-RedirectStandardOutput '{out}' -RedirectStandardError '{err}'"
    ).format(exe=cmd[0].replace("'", "''"), args=args_literal, out=out, err=err)

    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, timeout=max(timeout, 30))
    # UAC/进程可能略晚落盘，给一点余量
    _time.sleep(0.5)
    stdout, stderr = "", ""
    try:
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            stdout = fh.read()
    except OSError:
        pass
    try:
        with open(err, "r", encoding="utf-8", errors="replace") as fh:
            stderr = fh.read()
    except OSError:
        pass
    return stdout, stderr


def scan_via_bridge(pid, bridge_exe=None, hwnd=None, timeout=180):
    """调用 C# bridge（输出 JSON），返回 control_map 兼容 payload（含 Patterns）。

    bridge 源码在 axewindows_bridge/，需 .NET SDK：``dotnet run --project axewindows_bridge -- <pid>``。
    若 bridge 未编译，本函数会尝试 ``dotnet run`` 自动编译运行（需 dotnet 在 PATH）。

    注意：Axe.Windows 跨完整性枚举 UI 树可能需要管理员权限。    若当前非管理员，会自动以 runas 提权运行（弹 UAC），UIA 跨完整性枚举窗口
    必须管理员，否则目标进程窗口枚举为空导致扫描失败。
    """
    bridge_exe = bridge_exe or find_bridge_exe()
    cmd = None
    if bridge_exe:
        cmd = [bridge_exe, str(int(pid))]
        if hwnd:
            cmd.append(str(int(hwnd)))
    else:
        # 尝试 dotnet run（需 dotnet SDK）
        from shutil import which
        dotnet = which("dotnet")
        if not dotnet:
            raise RuntimeError(
                "未找到 AxeBridge.exe，且 dotnet SDK 未安装。请安装 .NET SDK 后运行："
                "dotnet run --project {} -- {}".format(BRIDGE_DIR, pid)
            )
        cmd = [dotnet, "run", "--project", BRIDGE_DIR, "--", str(int(pid))]
        if hwnd:
            cmd.append(str(int(hwnd)))

    if _is_admin():
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
        stdout, stderr, rc = proc.stdout or "", proc.stderr or "", proc.returncode
    else:
        # 非管理员：自动以 runas 提权运行（UIA 跨完整性枚举窗口必须管理员，
        # 否则目标进程窗口枚举为空导致扫描失败）。
        stdout, stderr = _run_elevated_capture(cmd, timeout)
        rc = 1 if not stdout.strip() else 0

    if rc != 0 or not stdout.strip():
        # 尝试从 stdout/stderr 解析 bridge 的友好 JSON 报错
        detail = stderr.strip()[:1500]
        try:
            err_doc = json.loads(stdout.strip() or stderr.strip())
            if isinstance(err_doc, dict) and "error" in err_doc:
                detail = "{}: {}\n{}".format(
                    err_doc.get("error", ""),
                    err_doc.get("message", ""),
                    err_doc.get("suggestion", ""),
                )
        except (ValueError, AttributeError):
            pass
        if not _is_admin():
            detail += "\n\n（已尝试自动提权，但当前仍非管理员——UAC 可能被拒绝。请手动以管理员身份运行总控台，或允许 UAC 提权。）"
        raise RuntimeError("AxeBridge 扫描失败（退出码 {}）：{}".format(rc, detail))

    try:
        doc = json.loads(stdout)
    except ValueError:
        raise RuntimeError("AxeBridge 输出非 JSON：{}".format(stdout[:1000]))

    # bridge 友好报错：返回 {"error":..., "suggestion":...}
    if isinstance(doc, dict) and "error" in doc:
        msg = doc.get("message", doc["error"])
        sug = doc.get("suggestion", "")
        raise RuntimeError("AxeBridge 扫描失败（{}）：{}\n建议：{}".format(
            doc["error"], msg, sug))

    payload = bridge_json_to_payload(doc)
    payload["scanMeta"]["bridgeCmd"] = " ".join(str(c) for c in cmd)
    payload["scanMeta"]["isAdmin"] = _is_admin()
    payload["scanMeta"]["elevated"] = (not _is_admin())
    return payload


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def _slugify(text, fallback="window"):
    text = str(text or "").strip()
    if not text:
        return fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


def save_payload(payload, control_map_dir=CONTROL_MAP_DIR):
    if not payload:
        return None
    os.makedirs(control_map_dir, exist_ok=True)
    pid = (payload.get("scanMeta") or {}).get("processId", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = "{}_pid{}_axewindows_control_map.json".format(ts, _slugify(pid, "proc"))
    fp = os.path.join(control_map_dir, fname)
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return fp


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def _main():
    import argparse

    ap = argparse.ArgumentParser(description="Axe.Windows 控件采集适配器")
    ap.add_argument("--pid", type=int, help="目标进程 ID")
    ap.add_argument("--hwnd", type=int, help="扫描根窗口句柄（仅 CLI 模式）")
    ap.add_argument("--bridge", action="store_true", help="用 C# bridge（输出 JSON + Patterns，需 dotnet）")
    ap.add_argument("--cli-exe", help="指定 AxeWindowsCLI.exe 路径")
    ap.add_argument("--output-dir", help="a11ytest 输出目录（CLI 模式）")
    ap.add_argument("--delay", type=int, default=0, help="扫描前延迟秒数（CLI，便于捕获菜单）")
    ap.add_argument("--no-save", action="store_true", help="不落盘，仅打印")
    ap.add_argument("--find-cli", action="store_true", help="仅查找 AxeWindowsCLI.exe 路径")
    args = ap.parse_args()

    if args.find_cli:
        exe = find_cli_exe()
        bridge = find_bridge_exe()
        print("AxeWindowsCLI.exe: {}".format(exe or "（未找到，请装 MSI）"))
        print("AxeBridge.exe:     {}".format(bridge or "（未编译，见 axewindows_bridge/）"))
        return 0

    if not args.pid:
        ap.print_help()
        return 2

    if args.bridge:
        payload = scan_via_bridge(args.pid, hwnd=args.hwnd)
    else:
        payload = scan_process(args.pid, hwnd=args.hwnd, output_dir=args.output_dir,
                               cli_exe=args.cli_exe, delay=args.delay)

    meta = payload.get("scanMeta", {})
    print("来源: {} | 进程: {} | 元素数: {}".format(
        meta.get("source"), meta.get("processId"), meta.get("totalControls", 0)))
    a11y = meta.get("a11ytestFiles") or []
    if a11y:
        print("a11ytest 文件（可用 AccessibilityInsights 打开）:")
        for f in a11y:
            print("  " + f)
    for c in payload.get("controlDefinitions", []):
        pats = c.get("inspectData", {}).get("patterns", [])
        pats_str = (" patterns={}".format(pats)) if pats else ""
        print("  {:<16} aid={!r:<28} name={!r}{}".format(
            c.get("controlType"), c.get("inspectData", {}).get("automationId"), c.get("name"), pats_str))

    fp = None
    if not args.no_save:
        fp = save_payload(payload)
        if fp:
            print("已保存: {}".format(fp))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
