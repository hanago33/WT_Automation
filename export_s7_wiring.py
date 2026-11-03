# encoding: utf-8
"""波-3 接线编排：综合完成门 → Vault 直取 → 残余 GUI 流。

职责边界（红线）：
  · 直取实现、产物匹配、manifest v2 仍归 `export_s7.py` / `mup_vault_export.py`；
    本模块**只调用**它们，不复制、不重写其匹配/拷贝逻辑。
  · 本模块只新增接线所需的纯编排：paramTable 行 → 实际卡号集合、cancelled 排除、
    直取 + 残余 GUI 流生成。
  · 残余 GUI 按 2026-11-02 zcode 真实观测（U-9）收窄：`.wri/.wrp/.wrt/.wrtd/
    .dat/.wrm` 可由 Vault 直取；`matrix_{n}.txt` 与 Excel/IEC 仍必须 GUI。
    因此默认在 export_s7 的 36 步 Excel/IEC 流后**保留 10 步矩阵块**（46 步 / 3 case），
    不把“直取 .wri”说成“已交付 matrix.txt”。

卡号口径（U-1/U-6/U-10）：
  · 复制卡由 MUP 自动命名 `综合{max_n+1}`，绝不能按“副本”关键词匹配；
  · `n` 是位置量，会发生漂移；本模块在发送时用 paramTable 行 + 基线 max 落账
    实际卡号集合，gate 与直取共用同一份集合；
  · 已取消（WROutputs.ErrorCode 含 Cancelled）的卡没有产物，必须从期望集合剔除
    （DEMO 综合1 即此场景）。

纯 stdlib；不启动 MUP、不触 HASP/licence、不写 Vault。
"""
import copy
import json
import os
import re
import shutil
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import export_s7
import mup_vault_export


# ---------------------------------------------------------------------------
# 参数表读取与“实际卡号集合”落账
# ---------------------------------------------------------------------------
def _load_json(path: str) -> Tuple[Optional[Any], str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), ""
    except Exception as exc:  # noqa: BLE001 —— 读取失败由调用方显式报因
        return None, str(exc)


def read_param_rows(flow_path: str) -> List[Dict[str, Any]]:
    """从综合流程的 `paramTable` 读取参数行（相对路径按流程目录解析）。

    解析委托 `mup_vault_export.parse_param_table`（JSON/CSV/Excel、全角分隔符、
    中英文表头统一 owner）。失败返回 []，由调用方决定 fail-closed/降级。
    """
    payload, _err = _load_json(flow_path)
    if not isinstance(payload, dict):
        return []
    param = str(payload.get("paramTable") or "").strip()
    if not param:
        return []
    if not os.path.isabs(param):
        param = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(flow_path)), param))
    if not os.path.isfile(param):
        return []
    return list(mup_vault_export.parse_param_table(param) or [])


def row_card_number(row: Dict[str, Any]) -> Optional[int]:
    """行显式卡号：`synthesisName=综合N` → N；空/无数字 → None（按自增推导）。"""
    name = str((row or {}).get("synthesisName") or "").strip()
    match = re.search(r"(\d+)", name)
    return int(match.group(1)) if (name and match) else None


def actual_card_numbers(rows: Sequence[Dict[str, Any]], baseline_max: int = 0,
                        excluded_ns: Iterable[int] = ()) -> List[int]:
    """由 paramTable 行序 + 基线 max 推导“实际创建卡号集合”。

    规则：
      · 行有显式 `综合N` → 采用 N（create 行由流程键入该名；fixture 用它标注真实卡）；
      · 行为空（copy/copyfull）→ MUP 自动命名 `综合{seq+1}`，seq 从基线 max 起递增；
      · `excluded_ns`（已取消卡）从结果剔除，但 seq 仍前进（MUP 序号单调）。
    """
    seq = int(baseline_max or 0)
    excluded = {int(n) for n in (excluded_ns or ())}
    out: List[int] = []
    for row in rows or ():
        explicit = row_card_number(row)
        if explicit is not None:
            seq = max(seq, explicit)
            number = explicit
        else:
            seq += 1
            number = seq
        if number not in excluded:
            out.append(number)
    return sorted(set(out))


def _resolved_card_name(row: Dict[str, Any], number: int) -> str:
    """实际卡名：create 行沿用键入名；copy/copyfull/空名按 MUP 自动命名 综合N。"""
    existing = str((row or {}).get("synthesisName") or "").strip()
    mode = str((row or {}).get("stepMode") or "").strip().lower()
    if existing and mode not in ("copy", "copyfull"):
        return existing
    return "综合%d" % number


def annotate_rows_for_expected(rows: Sequence[Dict[str, Any]],
                               expected_ns: Iterable[int],
                               baseline_max: int = 0,
                               created_ns: Optional[Iterable[int]] = None
                               ) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
    """把期望卡号映射回 paramTable 行，并补全 `synthesisName`/`index`。

    返回 `(selected_rows, missing_ns, simulated_ns)`：
      · selected_rows 只含 expected_ns 命中的行，name 补成 `综合N`，index=N；
      · missing_ns = expected_ns 中参数表无法映射的号（n 漂移/参数表过期）；
      · simulated_ns = 本轮实际创建号（有 created_ns 时按创建顺序映射，
        否则按 paramTable 行 + 基线 max 推导）。

    `created_ns` 是发送后 LocalDB 实际出现的卡号集合（含被取消的号）。当它与
    paramTable 行数一致时，**按行序一一映射**，不信任行内显式名字——这是
    U-6 n 漂移（例如基线已有 1..4 时新建卡实际是 5..8，而首行仍写“综合1”）
    的唯一可靠对齐方式。数目不一致时退回显式/自增推导并报 missing，fail-closed。
    """
    expected = {int(n) for n in (expected_ns or ())}
    created = None
    if created_ns is not None:
        created = [int(n) for n in created_ns]
    row_list = list(rows or ())

    if created is not None and created and len(created) == len(row_list):
        selected: List[Dict[str, Any]] = []
        for row, number in zip(row_list, created):
            if expected and number not in expected:
                continue
            item = dict(row)
            item["synthesisName"] = _resolved_card_name(item, number)
            item["index"] = number
            selected.append(item)
        missing = sorted(expected - set(created)) if expected else []
        return selected, missing, list(created)

    seq = int(baseline_max or 0)
    selected = []
    simulated: List[int] = []
    for row in row_list:
        explicit = row_card_number(row)
        if explicit is not None:
            seq = max(seq, explicit)
            number = explicit
        else:
            seq += 1
            number = seq
        simulated.append(number)
        if expected and number not in expected:
            continue
        item = dict(row)
        item["synthesisName"] = _resolved_card_name(item, number)
        item["index"] = number
        selected.append(item)
    missing = sorted(expected - set(simulated)) if expected else []
    return selected, missing, simulated


def is_cancelled_row(row: Dict[str, Any]) -> bool:
    """WROutputs.ErrorCode 含 Cancelled（不区分大小写）。"""
    return "cancel" in str((row or {}).get("error") or "").lower()


def db_expected_numbers(db_rows: Optional[Sequence[Dict[str, Any]]],
                        baseline_ns: Optional[Iterable[int]]
                        ) -> Tuple[List[int], str, List[int]]:
    """用发送前后的 LocalDB 快照判定“本次实际新建卡号”。

    返回 `(expected_ns, source, cancelled_ns)`：
      · 有新卡（n 不在基线）→ source=db-new，剔除其中已取消的；
      · 新卡全部取消 → source=db-new-all-cancelled，expected=[]（调用方 fail-closed）；
      · 没有新卡 → source=db-none，expected=[]（调用方回退 paramTable 推导）。
    `baseline_ns=None` 表示发送前 DB 查询失败，调用方不得使用本函数结果。
    """
    baseline = {int(n) for n in (baseline_ns or ())}
    rows = list(db_rows or ())
    cancelled = {int(r["n"]) for r in rows if is_cancelled_row(r)}
    new_all = [r for r in rows if int(r["n"]) not in baseline]
    if not new_all:
        return [], "db-none", sorted(cancelled)
    new_ok = sorted({int(r["n"]) for r in new_all
                     if int(r["n"]) not in cancelled})
    if not new_ok:
        return [], "db-new-all-cancelled", sorted(cancelled)
    return new_ok, "db-new", sorted(cancelled)


# ---------------------------------------------------------------------------
# 残余 GUI 流：36 步 Excel/IEC + 保留 10 步矩阵块
# ---------------------------------------------------------------------------
def _default_template_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "flow_packages", "flow_definition_导出综合计算结果.json")


def _matrix_steps(template_path: str) -> List[Dict[str, Any]]:
    """抽取 S7 模板 step_12..step_21（矩阵块），深拷贝并改 id。"""
    payload, _err = _load_json(template_path)
    if not isinstance(payload, dict):
        return []
    by_id = {str(s.get("id") or ""): s for s in payload.get("steps") or []
             if isinstance(s, dict)}
    out: List[Dict[str, Any]] = []
    for i in range(12, 22):
        src = by_id.get("step_%d" % i)
        if not isinstance(src, dict):
            continue
        step = copy.deepcopy(src)
        step["id"] = "s7_matrix_step_%d" % i
        out.append(step)
    return out


def _first_case_end_index(flow: Dict[str, Any]) -> int:
    """36 步流中第一个 case 的最后一步之后（矩阵块插入点）。"""
    insert_at = 0
    for idx, step in enumerate(flow.get("steps") or []):
        if str(step.get("id") or "").startswith("s7_case1_"):
            insert_at = idx + 1
    return insert_at


def _fix_first_case_selection(flow: Dict[str, Any]) -> None:
    """首 case 选择从“列表项 found_index=0”改为按卡名文本选中。

    S7 模板 step_1 的控件是 `WRAResults_ListItem_WRAResultTile,ListItem,0`；
    真实 DEMO 的综合1 被 Cancel 仍在列表但无产物，按索引 0 会选错卡。
    U-1 已证实卡名 `综合{N}` 稳定可读，故用 `name,control_type` 选中。
    """
    cases = list(flow.get("exportedCases") or [])
    first_case = str(cases[0] if cases else "").strip()
    if not first_case or first_case == "综合1":
        return  # 综合1 保持模板原行为，避免无谓回归
    for step in flow.get("steps") or []:
        if str(step.get("id") or "") != "s7_case1_step_1":
            continue
        hints = step.get("inspectHints")
        if isinstance(hints, dict):
            hints["controlName"] = first_case
            hints["controlType"] = "Text"
        for ctrl in step.get("controls") or []:
            if not isinstance(ctrl, dict):
                continue
            target = str(ctrl.get("targetValue") or "")
            if "ListItem" in target and str(ctrl.get("name") or "") == first_case:
                ctrl["targetMethod"] = "name,control_type"
                ctrl["targetValue"] = "%s,Text" % first_case
                ctrl["controlType"] = "Text"
                ctrl["automationId"] = ""


def _sync_flow_package(flow: Dict[str, Any]) -> None:
    packages = flow.get("flowPackages")
    if isinstance(packages, list) and packages:
        packages[0]["stepIds"] = [s.get("id") for s in flow.get("steps") or []]


def build_residual_gui_flow(manifest: Dict[str, Any],
                            output_path: Optional[str] = None,
                            output_dir: Optional[str] = None,
                            include_matrix: bool = True,
                            template_path: Optional[str] = None) -> Dict[str, Any]:
    """生成残余 GUI 流：export_s7 的 36 步 Excel/IEC + 可选 10 步矩阵块。

    · include_matrix=False → 严格沿用 export_s7 的 36 步（Excel/IEC，移除矩阵）；
    · include_matrix=True  → 在首 case 的 IEC 步后插入矩阵块，3 case 共 46 步。
      这是 U-9 新口径：`.wri` 不是 `matrix_{n}.txt`，高分辨率矩阵仍必须 GUI。
    """
    flow = export_s7.build_residual_gui_flow(manifest=manifest)
    excel_iec_steps = int(flow.get("totalSteps") or 0)
    if include_matrix:
        matrix = _matrix_steps(template_path or _default_template_path())
        if matrix:
            first_case = str((flow.get("exportedCases") or [""])[0] or "")
            if first_case and first_case != "综合1":
                # 矩阵块模板的 notes 写死“综合1 矩阵输出目录”；DEMO 首 case 可能是
                # 综合2，替换为真实卡名，避免现场对账误读。
                for step in matrix:
                    for key in ("name", "description", "successLog"):
                        if isinstance(step.get(key), str):
                            step[key] = step[key].replace("综合1", first_case)
                    for ctrl in step.get("controls") or []:
                        if isinstance(ctrl, dict) and isinstance(ctrl.get("notes"), str):
                            ctrl["notes"] = ctrl["notes"].replace("综合1", first_case)
            insert_at = _first_case_end_index(flow)
            flow["steps"][insert_at:insert_at] = matrix
            flow["matrixStepsRemoved"] = 0
            flow["matrixStepsRetained"] = len(matrix)
            flow["excelIecSteps"] = excel_iec_steps
            flow["totalSteps"] = len(flow.get("steps") or [])
            flow["version"] = "2.0-residual-46-matrix-retained"
            flow["description"] = (
                "S7 残余 GUI（Excel/IEC %d 步 + 矩阵块 %d 步 = %d 步）："
                "Vault 直取接管 .wri/.wrp/.wrt/.wrtd/.dat/.wrm；"
                "Excel/IEC 与高分辨率 matrix 仍走 GUI（U-9 实测：.wri 12扇区×3风速仓 "
                "≠ GUI 16扇区×25风速仓）。" % (excel_iec_steps, len(matrix),
                                                flow["totalSteps"]))
            _fix_first_case_selection(flow)
            _sync_flow_package(flow)

    if output_dir:
        flow.setdefault("runtimeConfig", {})["outputDir"] = str(output_dir)
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(flow, fh, ensure_ascii=False, indent=2)
    return flow


# ---------------------------------------------------------------------------
# 门后直取 + 残余流（单一编排入口）
# ---------------------------------------------------------------------------
def export_and_build_residual(
    site_label: str,
    case_prefix: str,
    param_rows: Sequence[Dict[str, Any]],
    expected_ns: Sequence[int],
    output_dir: str,
    vault_root: Optional[str] = None,
    baseline_max: int = 0,
    flow_output_path: Optional[str] = None,
    include_matrix: bool = True,
    copy: bool = True,
    diagnostics: Optional[Dict[str, Any]] = None,
    created_ns: Optional[Sequence[int]] = None,
    db_query_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """完成门通过后：按实际卡号集合直取，生成残余 GUI 流，写双计数 manifest。

    返回 dict（ok/outcome/failure_reason/两个计数/manifest/冗余流路径/缺口）。
    任何期望卡缺 `.wri`/`.wrp` → ok=False（整批不标成功）；条件族缺失只进
    manifest optional_missing（由 export_s7/mup_vault_export 负责记录）。
    """
    diag = diagnostics if diagnostics is not None else {}
    expected = sorted({int(n) for n in (expected_ns or ())})
    selected, missing, simulated = annotate_rows_for_expected(
        param_rows, expected, baseline_max=baseline_max, created_ns=created_ns)

    if not selected:
        return {
            "ok": False,
            "outcome": "failed",
            "failure_reason": "NO_PARAM_ROWS_FOR_EXPECTED_NS: expected=%s simulated=%s" % (
                expected, simulated),
            "expected_ns": expected,
            "missing_expected": list(missing),
            "selected_rows": [],
            "manifest": {},
            "manifest_path": "",
            "residual_flow": None,
            "residual_flow_path": "",
        }
    if expected and missing:
        return {
            "ok": False,
            "outcome": "failed",
            "failure_reason": "EXPECTED_NS_WITHOUT_PARAM_ROW: %s（paramTable 推导=%s）" % (
                missing, simulated),
            "expected_ns": expected,
            "missing_expected": list(missing),
            "selected_rows": selected,
            "manifest": {},
            "manifest_path": "",
            "residual_flow": None,
            "residual_flow_path": "",
        }

    result = export_s7.run_export_s7(
        site=case_prefix or site_label,
        param_table=selected,
        output_dir=output_dir,
        vault_root=vault_root,
        copy=copy,
        diagnostics=diag,
        db_query_fn=db_query_fn,
    )
    manifest = dict(result.get("manifest") or {})
    # 直取匹配用 CaseGUID；manifest 仍保留人类站点名，便于现场对账。
    manifest["site"] = site_label or case_prefix or ""
    manifest["case_prefix"] = case_prefix or manifest.get("case_prefix", "")
    manifest["expected_ns"] = list(expected)
    manifest["baseline_max_synthesis"] = int(baseline_max or 0)
    manifest["simulated_ns"] = list(simulated)
    manifest["dropped_ns"] = (sorted(set(simulated) - set(expected))
                              if expected else [])

    completed = {int(c.get("index")) for c in manifest.get("cases") or []
                 if c.get("mandatory_present") == 2}
    missing_completed = sorted(set(expected) - completed)
    reason = str(result.get("failure_reason") or "")
    if result.get("outcome") != "succeeded" or missing_completed:
        ok = False
        if missing_completed:
            reason = (reason + "；" if reason else "") + \
                     "EXPECTED_NS_NOT_COMPLETED: %s" % missing_completed
        if not reason:
            reason = "EXPORT_FAILED"
    else:
        ok = True

    residual_flow = None
    flow_path = ""
    if ok:
        flow_path = flow_output_path or os.path.join(
            output_dir, "export_residual_gui_flow.json")
        residual_flow = build_residual_gui_flow(
            manifest, output_path=flow_path, output_dir=output_dir,
            include_matrix=include_matrix)
        # 交付目录内再落一份 flow，manifest 与交付物自包含；workspace 路径供 Launcher 挂载。
        delivery_flow_path = os.path.join(output_dir, "export_residual_gui_flow.json")
        if os.path.abspath(delivery_flow_path) != os.path.abspath(flow_path):
            shutil.copyfile(flow_path, delivery_flow_path)
        manifest["residual_gui_steps"] = int(residual_flow.get("totalSteps") or 0)
        manifest["residual_gui_flow_path"] = os.path.abspath(delivery_flow_path)
        manifest["residual_gui_flow_workspace_path"] = os.path.abspath(flow_path)
        manifest["residual_gui_matrix_retained"] = bool(include_matrix)

    manifest["overall_status"] = "succeeded" if ok else "failed"
    if reason:
        manifest["failure_reason"] = reason
    mup_vault_export.validate_manifest_safety(manifest)
    manifest_path = mup_vault_export.write_manifest(manifest, output_dir)

    return {
        "ok": ok,
        "outcome": "succeeded" if ok else "failed",
        "failure_reason": reason,
        "expected_ns": expected,
        "missing_expected": list(missing_completed),
        "simulated_ns": list(simulated),
        "dropped_ns": list(manifest.get("dropped_ns") or []),
        "selected_rows": selected,
        "completed_cases_count": int(result.get("completed_cases_count") or 0),
        "delivered_cases_count": int(result.get("delivered_cases_count") or 0),
        "copied_count": int(result.get("copied_count") or 0),
        "manifest": manifest,
        "manifest_path": os.path.abspath(manifest_path),
        "residual_flow": residual_flow,
        "residual_flow_path": os.path.abspath(flow_path) if flow_path else "",
    }
