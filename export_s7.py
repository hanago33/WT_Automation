# encoding: utf-8
"""S7 导出管线编排器（Vault 直取落地 + 36 步残余 GUI 动态生成）。

权威口径依据：
  1. E:\\My_RF_Project\\MUP_recon\\99_synthesis\\2026-11-02-demo-export-directfetch.md
  2. E:\\My_RF_Project\\MUP_recon\\99_synthesis\\2026-11-02-input-automation-blueprint.md §6

核心职责：
  1. 导出直取管线 (run_export_s7)：
     - 接在综合完成门之后，按 manifest 逐 case 拷贝 {CaseGuid}_synthesis_{n}.wri/.wrp（必出2件套）
       以及条件族（.wrt / .wrtd×7 / timeserie×2 / 双GUID .wrm）到 04-WT输出/mN/；
     - 计算并记录 SHA-256 hash；
     - 产出符合 blueprint §6.5 规格的 manifest v2（含 case_prefix、mandatory、optional 字段）；
     - 必出缺任一 => FAILED（绝不整批标成功）；条件族缺失 => 仅记录 optional_missing；
     - 完成集合（取wri/wrp）⊃ 交付集合（仅 m 非空的 case）明确区分两个计数字段；
     - copyfull / 带绘图行 status=completed_with_gaps，不进入交付集合；
     - 严格校验 manifest 中无 licence/license/hasp 等敏感路径。
  2. 残余 GUI 36 步动态生成 (build_residual_gui_flow)：
     - S7 原 46 步流程中删除 10 步矩阵块（step_12-21，已由 Vault 直取接管）；
     - 保留 Excel 总表 + IEC 总结 24 步导出动作 + 12 步导航动作 = 36 步；
     - 动态驱动：步骤名与控件目标不再写死 1/2/3，而是由 paramTable / manifest 动态生成，
       支持真实 DEMO 案例（从综合2、综合3 等开始）无缝对接。
"""
import os
import sys
import copy
import json
import argparse
from typing import Dict, List, Any, Optional

import mup_vault_export


def build_residual_gui_flow(
    param_table=None,
    manifest=None,
    flow_template_path=None,
    output_path=None,
) -> Dict[str, Any]:
    """根据参数表/manifest 动态生成残余 GUI 36 步流程包定义（或 N-case 动态流程）。

    原 46 步流程结构剖析：
      - step_1..11: 综合1 导航(3) + Excel(4) + IEC(4) = 11 步
      - step_12..21: 矩阵块（展开元素、取消测风点、展开风流、拨动矩阵、勾选湍流/风频、输出txt）= 10 步（已由直取接管，剔除）
      - step_22..33: 综合2 导航(4) + Excel(4) + IEC(4) = 12 步
      - step_34..45: 综合3 导航(4) + Excel(4) + IEC(4) = 12 步
      - step_46: 收尾返回 = 1 步
      => 3 case 标准场景：11 + 12 + 12 + 1 = 36 步！

    动态特性：
      - 不写死综合1/2/3，根据 param_table / manifest 中 export=True 且 m_dir!="" 的有效交付用例动态构建；
      - 步骤名称、successLog、inspectHints.controlName、控件 labelText/targetValue 均替换为真实 case 名称；
      - 步骤参数 exportSubdir 设为对应 mN，exportExcelName/exportIecName 动态参数化。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if not flow_template_path:
        flow_template_path = os.path.join(base_dir, "flow_packages", "flow_definition_导出综合计算结果.json")

    template_data = {}
    if os.path.isfile(flow_template_path):
        with open(flow_template_path, "r", encoding="utf-8") as fh:
            template_data = json.load(fh)

    # 提取所有步骤模板
    orig_steps = template_data.get("steps", [])
    step_dict = {s.get("id"): copy.deepcopy(s) for s in orig_steps}

    # 获取有效导出用例列表
    cases_to_export = []
    if manifest and isinstance(manifest, dict) and "cases" in manifest:
        cases_to_export = [
            c for c in manifest["cases"]
            if c.get("export") and c.get("m_dir") and c.get("m_dir") not in ("(none)", "none", "")
        ]
    elif param_table:
        mapping = mup_vault_export.build_case_mapping(param_table)
        cases_to_export = [
            r for r in mapping
            if r.get("export") and r.get("m") and r.get("m") not in ("(none)", "none", "")
        ]
        # 补齐 index 字段（1-based）
        for i, c in enumerate(cases_to_export):
            if "index" not in c:
                c["index"] = i + 1
            if "m_dir" not in c:
                c["m_dir"] = c.get("m", "")
    else:
        # 默认 3 行标准 case（综合1, 综合2, 综合3）
        cases_to_export = [
            {"index": 1, "case": "综合1", "m_dir": "m1"},
            {"index": 2, "case": "综合2", "m_dir": "m4"},
            {"index": 3, "case": "综合3", "m_dir": "m10"},
        ]

    new_steps = []
    new_step_ids = []

    def _replace_step_case_names(step, old_label, new_label, m_dir):
        """深拷贝并替换步骤中的 case 名称、控件名及输出参数。"""
        s = copy.deepcopy(step)
        s["name"] = s.get("name", "").replace(old_label, new_label)
        s["description"] = s.get("description", "").replace(old_label, new_label)
        s["successLog"] = s.get("successLog", "").replace(old_label, new_label)

        if "inspectHints" in s and isinstance(s["inspectHints"], dict):
            hints = s["inspectHints"]
            if hints.get("controlName") == old_label:
                hints["controlName"] = new_label
            if "uiPath" in hints:
                hints["uiPath"] = hints["uiPath"].replace(old_label, new_label)

        if "controls" in s and isinstance(s["controls"], list):
            for ctrl in s["controls"]:
                ctrl["name"] = ctrl.get("name", "").replace(old_label, new_label)
                if ctrl.get("labelText") == old_label:
                    ctrl["labelText"] = new_label
                if ctrl.get("relatedLabelName") == old_label:
                    ctrl["relatedLabelName"] = new_label
                tv = ctrl.get("targetValue", "")
                if old_label in tv:
                    ctrl["targetValue"] = tv.replace(old_label, new_label)

        # 参数化输出子目录与文件名
        sp = s.get("stepParams", {})
        if "exportSubdir" in sp:
            sp["exportSubdir"] = m_dir
        if "exportExcelName" in sp:
            sp["exportExcelName"] = f"{new_label}_{m_dir}_综合计算结果.xlsx"
        if "exportIecName" in sp:
            sp["exportIecName"] = f"{new_label}_{m_dir}_IEC总结.xlsx"

        return s

    # 动态组装各 case 的 GUI 步骤
    for order_idx, c in enumerate(cases_to_export):
        c_label = c.get("case") or f"综合{order_idx + 1}"
        m_dir = c.get("m_dir") or "m1"

        if order_idx == 0:
            # 首个 Case：11 步（对应模板 step_1..11，选择卡片 + 打开结果视图 + Excel 4步 + IEC 4步）
            # 模板基准以 step_1..11 为参照
            base_ids = [f"step_{i}" for i in range(1, 12)]
            for bid in base_ids:
                if bid in step_dict:
                    st = _replace_step_case_names(step_dict[bid], "综合1", c_label, m_dir)
                    new_id = f"s7_case1_{bid}"
                    st["id"] = new_id
                    new_steps.append(st)
                    new_step_ids.append(new_id)
        else:
            # 后续 Case：12 步（返回列表 + 选择卡片 + 打开结果 + 展开面板 + Excel 4步 + IEC 4步）
            # 模板基准以 step_22..33 为参照
            base_ids = [f"step_{i}" for i in range(22, 34)]
            case_tag = f"case{order_idx + 1}"
            for bid in base_ids:
                if bid in step_dict:
                    st = _replace_step_case_names(step_dict[bid], "综合2", c_label, m_dir)
                    new_id = f"s7_{case_tag}_{bid}"
                    st["id"] = new_id
                    new_steps.append(st)
                    new_step_ids.append(new_id)

    # 最终收尾步：1 步（返回综合结果列表）
    if "step_46" in step_dict:
        st_final = copy.deepcopy(step_dict["step_46"])
        st_final["id"] = "s7_final_step_return"
        new_steps.append(st_final)
        new_step_ids.append(st_final["id"])

    flow_result = {
        "version": "2.0-residual-36",
        "project": "WT_Automation",
        "description": "S7 残余 GUI 自动化导出流（36步）：由 Vault 直取接管矩阵产物后，保留 Excel 总表及 IEC 总结动态导出动作（24步导出+12步导航）。",
        "totalSteps": len(new_steps),
        "exportedCases": [c.get("case") for c in cases_to_export],
        "exportedDirs": [c.get("m_dir") for c in cases_to_export],
        "matrixStepsRemoved": 10,
        "runtimeConfig": template_data.get("runtimeConfig", {
            "outputDir": "04-WT输出",
        }),
        "flowPackages": [
            {
                "id": "导出综合计算结果_残余36步",
                "name": "导出综合计算结果（残余36步）",
                "description": "已剔除 step_12..21 矩阵导出步骤，保留 Excel 总表与 IEC 总结导出",
                "stepIds": new_step_ids,
            }
        ],
        "steps": new_steps,
    }

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(flow_result, fh, ensure_ascii=False, indent=2)

    return flow_result


def run_export_s7(
    site: str,
    param_table=None,
    output_dir: Optional[str] = None,
    vault_root: Optional[str] = None,
    copy: bool = True,
    case_name: Optional[str] = None,
    db_query_fn=None,
    preserve_names: bool = True,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """S7 导出直取完整管线：接在综合完成门之后，执行直取落盘、manifest v2 生成及 36 步残余 GUI 流组装。

    参数：
      site: 站点名（如 'DEMO'）或 36 位 Case GUID。
      param_table: 参数表路径、文本或列表（缺省自动读取标准参数表）。
      output_dir: 交付输出目录（通常为 <项目>/04-WT输出）。
      vault_root: 可选 Vault 根目录。
      copy: True 真实拷贝并落盘；False 仅预演生成计划。
      case_name: 多 Case 场景下的显式用例名（如 'WT'）。
      db_query_fn: 可选 LocalDB 查询函数注入（无外部 subprocess 依赖）。
      preserve_names: 是否保留源文件名（建议 True）。
      diagnostics: 可选外部诊断容器。

    返回完整执行报告 dict。
    """
    if diagnostics is None:
        diagnostics = {}

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if not param_table:
        default_param = os.path.join(base_dir, "flow_packages", "param_table_发送综合计算.json")
        if os.path.isfile(default_param):
            param_table = default_param

    if not output_dir:
        output_dir = os.path.join(base_dir, "04-WT输出")

    target_root = os.path.abspath(output_dir)
    os.makedirs(target_root, exist_ok=True)

    # 1. 站点身份解析与 Case GUID 提取
    case_prefix = mup_vault_export.resolve_site_case_id(
        site=site,
        vault_root=vault_root,
        case_name=case_name,
        db_query_fn=db_query_fn,
        diagnostics=diagnostics,
        fallback_to_site=False,
    )
    diagnostics["case_prefix"] = case_prefix

    # 2. 构建 export_manifest v2
    manifest = mup_vault_export.build_export_manifest(
        param_table=param_table,
        site=site,
        case_prefix=case_prefix,
        vault_root=vault_root,
        output_dir=target_root,
        schema_version="v2",
        diagnostics=diagnostics,
    )

    # 3. 执行 Vault 直取拷贝
    manifest = mup_vault_export.export_by_manifest(
        manifest_or_param_table=manifest,
        site=site,
        output_dir=target_root,
        vault_root=vault_root,
        copy=copy,
        preserve_names=preserve_names,
        case_prefix=case_prefix,
        case_name=case_name,
        db_query_fn=db_query_fn,
        diagnostics=diagnostics,
    )

    # 4. 组装残余 36 步 GUI 流程包
    residual_flow = build_residual_gui_flow(
        manifest=manifest,
    )
    diagnostics["residual_gui_flow_steps"] = residual_flow.get("totalSteps", 0)

    # 5. 校验敏感路径安全性
    mup_vault_export.validate_manifest_safety(manifest)

    # 6. 构造返回摘要
    outcome = "succeeded" if manifest.get("overall_status") == "succeeded" else "failed"
    result = {
        "outcome": outcome,
        "site": site,
        "case_prefix": case_prefix,
        "manifest_path": manifest.get("manifest_path", os.path.join(target_root, "export_manifest.json")),
        "total_cases": manifest.get("total_cases", 0),
        "completed_cases_count": manifest.get("completed_cases_count", 0),
        "delivered_cases_count": manifest.get("delivered_cases_count", 0),
        "export_cases_count": manifest.get("export_cases_count", 0),
        "gap_cases_count": manifest.get("gap_cases_count", 0),
        "copied_count": manifest.get("copied_count", 0),
        "failure_reason": manifest.get("failure_reason", ""),
        "residual_gui_flow_steps": residual_flow.get("totalSteps", 0),
        "diagnostics": diagnostics,
        "manifest": manifest,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="S7 导出直取管线与残余 36 步 GUI 编排器")
    parser.add_argument("--site", required=True, help="站点名（如 DEMO）或 Case GUID")
    parser.add_argument("--param-table", default=None, help="参数表路径 (JSON/CSV/Excel)")
    parser.add_argument("--output-dir", default="04-WT输出", help="交付输出根目录")
    parser.add_argument("--vault-root", default=None, help="MUP Vault 存储根目录")
    parser.add_argument("--dry-run", action="store_true", help="预演模式（不实际复制）")
    parser.add_argument("--generate-flow", default=None, help="导出生成的残余 GUI flow JSON 路径")

    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    diag = {}
    res = run_export_s7(
        site=args.site,
        param_table=args.param_table,
        output_dir=args.output_dir,
        vault_root=args.vault_root,
        copy=not args.dry_run,
        diagnostics=diag,
    )

    if args.generate_flow:
        build_residual_gui_flow(manifest=res["manifest"], output_path=args.generate_flow)
        print(f"Residual GUI flow saved to: {args.generate_flow}")

    print("=" * 60)
    print(f"S7 Export Pipeline Result: {res['outcome'].upper()}")
    print(f"Site: {res['site']} | CaseGUID: {res['case_prefix']}")
    print(f"Completed Cases: {res['completed_cases_count']} | Delivered Cases: {res['delivered_cases_count']} (Total: {res['total_cases']})")
    print(f"Copied Products: {res['copied_count']} files -> {res['manifest_path']}")
    if res.get("failure_reason"):
        print(f"Failure Reason: {res['failure_reason']}")
    print("=" * 60)

    sys.exit(0 if res["outcome"] == "succeeded" else 1)


if __name__ == "__main__":
    main()
