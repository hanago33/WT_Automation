# encoding: utf-8
"""检验定位探针子进程：在隔离进程中执行控件定位搜索。

背景：检验定位若在采集器进程内直接跑 UIA 遍历，遇到失效的 UIA COM 元素指针
会触发原生堆损坏（STATUS_HEAP_CORRUPTION, 0xc0000374），Python 的 try/except
拦不住，进程直接崩溃、采集器主窗口随之消失。
本探针把定位搜索放到独立 subprocess：原生崩溃只终止探针子进程，
采集器主窗口不受影响；主进程通过结果文件判断成功/失败/崩溃。

用法：
    python control_locator_probe.py <control.json> <output.json>
退出码 0 = 完整执行完毕（status 在 output.json 中：found / not_found / error）；
进程被系统终止（非零退出且无有效 output.json）= 定位期间发生原生崩溃。
设计约束：遍历残留的 UIA COM 包装不可在本进程内析构（见 search_control 注释），
结果落盘后立即 os._exit(0) 硬退出，跳过解释器终结。
"""

import gc
import json
import os
import sys
import time

# 与采集器一致：MTA 模式必须早于 pywinauto/comtypes 导入，否则 UIA 跨进程 COM 调用极慢
sys.coinit_flags = 0

import wt_flow_locator as flow_locator  # noqa: E402

# ── 遍历预算（方案2：限制无界 UIA 遍历，降低触发原生崩溃的概率）──
MAX_WINDOWS_SCAN = 6              # 最多扫描的窗口数量（迭代序按评级降序，先扫命中候选）
MAX_DESCENDANTS_ELEMENTS = 6000   # 阶段3 单窗口 descendants 元素上限
DESCENDANTS_BUDGET_SECONDS = 8.0  # 阶段3 总时间预算
RAW_VIEW_BUDGET_SECONDS = 8.0     # 阶段2 Raw View 兜底预算
# 与流程执行器 find_flow_control 一致：出现 >=100 分的高置信候选立即返回，
# 不再继续评分后续候选（MUP 上单次评分可达秒级~数十秒，是检验定位耗时大头）
EARLY_EXIT_SCORE = 100


def _bounded_descendants(window, expected_type, deadline, max_elements):
    """对 window.descendants 做元素数 + 时间预算的受限遍历，返回受限候选列表。

    pywinauto 的 descendants 会整体物化列表，无法增量中断；这里在拿到列表后
    立即截断到 max_elements 且不超过 deadline，避免无界遍历拖垮整个探针。
    """
    results = []
    try:
        if expected_type:
            raw = window.descendants(control_type=expected_type)
        else:
            raw = window.descendants()
    except Exception:
        return results
    for candidate in raw:
        if len(results) >= max_elements or time.time() >= deadline:
            break
        results.append(candidate)
    return results


def search_control(control, budgets=None):
    """在隔离进程内执行与采集器/流程执行器同源的分阶段定位搜索，返回结果 dict。

    阶段1 快速候选 → 阶段2 Raw View 兜底 → 阶段3 descendants 受限兜底。
    各阶段均受预算约束；任意阶段命中即返回。出现 >=EARLY_EXIT_SCORE 的高置信
    候选时立即返回（与执行器 find_flow_control 的 best_score>=100 早退同语义），
    此时 match_count 只统计已评分部分，结果带 early_exit=True 标记。

    budgets 为可选覆盖参数（键同模块常量），仅供测试注入小预算/禁用阶段。
    """
    budgets = dict(budgets or {})
    max_windows = int(budgets.get("max_windows", MAX_WINDOWS_SCAN))
    max_elements = int(budgets.get("max_descendants_elements", MAX_DESCENDANTS_ELEMENTS))
    desc_budget = float(budgets.get("descendants_budget_seconds", DESCENDANTS_BUDGET_SECONDS))
    raw_budget = float(budgets.get("raw_view_budget_seconds", RAW_VIEW_BUDGET_SECONDS))

    start = time.time()
    result = {
        "status": "not_found",
        "elapsed": 0.0,
        "match_count": 0,
        "score": 0,
        "rect": None,
        "center": None,
        "snapshot": {},
        "targetMethod": str(control.get("targetMethod", "") or "").strip(),
        "targetValue": str(control.get("targetValue", "") or "").strip(),
        "error": "",
    }

    try:
        # 与 find_flow_control 同款：UIA 遍历期间禁用 GC，防止 comtypes 释放失效 COM
        # 指针触发原生堆损坏(0xc0000374)。GC 保持禁用直到进程退出、也不做
        # CoUninitialize：遍历残留的 pywinauto/comtypes 包装持有悬空接口指针，
        # 本进程内任何时点的批量析构（重新启用 GC、解释器终结均会）都会 Release
        # 失效指针再次堆损坏——清理交给 main() 落盘后的 os._exit 硬退出，
        # 由操作系统回收整个进程资源。
        gc.disable()
        import pythoncom
        pythoncom.CoInitialize()

        windows = list(flow_locator.iter_flow_search_windows(
            {},
            window_title_hint=str(control.get("windowTitle", "")).strip(),
            control_definition=control,
        ))
        windows = windows[:max_windows]
        if not windows:
            result["error"] = "未找到目标窗口：" + (control.get("windowTitle") or "当前应用窗口")
            return result

        matched = []
        seen = set()
        best_score = 0

        def _early_hit():
            return best_score >= EARLY_EXIT_SCORE

        def _match(candidate_iter, _window):
            nonlocal best_score
            for candidate in candidate_iter:
                handle = flow_locator.get_wrapper_handle(candidate) or id(candidate)
                if handle in seen:
                    continue
                seen.add(handle)
                score = flow_locator.score_control_match(candidate, control)
                if score > 0:
                    matched.append((score, candidate, _window))
                    if score > best_score:
                        best_score = score
                    if _early_hit():
                        return

        # 阶段1：快速候选（窗口按评级降序，通常前几个窗口即命中）
        for window in windows:
            if time.time() - start > desc_budget:
                break
            _match(flow_locator.iter_fast_locator_candidates(window, control), window)
            if _early_hit():
                break

        # 阶段2：RawView 兜底（带预算）
        if not _early_hit() and not matched and control.get("inspectData", {}):
            try:
                from wt_flow_locator import (
                    control_definition_expects_raw_view,
                    iter_raw_view_fallback_candidates,
                )
                if control_definition_expects_raw_view(control):
                    for window in windows:
                        if time.time() - start > raw_budget:
                            break
                        try:
                            _match(iter_raw_view_fallback_candidates(
                                window, control, budget_seconds=raw_budget
                            ), window)
                        except Exception:
                            pass
                        if _early_hit():
                            break
            except Exception:
                pass

        # 阶段3：descendants 受限兜底（元素数 + 时间预算，只扫候选窗口）
        if not _early_hit() and not matched:
            expected_type = str(
                control.get("controlType", "")
                or control.get("inspectData", {}).get("controlType", "")
            ).strip()
            desc_deadline = time.time() + desc_budget
            for window in windows:
                if time.time() >= desc_deadline:
                    break
                _match(_bounded_descendants(window, expected_type, desc_deadline, max_elements), window)
                if _early_hit():
                    break

        elapsed = time.time() - start
        result["elapsed"] = round(elapsed, 1)
        result["early_exit"] = bool(_early_hit())
        if not matched:
            result["error"] = "未找到匹配控件"
            return result

        matched.sort(key=lambda e: e[0], reverse=True)
        best_score, found, _win = matched[0]
        result["score"] = int(best_score)
        result["match_count"] = len(matched)
        try:
            rect = found.rectangle()
            result["rect"] = {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            }
        except Exception:
            result["rect"] = None
        if result["rect"]:
            result["center"] = {
                "x": (result["rect"]["left"] + result["rect"]["right"]) // 2,
                "y": (result["rect"]["top"] + result["rect"]["bottom"]) // 2,
            }
        try:
            result["snapshot"] = flow_locator.get_wrapper_debug_snapshot(found)
        except Exception:
            result["snapshot"] = {}
        result["status"] = "found"
        return result
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["elapsed"] = round(time.time() - start, 1)
        return result


def main():
    if len(sys.argv) < 3:
        print("usage: python control_locator_probe.py <control.json> <output.json>", file=sys.stderr)
        return 2
    control_path, output_path = sys.argv[1], sys.argv[2]
    try:
        with open(control_path, "r", encoding="utf-8") as f:
            control = json.load(f)
    except Exception as exc:
        print("读取控件定义失败: {}".format(exc), file=sys.stderr)
        return 2

    result = search_control(control)

    # 先写临时文件再原子替换，避免主进程读到半写状态
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp_path, output_path)
    # 结果已原子落盘：硬退出跳过解释器终结。此时进程内仍残留遍历产生的失效 COM
    # 包装，正常退出路径的模块清理/GC 会析构它们并触发 0xc0000374（实测必现），
    # 把退出码变成崩溃码、父进程误报"定位进程异常退出"。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
