# encoding: utf-8
"""MUP 冷启动登录自动化（UIA）。

用途：MUPSmartClient 冷启动后停在自绘登录页（用户名/密码/登录 都是 TextBlock），
pywinauto 的 UIA 树里没有 Edit 控件；本模块用"标签坐标 -> 点击 -> 全选清空 -> 键入"完成登录。

安全：密码只从环境变量 MUP_PASSWORD 读，不落盘、不打印、不写 user.config。
第三步（登录后进入站点主面板）的 WT 节点定位器在 ``WT_NODE_LOCATOR`` 配置；
未填 automation id 时退回已实测的绿色弧兜底。

用法：
    set MUP_PASSWORD=...
    python mup_login_auto.py [--timeout 120]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from dataclasses import dataclass

from pywinauto import Application, mouse

# WT 节点定位器的合法类型：automation id / 控件文本 / 屏幕坐标。
WT_LOCATOR_KINDS = ("automation_id", "name", "point")


@dataclass(frozen=True)
class WtNodeLocator:
    """WT 节点（模块枢纽里的绿弧 + 风机图标）的定位描述。

    kind:
      - "automation_id": value 为 UIA AutomationId，最稳，优先使用。
      - "name":          value 为控件文本（精确匹配）。
      - "point":         value 为 (x, y) 屏幕像素坐标，仅在拿不到 id 时使用。
    """

    kind: str
    value: str | tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.kind not in WT_LOCATOR_KINDS:
            raise ValueError("未知 WT 定位类型: " + repr(self.kind))
        if self.kind == "point" and self.value not in (None, ""):
            if not (isinstance(self.value, (tuple, list)) and len(self.value) == 2):
                raise ValueError("point 定位需要 (x, y)")


# === 唯一配置点：拿到 WT 节点 AutomationId 后只改这一行的 value ===
# 已确认锚点：主面板模块枢纽的「WT」卡片 =
#   automationId=MainPage_Button_Microscale, controlName='Open Module 按钮',
#   uiPath=Window_Main > MUPHomePageMainView > OpenModule,
#   子节点含 'WT' / 'WT微尺度模拟' TextBlock。
# 证据：flow_packages/flow_definition_创建一个新建模.json step_1「点击-WT」
#   （create_model 流程实测可打开模块）。value 为空时退回绿色弧兜底。
WT_NODE_LOCATOR = WtNodeLocator(kind="automation_id", value="MainPage_Button_Microscale")

SPECIAL = {
    "{": "{{}", "}": "{}}", "+": "{+}", "^": "{^}", "%": "{%}",
    "~": "{~}", "(": "{(}", ")": "{)}", "[": "{[}", "]": "{]}",
}


def _escape(text: str) -> str:
    return "".join(SPECIAL.get(ch, ch) for ch in text)


def _mup_pids() -> list[int]:
    try:
        out = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq MUPSmartClient.exe" /FO CSV /NH',
            shell=True,
        ).decode("gbk", "ignore")
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) > 1 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def _rect_by_text(w, text: str):
    for d in w.descendants():
        try:
            if d.window_text().strip() == text:
                return d.rectangle()
        except Exception:
            pass
    return None


def _rect_by_automation_id(w, automation_id: str):
    for d in w.descendants():
        try:
            if (d.element_info.automation_id or "") == automation_id:
                r = d.rectangle()
                if r.right > r.left and r.bottom > r.top:
                    return r
        except Exception:
            pass
    return None


def _center(rect) -> tuple[int, int]:
    return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)


def resolve_wt_target(w, locator: WtNodeLocator | None = None):
    """把 WT 定位器解析成屏幕点击点；解析不到返回 None。

    纯解析、不点击，便于在不启动 GUI 的单测里验证接线。
    """
    locator = WT_NODE_LOCATOR if locator is None else locator
    if locator.value in (None, ""):
        return None
    if locator.kind == "point":
        return (int(locator.value[0]), int(locator.value[1]))
    if locator.kind == "automation_id":
        rect = _rect_by_automation_id(w, str(locator.value))
    else:
        rect = _rect_by_text(w, str(locator.value))
    return _center(rect) if rect is not None else None


def _dashboard_ready(w) -> bool:
    for d in w.descendants():
        try:
            if d.element_info.automation_id == "DashboardView_Button_Refresh":
                return True
        except Exception:
            pass
    return False


def _green_node_center():
    """在屏幕中找 WT 绿色弧节点（绿色像素最大簇）的质心。"""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        w, h = img.size
        px = img.load()
        pts = []
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                r, g, b = px[x, y][:3]
                if g > 110 and g > r + 40 and g > b + 40:
                    pts.append((x, y))
        if len(pts) < 200:
            return None
        # 取最大簇：简单按 100px 网格分箱
        from collections import Counter
        bins = Counter(((x // 100) * 100, (y // 100) * 100) for x, y in pts)
        (bx, by), _n = bins.most_common(1)[0]
        cluster = [(x, y) for x, y in pts if bx <= x < bx + 100 and by <= y < by + 100]
        if not cluster:
            return None
        return (sum(p[0] for p in cluster) // len(cluster),
                sum(p[1] for p in cluster) // len(cluster))
    except Exception:
        return None


def _click_and_wait_dashboard(app, point, timeout: float) -> bool:
    try:
        w = app.windows()[0]
        w.set_focus()
        mouse.click(button="left", coords=point)
    except Exception:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _dashboard_ready(app.windows()[0]):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def enter_dashboard(app, timeout: int = 60,
                    wt_locator: WtNodeLocator | None = None) -> bool:
    """第三步：登录后从模块枢纽进入站点主面板。

    定位顺序：显式 WT 节点定位器 -> 绿色弧像素兜底 -> 失败。
    不再点 Central（实测进入的是项目列表，不是站点主面板）。
    """
    w = app.windows()[0]
    if _dashboard_ready(w):
        return True

    locator = WT_NODE_LOCATOR if wt_locator is None else wt_locator
    target = resolve_wt_target(w, locator)
    if target is not None:
        if _click_and_wait_dashboard(app, target, min(20, timeout)):
            return True
        print("[WARN] WT 节点定位命中但未进入主面板: "
              + locator.kind + "=" + repr(locator.value))
    elif locator.value in (None, ""):
        print("[INFO] WT_NODE_LOCATOR 未配置（占位空值），使用绿色弧兜底")
    else:
        print("[WARN] WT 节点定位未命中: " + locator.kind + "=" + repr(locator.value))

    center = _green_node_center()
    if center is not None:
        if _click_and_wait_dashboard(app, center, min(20, timeout)):
            return True
    return False


def login(timeout: int = 120, password: str | None = None, pid: int | None = None,
          dashboard: bool = True,
          wt_locator: WtNodeLocator | None = None) -> bool:
    password = password if password is not None else os.environ.get("MUP_PASSWORD", "")
    if not password:
        raise RuntimeError("MUP_PASSWORD 未设置")

    deadline = time.time() + timeout
    pids = [pid] if pid else _mup_pids()
    while not pids and time.time() < deadline:
        time.sleep(2)
        pids = _mup_pids()
    if not pids:
        raise RuntimeError("MUPSmartClient 未启动")

    app = Application(backend="uia").connect(process=pids[0])
    while time.time() < deadline:
        try:
            w = app.windows()[0]
        except Exception:
            time.sleep(2)
            continue
        user = _rect_by_text(w, "用户名")
        pwd = _rect_by_text(w, "密码")
        btn = _rect_by_text(w, "登录")
        if not (user and pwd and btn):
            # 已经登录（登录页文本消失）
            return True
        w.set_focus()
        time.sleep(0.4)
        # 用户名：点击 -> 全选 -> 删除 -> 输入
        mouse.click(button="left", coords=((user.left + user.right) // 2, user.bottom - 4))
        time.sleep(0.3)
        w.type_keys("^a", pause=0.05)
        w.type_keys("{DELETE}", pause=0.05)
        w.type_keys("admin", with_spaces=True, pause=0.03)
        time.sleep(0.3)
        # 密码：同样先清空
        mouse.click(button="left", coords=((pwd.left + pwd.right) // 2, pwd.bottom - 4))
        time.sleep(0.3)
        w.type_keys("^a", pause=0.05)
        w.type_keys("{DELETE}", pause=0.05)
        w.type_keys(_escape(password), with_spaces=True, pause=0.03)
        time.sleep(0.3)
        mouse.click(button="left", coords=((btn.left + btn.right) // 2, (btn.top + btn.bottom) // 2))
        time.sleep(6)
        # 登录页消失即成功
        if _rect_by_text(app.windows()[0], "用户名") is None:
            if dashboard:
                return enter_dashboard(app, timeout=max(30, timeout // 2),
                                       wt_locator=wt_locator)
            return True
        time.sleep(2)
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MUP 冷启动登录自动化")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--pid", type=int)
    p.add_argument("--no-dashboard", action="store_true")
    args = p.parse_args(argv)
    try:
        ok = login(timeout=args.timeout, pid=args.pid, dashboard=not args.no_dashboard)
    except Exception as exc:
        print("[FAIL] " + str(exc))
        return 2
    print("[OK] logged in" if ok else "[FAIL] login not completed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
