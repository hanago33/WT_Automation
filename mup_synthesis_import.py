# encoding: utf-8
"""「综合」(WRA Analysis / Synthesis) 对象数据层落库（模式 B，对标 mup_terrain_import）：
在既有 CFD 结果之上，于副本库按金样同构写一个"综合"对象（只建对象/引用，不触发计算）。

职责
----
  站点/工况选择 + 参考综合（克隆源） + 表单参数（综合6 字段）
      -> 校验既有链路（Case → DASCFDObject×N → dbo.CFD 方向/稳定度）
      -> 按金样同构 9~10 张表单事务落库（ComputationObject → WRAAnalysis 家族）
      -> 写后校验（行数 + 键等值 + 参数逐列）+ rollback.sql

金样与证据锚点（H:\\Rea\\rea-main\\99_synthesis\\2026-11-04-synthesis-datalayer-zcode.md，
全部为观测；P3-J：本仓 E:\\My_RF_Project\\MUP_recon\\99_synthesis\\ 下没有这份，引用改绑实存位置）
----
  · 金样：副本库 MUPLocalDb_site_verify DEMO 站 Case 'WT' (ea368c7f-9810-487d-be4c-
    bbcc9339c83e) 的综合1~5（综合5 为 2026-11-02 真实 GUI 新建/保存产物，S6 报告）。
  · 表单字段 → 落表（综合6 表单，用户 2026-11-04 提供口径）：
      描述                -> WRAAnalysis.Description（金样全 NULL，可空）
      名称                -> WRAAnalysis.Name（GUI 自增 = 综合{max(SynthesisNumber)+1}）
      风机配置选取         -> 元素快照 WRAAnalysisElements（"默认配置" = dbo.Configurations
                             的 CaseElementConfiguration 行；已实证其元素集 ≡ 综合1 快照）
      对所有风机点计算风廓线 -> WRAAnalysisParameters.Global_ComputeWindSpeedProfiles
                             （VertProfiles 是工况级定义表、无分析外键，不随勾选写行）
      计算限功率          -> WRAAnalysisParameters.Global_ComputeCurtailment
                             （本接口只写该标志位；WRAAnalysisElementsWT_CurtailmentConstraints
                             约束行是 GUI 勾选路径的产物，本接口从不写——副本库实测该表 0 行，
                             侧审 S-P3-2 修正此处文档漂移）
      参考点/气象参考      -> WRAAnalysisClimatologyConfiguration(Element_Id, Climatology_Id,
                             SelectedHeight, UseTurbulence) + WRAAnalysis.ReferenceClimatology_Id
                             指向该行；多塔从属行同表追加（金样单塔）
      空气密度/海拔       -> WRAAnalysisParameters.Climatology_ReferenceAirDensity /
                             Climatology_ReferenceAirDensityAltitude
      分离区输出          -> WRAAnalysisParameters.Global_SectorStep（22.5）
      时间序列综合        -> WRAAnalysisParameters.Global_ComputeTemporalSynthesis
                             （勾选才加 WRAAnalysis_TemporalClimatology 链接行）
      CFD结果选取         -> WRAAnalysis_CFDs（WRAAnalysis ↔ ComputationObject.DASCFDObject；
                             dbo.CFD.Direction=扇区度数/Stability=热稳定度）
      计算尾流效应        -> WRAAnalysisParameters.Global_ComputeWake（Wake_* 参数行随行保留）
  · ComputationId 语义：MTDElementComputationPoint 计算点 int 键（MTDWRAAnalysisModel
    SetElement(element, computationId)/GetElementComputationPoint CIL）；风机 11 点
    （轮毂+VertProfiles 20..200@20 十档）、塔 1 点；同一物理元素跨综合复用同一 ComputationId
    （综合1/3 逐点相等已实证）。克隆源快照原样携带，故无需重算。
  · WRAWTType 是分析侧机型快照，跨综合共享（25b7e349 被综合1~5 全部引用）→ 克隆直接复用。
  · 消费链：dbo.CFD(16 扇区, Stability) ← ComputationObject(DASCFDObject, Parent_Id=Case)
    ← WRAAnalysis_CFDs ← WRAAnalysis →(计算后) WRAnalysisResultFiles/Files(vault .wri+.wrp)、
    WindResourceMaps(SynthesisId/MappingId)；"设置中"态 = 无结果文件（S1 完成判定同口径）。
  · 大型风电场配置：每个综合恒有 1 行 WRAAnalysisLargeParkConfiguration（金样默认
    HMean=100/DMean=100/Z0=0.05/HStart=0/Ibl1=0.05/Ibl2=0.3/名称=场区 1）→ 一并克隆。
  · 极风参考（侧审 S-P1-1 + P3-F）：克隆默认原样保留源行 ExtremeWindReference_Id 与
    ExtremeWindReferencePointId（生产 5 行：2 行 (_Id=NULL, PointId=0)、3 行 (_Id 非空,
    PointId=1157)）。--clear-extreme-wind-reference 成对置 (_Id=NULL, PointId=0)：
    「_Id=NULL 但 PointId 仍指向点位」是生产从未观测的半清空形状，GUI 是否会忽略 _Id 仍属
    unknown（侧审 §9.6），所以不造这个形状；写后校验按开关分支同时断言两列。
  · 变长列读取（P1-A）：克隆源的 t 列（WRAAnalysis.Description/Name、
    LargeParkParameters_Name）走 hex 通道，先探 MAX(DATALENGTH) 换算 sqlcmd -y 宽度、
    读回时逐列带回 DATALENGTH 并在解码前断言 len(hex)==2×DATALENGTH。过去默认 -W 通道
    把变长列按 256 个显示字符裁掉（-W 与 -y 互斥），实测 145 字符的 Description 只回读
    64 字符照样落库并报 [OK]；现在不符即 fail-closed（db_guard，明确「未写库」）。

红线
----
  · 默认 dry-run；写库必须 --apply 且 --db 显式；生产库 MUPLocalDb 一律拒绝、无放行开关
    （侧审 S-P2-1：删除永不生效且给 apply_plan 开旁路的假开关 --danger-write-prod）；
    副本库白名单 *_verify / *_test / *_sandbox（复用气象接口口径）。
  · 单事务 + rollback.sql（FK 逆序删除）+ 写后校验 fail-closed；校验失败自动执行
    rollback.sql 并把回滚结果写进返回负载（侧审 S-P2-5：回滚成功=失败态可解释，
    回滚失败=升级为需人工介入）。
  · 只建对象/引用，绝不提交 HPC 计算、不产生/删除 vault 产物文件、不启动 MUP、
    不触 HASP/GUI、不改 user.config。
  · 同名/同 SynthesisNumber 拒绝；克隆源缺元素/缺 CFD 结果 fail-closed。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import mup_weather_import as mw  # 错误代数 / SQL 工具 / 白名单（与站点/地形接口同款复用）

ImportCode = mw.ImportCode
ImportIssue = mw.ImportIssue
ImportResult = mw.ImportResult


class PreconditionError(LookupError):
    """前置依赖缺失（exit 7，对齐 P4 口径）：引导上游补数据，非用户输入错误。"""
    pass


__all__ = [
    "ImportIssue",
    "ImportResult",
    "ImportCode",
    "PreconditionError",
    "SynthesisSpec",
    "WritePlan",
    "build_plan",
    "apply_plan",
    "apply_ok",
    "run",
    "main",
]

DEFAULT_CONFIGURATION = "CaseElementConfiguration"  # GUI「默认配置」
DEFAULT_DENSITY = 1.255          # 综合6 表单默认
DEFAULT_ALTITUDE = 0.0
DEFAULT_SECTOR_STEP = 22.5

DISC_WRA_OBJECT = "MTDWRAAnalysisObject"
DISC_CFD_OBJECT = "DASCFDObject"

# 目标表期望列（名称+类型类）。写前与 sys.columns 严格全等比较：MUP 版本升级加列/改列
# 一律 fail-closed（新增 NOT NULL 列本就需要新值语义，不能静默猜）。
# 类型类：s=字符串/GUID(N'..')，n=数值（SQL Server 渲染文本原样回写），
#         t=自由文本（克隆读取走 hex 通道，见 _clone_literals——侧审 S-P2-3）。
T_COMPUTATION_OBJECT = {
    "Id": "s", "Discriminator": "s", "Micro_AtlasId": "s", "CFD_Id": "s", "Parent_Id": "s",
}
T_WRA_ANALYSIS = {
    "Id": "s", "Description": "t", "Name": "t", "SynthesisNumber": "n",
    "ExtremeWindReferencePointId": "n", "ExtremeWindReference_Id": "s",
    "ReferenceClimatology_Id": "s", "ComputationObject_Id": "s",
}
T_CLIMATO_CONFIG = {
    "Id": "s", "ComputationId": "n", "UseTurbulence": "n", "SelectedHeight": "n",
    "AlphaSigmaCoefficient": "n", "Climatology_Id": "s", "Element_Id": "s",
}
T_LINK_CLIMATO = {
    "WRAAnalysis_Id": "s", "WRAAnalysisClimatologyConfiguration_Id": "s",
}
T_LINK_TEMPORAL = {
    "WRAAnalysis_Id": "s", "WRAAnalysisClimatologyConfiguration_Id": "s",
}
T_LINK_CFD = {
    "WRAAnalysis_Id": "s", "CFD_Id": "s",
}
T_ELEMENTS = {
    "Id": "s", "Discriminator": "s", "Element_Id": "s", "WindTurbineType_Id": "s",
    "WRAAnalysis_Id": "s", "SurfaceZone_Id": "s",
}
T_CPM = {
    "Id": "s", "ComputationId": "n", "IsNearest": "n", "Zone_Id": "s",
    "WRAAnalysisElement_Id": "s", "IsRadius": "n",
}
T_LARGE_PARK = {
    "Id": "s", "LargeParkParameters_HMean": "n", "LargeParkParameters_DMean": "n",
    "LargeParkParameters_Z0": "n", "LargeParkParameters_HStart": "n",
    "LargeParkParameters_Ibl1": "n", "LargeParkParameters_Ibl2": "n",
    "LargeParkThrustCurve_Id": "s", "WRAAnalysis_Id": "s",
    "LargeParkParameters_Name": "t",
}
T_PARAMETERS = {
    "Id": "s", "Climatology_ReferenceAirDensity": "n",
    "Climatology_ReferenceAirDensityAltitude": "n", "ExtremeWind_Reference": "n",
    "ExtremeWind_ReferenceSpeed": "n", "ExtremeWind_Type": "n",
    "IEC_DefaultAlphaSigma": "n", "Global_SectorStep": "n",
    "Global_StrongWindThreshold": "n", "Global_ComputeWindSpeedProfiles": "n",
    "Global_ComputeWake": "n", "Global_ComputeIec": "n",
    "Global_ComputeTemporalSynthesis": "n", "Global_ComputeExtremeWind": "n",
    "Turbulence_MaxTurbulenceThresholdCoefficient": "n",
    "Turbulence_MinTurbulenceThresholdCoefficient": "n",
    "Turbulence_TurbulenceThresholdForPowerDensity": "n",
    "MatrixDimensions_DirectionMaxValue": "n", "MatrixDimensions_DirectionStep": "n",
    "MatrixDimensions_SpeedMaxValue": "n", "MatrixDimensions_SpeedMinValue": "n",
    "MatrixDimensions_SpeedStep": "n", "Weibull_MethodMapping": "n",
    "Weibull_MethodPoints": "n", "Wake_AlphaMax": "n", "Wake_DiameterDistanceMax": "n",
    "Wake_DirectionalResolution": "n", "Wake_MappingComputation": "n",
    "Wake_MappingFollowNorm": "n", "Wake_PointsComputation": "n",
    "Wake_SpeedResolution": "n", "SpeedReductionModel": "n",
    "SpeedReductionModelEvm_Resolution": "n",
    "SpeedReductionModelPark_DecayConstants": "n", "SpeedReductionModelPark_Kmax": "n",
    "SpeedReductionModelPark_Kmin": "n", "TurbulenceModel": "n",
    "TurbulenceModelQuarton_Alpha1": "n", "TurbulenceModelQuarton_Alpha2": "n",
    "TurbulenceModelQuarton_Alpha3": "n", "TurbulenceModelQuarton_BladesNumber": "n",
    "TurbulenceModelQuarton_K1": "n", "TurbulenceModelQuarton_Reference": "n",
    "TurbulenceModelQuarton_SpeedRatio": "n", "WRAAnalysis_Id": "s",
    "Global_ParallelCore": "n", "Global_RewsRotorSegments": "n",
    "Wake_LargeParkComputation": "n", "Wake_SdMax": "n", "Wake_ScMax": "n",
    "Wake_PAlignement": "n", "Wake_SweepDirection": "n",
    "ExtremeWind_ReferenceSpeed100Years": "n", "Global_ComputeCurtailment": "n",
    "Climatology_WeightingDistanceExponent": "n",
    "Climatology_ElementWeightingNbNearestPoint": "n",
    "Climatology_ElementWeightingRadiusSize": "n",
    "Climatology_ElementWeightingRadiusNbNearestPoint": "n",
    "Climatology_SurfaceWeightingNbNearestPoint": "n",
    "Climatology_SurfaceWeightingRadiusSize": "n",
    "Climatology_SurfaceWeightingRadiusNbNearestPoint": "n", "CoverageModel": "n",
    "IEC_IECTurbulenceMethod": "n", "IEC_TurbulenceCoefficientUnderCurtailment": "n",
    "Wake_SpeedReductionSuperpositionType": "n",
    "Wake_AddedTurbulenceSuperpositionType": "n", "Wake_UseWakePropagation": "n",
    "Wake_IsSiteOffShore": "n", "SpeedReductionModelPark_KForced": "n",
}


def apply_ok(db: str) -> bool:
    """写库白名单（复用气象接口口径）：*_verify / *_test / *_sandbox。"""
    return mw.apply_ok(db)


def _lit(value: Optional[str]) -> str:
    """纯字符串/GUID 字面量：None → NULL；文本 'NULL' 原样写成 N'NULL'。

    侧审 S-P2-2：旧实现把字面 'NULL' 折叠为 NULL 关键字（sqlcmd 渲染约定误用于
    调用方输入），名称/描述恰为 'NULL' 时会被静默置空。sqlcmd 文本协议的还原
    请用 _lit_or_null。
    """
    if value is None:
        return "NULL"
    return "N'" + str(value).replace("'", "''") + "'"


def _lit_or_null(value: Optional[str]) -> str:
    """sqlcmd 文本协议还原：None 或渲染文本 'NULL' → NULL 关键字（与
    mup_terrain_import._lit_or_null 同族约定）。仅用于读自 sqlcmd 输出的 token，
    不得用于调用方输入。"""
    if value is None or (isinstance(value, str) and value.strip().upper() == "NULL"):
        return "NULL"
    return _lit(value)


def _num(value: Any) -> str:
    """数值字面量：int/float → SQL 可解析文本（SQL Server real/float 按列精度取整）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


def _sql_float(value: float) -> str:
    return _num(value)


def _is_zero_number(token: Any) -> bool:
    """回读文本是否就是 0（'0' / '0.0' / '0E+00' 都算）；空、NULL、不可解析一律 False。

    写后校验里「这一列必须是 0」的判定只有这一个解释者，读不懂就当不成立（fail-closed）。
    """
    raw = str(token if token is not None else "").strip()
    if not raw or raw.upper() == "NULL":
        return False
    try:
        return float(raw) == 0.0
    except ValueError:
        return False


class SynthesisSpec:
    """综合对象输入参数（综合6 表单口径；未显式给定的表单字段用表单默认）。"""

    def __init__(self, site: str, case: Optional[str] = None, name: Optional[str] = None,
                 description: Optional[str] = None, clone_from: Optional[str] = None,
                 configuration: str = DEFAULT_CONFIGURATION,
                 stability: Optional[int] = None,
                 density: float = DEFAULT_DENSITY, altitude: float = DEFAULT_ALTITUDE,
                 sector_step: float = DEFAULT_SECTOR_STEP,
                 use_turbulence: bool = False, compute_profiles: bool = False,
                 compute_wake: bool = False, compute_curtailment: bool = False,
                 compute_temporal: bool = False,
                 compute_iec: Optional[bool] = None,
                 compute_extreme_wind: Optional[bool] = None,
                 height: Optional[float] = None,
                 clear_extreme_wind_reference: bool = False) -> None:
        self.site = (site or "").strip()
        self.case = (case or "").strip() or None
        self.name = (name or "").strip() or None
        self.description = description
        self.clone_from = (clone_from or "").strip() or None
        self.configuration = (configuration or "").strip() or DEFAULT_CONFIGURATION
        self.stability = stability
        self.density = float(density)
        self.altitude = float(altitude)
        self.sector_step = float(sector_step)
        self.use_turbulence = bool(use_turbulence)
        self.compute_profiles = bool(compute_profiles)
        self.compute_wake = bool(compute_wake)
        self.compute_curtailment = bool(compute_curtailment)
        self.compute_temporal = bool(compute_temporal)
        self.compute_iec = compute_iec
        self.compute_extreme_wind = compute_extreme_wind
        self.height = None if height is None else float(height)
        self.clear_extreme_wind_reference = bool(clear_extreme_wind_reference)

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if not self.site:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "site 必填（站点 Name 或 Id）"))
        if self.stability is not None and self.stability < 0:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "--stability 必须为非负整数"))
        for key, value in (("density", self.density), ("altitude", self.altitude),
                           ("sector-step", self.sector_step)):
            if value < 0:
                issues.append(mw.ImportIssue(mw.ImportCode.INPUT, f"--{key} 必须为非负数"))
        if self.height is not None and self.height < 0:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "--height 必须为非负数"))
        return issues


@dataclass
class WritePlan:
    spec: SynthesisSpec
    db: str
    site_id: str
    site_name: str
    case_id: str
    case_name: str
    analysis_id: str
    computation_object_id: str
    climato_config_id: str
    name: str
    synthesis_number: int
    clone_from_name: str
    clone_from_id: str
    element_count: int
    cpm_count: int
    cpm_insert_count: int
    cfd_count: int
    temporal: bool
    clear_extreme_wind_reference: bool
    overrides: dict
    sql: str
    rollback_sql: str

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["spec"] = self.spec.__dict__
        return d


# --- 库内解析 ------------------------------------------------------------------

def _rows(db: str, sql: str, timeout: int = 600) -> list[list[str]]:
    ok, out, err = mw._run_sql(db, "SET NOCOUNT ON;\n" + sql, timeout=timeout)
    if not ok:
        raise RuntimeError(err or out or "sqlcmd failed")
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append([p.strip() for p in line.split("|")])
    return rows


def _var_width(db: str, probe_sql: str) -> int:
    """变长列读取前的宽度探测（P1-A 归属边界，见 _clone_literals）。

    单列超过 sqlcmd -y 上限时 mw.var_column_width 抛 RuntimeError：调用方必须停，
    不能退回「按裁剩的值继续」。这层包装同时是给单测的注入缝。
    """
    return mw.var_column_width(db, probe_sql)


def _sized_rows(db: str, sql: str, width: int) -> list[list[str]]:
    """定宽读变长列：-y <width> 且不带 -W（两者互斥，-W 正是 P1-A 的裁剪根因）。"""
    return mw.read_sized_rows(db, sql, width)


def _single(rows: list[list[str]], what: str, db: str) -> list[str]:
    if not rows:
        raise LookupError(f"{what}：库内无匹配（db={db}）")
    if len(rows) > 1:
        raise LookupError(f"{what}：匹配到 {len(rows)} 行，需要唯一（db={db}）")
    return rows[0]


def _resolve_site(db: str, selector: str) -> tuple[str, str]:
    rows = _rows(db, "SELECT Id, Name FROM dbo.Sites WHERE Id=" + _lit(selector) +
                 " OR Name=" + _lit(selector) + ";")
    if not rows:
        raise LookupError(f"站点不存在: {selector}（db={db}）")
    if len(rows) > 1:
        raise LookupError(f"站点选择器 {selector} 命中 {len(rows)} 行，请用 Id")
    return rows[0][0], rows[0][1]


def _resolve_case(db: str, site_id: str, selector: Optional[str]) -> tuple[str, str, list]:
    rows = _rows(db, "SELECT Id, Name FROM dbo.Cases WHERE Parent_Id=" + _lit(site_id) + ";")
    if not rows:
        raise PreconditionError(f"站点 {site_id} 下没有工况（Cases）：先用 mup_site_import.py 建站点/工况再来")
    if selector:
        hit = [r for r in rows if r[0] == selector or r[1] == selector]
        if not hit:
            names = ", ".join(f"{r[1]}({r[0]})" for r in rows)
            raise LookupError(f"工况 {selector} 不在该站点下；可用: {names}")
        if len(hit) > 1:
            raise LookupError(f"工况选择器 {selector} 命中 {len(hit)} 行，请用 Id")
        return hit[0][0], hit[0][1], rows
    if len(rows) > 1:
        names = ", ".join(f"{r[1]}({r[0]})" for r in rows)
        raise PreconditionError(f"站点有 {len(rows)} 个工况，请用 --case 指定: {names}")
    return rows[0][0], rows[0][1], rows


def _check_columns(db: str, table: str, expected: dict[str, str]) -> None:
    rows = _rows(
        db,
        "SELECT c.name, ty.name FROM sys.columns c "
        "JOIN sys.types ty ON ty.user_type_id=c.user_type_id "
        "JOIN sys.tables t ON t.object_id=c.object_id "
        f"WHERE t.name={_lit(table)} ORDER BY c.column_id;")
    actual = {r[0]: r[1] for r in rows}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise LookupError(
            f"表 {table} 列与期望不一致（MUP 版本变化？fail-closed）: "
            f"缺少={missing} 多出={extra}")


def _analyses_of_case(db: str, case_id: str) -> list[dict]:
    rows = _rows(
        db,
        "SELECT a.Id, a.Name, a.SynthesisNumber, a.ComputationObject_Id, "
        "(SELECT COUNT(*) FROM dbo.WRAAnalysisElements e WHERE e.WRAAnalysis_Id=a.Id) "
        "FROM dbo.WRAAnalysis a "
        "JOIN dbo.ComputationObject c ON c.Id=a.ComputationObject_Id "
        "WHERE c.Parent_Id=" + _lit(case_id) + " AND c.Discriminator=" +
        _lit(DISC_WRA_OBJECT) + " ORDER BY a.SynthesisNumber;")
    return [{"id": r[0], "name": r[1], "number": int(r[2]), "co_id": r[3],
             "elements": int(r[4])} for r in rows]


def _pick_clone_source(db: str, case_id: str, case_name: str, spec: SynthesisSpec,
                       config_id: str, config_size: int) -> tuple[dict, Optional[mw.ImportIssue]]:
    analyses = _analyses_of_case(db, case_id)
    if not analyses:
        raise PreconditionError(f"工况 {case_name} 下没有任何既有综合，无法确定克隆源/编号基线：需至少先有 1 个基础综合")

    if spec.clone_from:
        hit = [a for a in analyses
               if a["name"] == spec.clone_from or str(a["number"]) == spec.clone_from
               or a["id"] == spec.clone_from]
        if not hit:
            names = ", ".join(a["name"] for a in analyses)
            raise LookupError(f"--clone-from {spec.clone_from} 不存在；可用: {names}")
        source = hit[0]
        # 覆盖检查：所选配置的每个元素必须被克隆源快照覆盖
        missing = _rows(
            db,
            "SELECT COUNT(*) FROM dbo.Configuration_Element ce "
            "WHERE ce.ConfigurationId=" + _lit(config_id) + " AND NOT EXISTS ("
            "SELECT 1 FROM dbo.WRAAnalysisElements e WHERE e.WRAAnalysis_Id=" +
            _lit(source["id"]) + " AND e.Element_Id=ce.ElementId);")
        if int(missing[0][0]) > 0:
            raise LookupError(
                f"克隆源 {source['name']} 的元素快照未覆盖所选配置 "
                f"（缺 {missing[0][0]} 个元素）；请换 --clone-from 或 --configuration")
        return source, None

    # 默认：找元素集与所选配置完全一致的既有综合（= GUI 同配置路径），取最小序号
    exact = []
    for a in analyses:
        both = _rows(
            db,
            "SELECT COUNT(*) FROM dbo.WRAAnalysisElements e "
            "WHERE e.WRAAnalysis_Id=" + _lit(a["id"]) +
            " AND EXISTS (SELECT 1 FROM dbo.Configuration_Element ce "
            "WHERE ce.ConfigurationId=" + _lit(config_id) + " AND ce.ElementId=e.Element_Id);")
        if int(both[0][0]) == config_size and a["elements"] == config_size:
            exact.append(a)
    if exact:
        return exact[0], None
    names = ", ".join(f"{a['name']}({a['elements']}元素)" for a in analyses)
    raise LookupError(
        f"没有元素集与配置（{config_size} 元素）一致的既有综合可作克隆源；"
        f"可用综合: {names}。请显式 --clone-from")


def _hex_text(token: str) -> Optional[str]:
    """sqlcmd hex 通道解码：'NULL' → None，否则 UTF-16LE hex → 文本。"""
    t = token.strip()
    if t.upper() == "NULL":
        return None
    return bytes.fromhex(t).decode("utf-16-le")


def _clone_literals(db: str, table: str, schema: dict[str, str], where: str,
                    overrides: dict[str, Any]) -> dict[str, str]:
    """读源行（文本列名→sqlcmd 文本），按列类型生成 INSERT 字面量。

    数值列（n）用 SQL Server 自己渲染的文本原样回写（如 9.4999999E-2）——回写值与
    源值逐位一致（侧审 S-P3-3：real/float 抽样 10 列全等，float(53) 全列未穷举，
    写后校验对浮点参数用 1e-6 容差兜底）。
    自由文本列（t，侧审 S-P2-3）经 hex 通道读取：CONVERT(varbinary) 后按 binary→
    nvarchar style 2 渲染 hex，绕开 sqlcmd '|' 列分隔与文本 'NULL' 歧义，Python 侧
    UTF-16LE 解码；任何分隔错位（token 数 ≠ 列数）一律 fail-closed。

    P1-A：hex 是「变长」列，sqlcmd 不带 -y 时按 256 个显示字符裁掉（-W 与 -y 互斥），
    过去一条 145 字符的 Description 会被静默裁成 64 字符照样落库并报 [OK]。现在：
      · 先问 MAX(DATALENGTH) 换算出 -y 宽度，再走定宽通道 read_sized_rows()；
      · 每个 t 列额外带回 DATALENGTH，解码前用 mw.hex_length_reason() 断言
        len(hex)==2×DATALENGTH，不符 ⇒ 抛错（调用方转成 fail-closed，绝不按残缺值克隆）；
      · 单列超过 -y 上限（8000 个显示字符 ⇒ 3996 字节）时 mw.var_column_width 直接抛错，
        这条通道读不完整就必须停，改走 .NET SqlClient 是后续工作，不是静默少读的理由。
    """
    hex_at: dict[str, int] = {}
    len_at: dict[str, int] = {}
    pos_at: dict[str, int] = {}
    cols: list[str] = []
    for name, kind in schema.items():
        pos_at[name] = len(cols)
        if kind != "t":
            cols.append(name)
            continue
        cols.append(f"CASE WHEN {name} IS NULL THEN N'NULL' "
                    f"ELSE CONVERT(nvarchar(max), CONVERT(varbinary(max), {name}), 2) END")
        hex_at[name] = pos_at[name]
        len_at[name] = len(cols)
        # NULL 用 -1 哨兵：hex_length_reason 先认哨兵，不会把 NULL 误判成裁剪。
        cols.append(f"CASE WHEN {name} IS NULL THEN -1 ELSE DATALENGTH({name}) END")
    sql = "SELECT " + ", ".join(cols) + " FROM dbo.{0} WHERE {1};".format(table, where)
    if hex_at:
        probe = ("SELECT ISNULL(MAX(v),0) FROM (" + " UNION ALL ".join(
            "SELECT DATALENGTH({0}) AS v FROM dbo.{1} WHERE {2}".format(col, table, where)
            for col in hex_at) + ") x(v);")
        rows = _sized_rows(db, sql, _var_width(db, probe))
    else:
        rows = _rows(db, sql)
    row = _single(rows, f"{table} 源行（{where}）", db)
    if len(row) != len(schema) + len(hex_at):
        raise LookupError(
            f"{table} 源行 token 数 {len(row)} ≠ 列数+长度列数 {len(schema) + len(hex_at)}"
            f"（文本含 '|' 或渲染异常，fail-closed；db={db}）")
    for col, idx in hex_at.items():
        reason = mw.hex_length_reason(row[idx], row[len_at[col]])
        if reason:
            raise LookupError(f"{table}.{col} 定宽读取不完整：{reason}（fail-closed，"
                              f"没有按残缺值克隆；where={where}）")
    out: dict[str, str] = {}
    for name, kind in schema.items():
        token = row[pos_at[name]]
        if name in overrides:
            out[name] = overrides[name]
        elif kind == "t":
            out[name] = _lit_or_null(_hex_text(token))
        elif kind == "s":
            out[name] = _lit_or_null(token)
        else:
            if token.strip().upper() == "NULL":
                out[name] = "NULL"
            else:
                # 数值/位：服务端渲染文本回写（int/bit/real/float 均为合法 TSQL 数值字面量）
                float(token)
                out[name] = token
    return out


def _clone_literals_result(db: str, table: str, schema: dict[str, str], where: str,
                           overrides: dict[str, Any]) -> tuple[Optional[dict[str, str]],
                                                               Optional[mw.ImportResult]]:
    """`_clone_literals` 的结果化封装：读不出/被裁剪 ⇒ fail-closed 负载，不让异常穿出 main()。

    build_plan 里这几处原本是裸调用：定宽通道的 RuntimeError（超过 -y 上限）、列错位/裁剪的
    LookupError、以及数值列 float() 的 ValueError 都会一路穿出到进程退出码以外，调用方拿不到
    「未写库」这个事实。统一转成 db_guard(4)：语义是「守卫拦下、库里没动」，不是 verify_failed。
    """
    try:
        return _clone_literals(db, table, schema, where, overrides), None
    except (LookupError, RuntimeError, ValueError) as exc:
        return None, mw.ImportResult(
            False, mw.ImportCode.DB_GUARD,
            "读取克隆源 %s 失败（fail-closed，未写库）: %s" % (table, exc))


# --- 计划 ----------------------------------------------------------------------

def build_plan(spec: SynthesisSpec, db: str) -> mw.ImportResult:
    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)

    # 0) 目标表列集合 fail-closed 校验
    for table, schema in (("ComputationObject", T_COMPUTATION_OBJECT),
                          ("WRAAnalysis", T_WRA_ANALYSIS),
                          ("WRAAnalysisClimatologyConfiguration", T_CLIMATO_CONFIG),
                          ("WRAAnalysis_ClimatologyConfiguration", T_LINK_CLIMATO),
                          ("WRAAnalysis_TemporalClimatology", T_LINK_TEMPORAL),
                          ("WRAAnalysis_CFDs", T_LINK_CFD),
                          ("WRAAnalysisElements", T_ELEMENTS),
                          ("WRAAnalysisComputationPointConfidenceMethod", T_CPM),
                          ("WRAAnalysisLargeParkConfiguration", T_LARGE_PARK),
                          ("WRAAnalysisParameters", T_PARAMETERS)):
        try:
            _check_columns(db, table, schema)
        except LookupError as exc:
            return mw.ImportResult(False, mw.ImportCode.DB_GUARD, str(exc))

    try:
        site_id, site_name = _resolve_site(db, spec.site)
        case_id, case_name, _ = _resolve_case(db, site_id, spec.case)
    except PreconditionError as exc:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION, str(exc))
    except LookupError as exc:
        return mw.ImportResult(False, mw.ImportCode.INPUT, str(exc))

    # 1) 风机配置（GUI「默认配置」= CaseElementConfiguration 行）
    cfg_rows = _rows(db, "SELECT Id, Name FROM dbo.Configurations WHERE Case_Id=" +
                     _lit(case_id) + " AND Name=" + _lit(spec.configuration) + ";")
    if not cfg_rows:
        avail = _rows(db, "SELECT Name FROM dbo.Configurations WHERE Case_Id=" +
                      _lit(case_id) + ";")
        names = ", ".join(r[0] for r in avail)
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"配置 {spec.configuration} 不存在；可用: {names}")
    if len(cfg_rows) > 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"配置名 {spec.configuration} 不唯一，请用 Id")
    config_id, config_name = cfg_rows[0]
    config_size = int(_rows(db, "SELECT COUNT(*) FROM dbo.Configuration_Element "
                            "WHERE ConfigurationId=" + _lit(config_id) + ";")[0][0])
    if config_size == 0:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "所选配置没有元素")

    # 2) 克隆源
    try:
        source, warn = _pick_clone_source(db, case_id, case_name, spec, config_id, config_size)
    except PreconditionError as exc:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION, str(exc))
    except LookupError as exc:
        return mw.ImportResult(False, mw.ImportCode.INPUT, str(exc))

    # 3) CFD 结果链（CFD结果选取）：Case 的 DASCFDObject（可选按热稳定度过滤）
    cfd_filter = ""
    if spec.stability is not None:
        cfd_filter = " AND cfd.Stability=" + _num(spec.stability)
    cfd_rows = _rows(
        db,
        "SELECT co.Id, cfd.Direction, cfd.Stability FROM dbo.ComputationObject co "
        "JOIN dbo.CFD cfd ON cfd.Id=co.CFD_Id WHERE co.Parent_Id=" + _lit(case_id) +
        " AND co.Discriminator=" + _lit(DISC_CFD_OBJECT) + cfd_filter +
        " ORDER BY cfd.Direction;")
    if not cfd_rows:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION,
                               "工况没有 CFD 计算结果（DASCFDObject）——新建综合必须先有 CFD 结果"
                               + (f"（Stability={spec.stability} 过滤后为空）" if spec.stability is not None else ""))
    cfd_ids = [r[0] for r in cfd_rows]

    # 4) 同名/同序号冲突
    new_number = max(a["number"] for a in _analyses_of_case(db, case_id)) + 1
    new_name = spec.name or f"综合{new_number}"
    dup = _rows(
        db,
        "SELECT COUNT(*) FROM dbo.WRAAnalysis a "
        "JOIN dbo.ComputationObject c ON c.Id=a.ComputationObject_Id "
        "WHERE c.Parent_Id=" + _lit(case_id) +
        " AND (a.Name=" + _lit(new_name) + " OR a.SynthesisNumber=" + _num(new_number) + ");")
    if int(dup[0][0]) > 0:
        return mw.ImportResult(False, mw.ImportCode.CONFLICT,
                               f"同名/同序号综合已存在: {new_name}#{new_number}（fail-closed）")

    # 5) 克隆源快照
    ref, err = _clone_literals_result(db, "WRAAnalysis", T_WRA_ANALYSIS,
                                      "Id=" + _lit(source["id"]), {})
    if err:
        return err
    # 表单外参数锚点（写后回读比对克隆未被覆盖列原样保留）
    ref_params = _rows(
        db, "SELECT Wake_AlphaMax, SpeedReductionModel, Weibull_MethodMapping "
            "FROM dbo.WRAAnalysisParameters WHERE WRAAnalysis_Id=" + _lit(source["id"]) + ";")
    if len(ref_params) != 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"克隆源 {source['name']} 参数行不唯一（fail-closed）")
    ref_anchor = {"Wake_AlphaMax": ref_params[0][0],
                  "SpeedReductionModel": ref_params[0][1],
                  "Weibull_MethodMapping": ref_params[0][2]}
    # 气象配置：克隆源的链接行（金样每综合恰 1 行）
    link_rows = _rows(db, "SELECT WRAAnalysisClimatologyConfiguration_Id "
                          "FROM dbo.WRAAnalysis_ClimatologyConfiguration WHERE WRAAnalysis_Id=" +
                      _lit(source["id"]) + ";")
    if len(link_rows) != 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"克隆源 {source['name']} 有 {len(link_rows)} 行气象配置链接"
                               "（多塔场景未支持，fail-closed；金样单塔=1）")
    ref_cc_id = link_rows[0][0]
    ref_cc, err = _clone_literals_result(db, "WRAAnalysisClimatologyConfiguration",
                                         T_CLIMATO_CONFIG, "Id=" + _lit(ref_cc_id), {})
    if err:
        return err
    ref_lp_rows = _rows(db, "SELECT Id FROM dbo.WRAAnalysisLargeParkConfiguration "
                            "WHERE WRAAnalysis_Id=" + _lit(source["id"]) + ";")
    if len(ref_lp_rows) != 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"克隆源 {source['name']} 有 {len(ref_lp_rows)} 行大型风电场配置"
                               "（金样恒 1 行，fail-closed）")
    ref_lp, err = _clone_literals_result(db, "WRAAnalysisLargeParkConfiguration", T_LARGE_PARK,
                                         "Id=" + _lit(ref_lp_rows[0][0]), {})
    if err:
        return err

    elements_rows = _rows(
        db,
        "SELECT Id, Discriminator, Element_Id, WindTurbineType_Id, SurfaceZone_Id "
        "FROM dbo.WRAAnalysisElements WHERE WRAAnalysis_Id=" + _lit(source["id"]) + ";")
    element_ids = [r[0] for r in elements_rows]

    cpm_rows = _rows(
        db,
        "SELECT Id, ComputationId, IsNearest, Zone_Id, WRAAnalysisElement_Id, IsRadius "
        "FROM dbo.WRAAnalysisComputationPointConfidenceMethod "
        "WHERE WRAAnalysisElement_Id IN (" + ",".join(_lit(i) for i in element_ids) + ");")
    # 表面区 CPM 行（Element_Id 为空）被 MappingConfiguration.SurfaceZone_Id 引用且跨综合共享：
    # 只引用不复制（金样综合1/3 共享同一行）。
    surface_zone_ids = [r[4] for r in elements_rows if r[4] not in ("NULL",)]
    surface_rows = []
    if surface_zone_ids:
        surface_rows = _rows(
            db, "SELECT Id FROM dbo.WRAAnalysisComputationPointConfidenceMethod WHERE Id IN (" +
            ",".join(_lit(i) for i in surface_zone_ids) + ");")
        if len(surface_rows) != len(set(surface_zone_ids)):
            return mw.ImportResult(False, mw.ImportCode.INPUT,
                                   "克隆源的表面区 CPM 行不完整（fail-closed）")
    cpm_sum = sum(int(r[1]) for r in cpm_rows)
    cpm_distinct = len({r[1] for r in cpm_rows})

    # 6) 新 Id 与覆盖
    analysis_id = str(uuid.uuid4())
    co_id = str(uuid.uuid4())
    cc_id = str(uuid.uuid4())
    param_id = str(uuid.uuid4())
    lp_id = str(uuid.uuid4())
    new_element_ids = {old: str(uuid.uuid4()) for old in element_ids}
    new_cpm_ids = {old: str(uuid.uuid4()) for old in (r[0] for r in cpm_rows)}

    overrides = {
        "Global_ComputeWindSpeedProfiles": _num(int(spec.compute_profiles)),
        "Global_ComputeWake": _num(int(spec.compute_wake)),
        "Global_ComputeCurtailment": _num(int(spec.compute_curtailment)),
        "Global_ComputeTemporalSynthesis": _num(int(spec.compute_temporal)),
    }
    params_overrides: dict[str, Any] = {}
    for col, lit in overrides.items():
        params_overrides[col] = lit
    params_overrides["Climatology_ReferenceAirDensity"] = _num(spec.density)
    params_overrides["Climatology_ReferenceAirDensityAltitude"] = _num(spec.altitude)
    params_overrides["Global_SectorStep"] = _num(spec.sector_step)
    if spec.compute_iec is not None:
        params_overrides["Global_ComputeIec"] = _num(int(spec.compute_iec))
    if spec.compute_extreme_wind is not None:
        params_overrides["Global_ComputeExtremeWind"] = _num(int(spec.compute_extreme_wind))
    # 克隆值 + 覆盖（WRAAnalysis_Id 覆盖为新分析）
    params_overrides["Id"] = _lit(param_id)
    params_overrides["WRAAnalysis_Id"] = _lit(analysis_id)
    params_lit, err = _clone_literals_result(db, "WRAAnalysisParameters", T_PARAMETERS,
                                             "WRAAnalysis_Id=" + _lit(source["id"]),
                                             params_overrides)
    if err:
        return err

    cc_overrides = {"Id": _lit(cc_id), "UseTurbulence": _num(int(spec.use_turbulence))}
    if spec.height is not None:
        cc_overrides["SelectedHeight"] = _num(spec.height)
    cc_lit, err = _clone_literals_result(db, "WRAAnalysisClimatologyConfiguration",
                                         T_CLIMATO_CONFIG, "Id=" + _lit(ref_cc_id), cc_overrides)
    if err:
        return err
    cc_lit["Id"] = _lit(cc_id)

    # 侧审 S-P1-1：默认原样克隆源行 ExtremeWindReference_Id（含 NULL/NOT NULL），
    # 需清空走 --clear-extreme-wind-reference；不再静默置 NULL。
    # P3-F：清空要成对 —— 生产只观测到 (_Id=NULL, PointId=0) 与 (_Id 非空, PointId=非0) 两种形状
    # （WRAAnalysis 5 行：2 行 NULL/0 + 3 行 1157 带点位），(NULL, 非0) 这种「半清空」从未出现，
    # 而 ExtremeWindReferencePointId 仍是非零点位时 GUI 是否忽略 _Id 属 unknown ⇒ 不造未观测形状。
    if spec.clear_extreme_wind_reference:
        ewr_ref_id, ewr_point_id = "NULL", _num(0)
    else:
        ewr_ref_id = ref["ExtremeWindReference_Id"]
        ewr_point_id = ref["ExtremeWindReferencePointId"]
    wra_lit = {
        "Id": _lit(analysis_id),
        "Description": _lit(spec.description),
        "Name": _lit(new_name),
        "SynthesisNumber": _num(new_number),
        "ExtremeWindReferencePointId": ewr_point_id,
        "ExtremeWindReference_Id": ewr_ref_id,
        "ReferenceClimatology_Id": _lit(cc_id),
        "ComputationObject_Id": _lit(co_id),
    }
    co_lit = {
        "Id": _lit(co_id), "Discriminator": _lit(DISC_WRA_OBJECT),
        "Micro_AtlasId": "NULL", "CFD_Id": "NULL", "Parent_Id": _lit(case_id),
    }
    lp_lit = dict(ref_lp)
    lp_lit["Id"] = _lit(lp_id)
    lp_lit["WRAAnalysis_Id"] = _lit(analysis_id)

    # 7) INSERT 语句（FK 安全序）+ 回滚（逆序）
    stmts: list[str] = []
    rollback: list[str] = []
    a_lit, cc_link_a = _lit(analysis_id), _lit(analysis_id)

    def _insert(table: str, cols: dict[str, str]) -> str:
        names = ", ".join(cols)
        vals = ", ".join(cols[c] for c in cols)
        return f"INSERT INTO dbo.{table} ({names}) VALUES ({vals});"

    stmts.append(_insert("ComputationObject", co_lit))
    stmts.append(_insert("WRAAnalysisClimatologyConfiguration", cc_lit))
    stmts.append(_insert("WRAAnalysis", wra_lit))
    stmts.append(_insert("WRAAnalysis_ClimatologyConfiguration",
                         {"WRAAnalysis_Id": a_lit,
                          "WRAAnalysisClimatologyConfiguration_Id": _lit(cc_id)}))
    stmts.append(_insert("WRAAnalysisParameters", params_lit))
    # 元素先于计算点：CPM.WRAAnalysisElement_Id → WRAAnalysisElements（FK）；
    # 表面区 CPM 行（Element_Id 为空，被 MappingConfiguration.SurfaceZone_Id 引用）
    # 跨综合共享、只引用不复制，故无"表面区先于元素"的循环。
    for r in elements_rows:
        stmts.append(_insert("WRAAnalysisElements", {
            "Id": _lit(new_element_ids[r[0]]), "Discriminator": _lit_or_null(r[1]),
            "Element_Id": _lit_or_null(r[2]), "WindTurbineType_Id": _lit_or_null(r[3]),
            "WRAAnalysis_Id": a_lit, "SurfaceZone_Id": _lit_or_null(r[4]),
        }))
    for old, new in new_cpm_ids.items():
        src = next(r for r in cpm_rows if r[0] == old)
        stmts.append(_insert("WRAAnalysisComputationPointConfidenceMethod", {
            "Id": _lit(new), "ComputationId": _num(int(src[1])),
            "IsNearest": _num(int(src[2])), "Zone_Id": _lit_or_null(src[3]),
            "WRAAnalysisElement_Id": _lit(new_element_ids[src[4]]),
            "IsRadius": _num(int(src[5])),
        }))
    for cfd in cfd_ids:
        stmts.append(_insert("WRAAnalysis_CFDs",
                             {"WRAAnalysis_Id": a_lit, "CFD_Id": _lit(cfd)}))
    stmts.append(_insert("WRAAnalysisLargeParkConfiguration", lp_lit))
    if spec.compute_temporal:
        stmts.append(_insert("WRAAnalysis_TemporalClimatology",
                             {"WRAAnalysis_Id": cc_link_a,
                              "WRAAnalysisClimatologyConfiguration_Id": _lit(cc_id)}))

    del_cpm = ("DELETE FROM dbo.WRAAnalysisComputationPointConfidenceMethod "
               "WHERE WRAAnalysisElement_Id IN (" +
               ",".join(_lit(i) for i in new_element_ids.values()) + ");")
    del_elements = ("DELETE FROM dbo.WRAAnalysisElements WHERE WRAAnalysis_Id=" +
                    a_lit + ";")
    rollback.extend([
        f"DELETE FROM dbo.WRAAnalysis_TemporalClimatology WHERE WRAAnalysis_Id={a_lit};",
        f"DELETE FROM dbo.WRAAnalysisLargeParkConfiguration WHERE WRAAnalysis_Id={a_lit};",
        f"DELETE FROM dbo.WRAAnalysis_CFDs WHERE WRAAnalysis_Id={a_lit};",
        del_cpm,
        del_elements,
        f"DELETE FROM dbo.WRAAnalysisParameters WHERE WRAAnalysis_Id={a_lit};",
        f"DELETE FROM dbo.WRAAnalysis_ClimatologyConfiguration WHERE WRAAnalysis_Id={a_lit};",
        f"DELETE FROM dbo.WRAAnalysis WHERE Id={a_lit};",
        f"DELETE FROM dbo.WRAAnalysisClimatologyConfiguration WHERE Id={_lit(cc_id)};",
        f"DELETE FROM dbo.ComputationObject WHERE Id={_lit(co_id)};",
    ])

    sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n" + "\n".join(stmts)
           + "\nCOMMIT;\n")
    rollback_sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n"
                    + "\n".join(rollback) + "\nCOMMIT;\n")

    plan = WritePlan(
        spec=spec, db=db, site_id=site_id, site_name=site_name, case_id=case_id,
        case_name=case_name, analysis_id=analysis_id, computation_object_id=co_id,
        climato_config_id=cc_id, name=new_name, synthesis_number=new_number,
        clone_from_name=source["name"], clone_from_id=source["id"],
        element_count=len(element_ids), cpm_count=len(cpm_rows),
        cpm_insert_count=len(new_cpm_ids), cfd_count=len(cfd_ids),
        temporal=bool(spec.compute_temporal),
        clear_extreme_wind_reference=bool(spec.clear_extreme_wind_reference),
        overrides={
            "density": spec.density, "altitude": spec.altitude,
            "sector_step": spec.sector_step, "use_turbulence": spec.use_turbulence,
            "compute_profiles": spec.compute_profiles, "compute_wake": spec.compute_wake,
            "compute_curtailment": spec.compute_curtailment,
            "compute_temporal": spec.compute_temporal,
            "compute_iec": spec.compute_iec, "compute_extreme_wind": spec.compute_extreme_wind,
            "height": spec.height,
            "reference_param_wake": ref_anchor,
        },
        sql=sql, rollback_sql=rollback_sql)
    data = {"plan_obj": plan, "plan": plan.to_dict(),
            "cfd_sectors": [{"id": r[0], "direction": int(r[1]), "stability": int(r[2])}
                            for r in cfd_rows],
            "warnings": [warn.to_dict()] if warn else []}
    return mw.ImportResult(True, mw.ImportCode.OK, "写入计划已生成", data=data)


# --- 写后校验 ------------------------------------------------------------------

def _verify(db: str, plan: WritePlan) -> mw.ImportResult:
    a = _lit(plan.analysis_id)
    checks: dict[str, bool] = {}
    try:
        counts = _rows(db, (
            "SELECT "
            f"(SELECT COUNT(*) FROM dbo.ComputationObject WHERE Id={_lit(plan.computation_object_id)}"
            f" AND Discriminator={_lit(DISC_WRA_OBJECT)} AND Parent_Id={_lit(plan.case_id)}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysis WHERE Id={a} AND Name={_lit(plan.name)}"
            f" AND SynthesisNumber={plan.synthesis_number}"
            f" AND ReferenceClimatology_Id={_lit(plan.climato_config_id)}"
            f" AND ComputationObject_Id={_lit(plan.computation_object_id)}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysisParameters WHERE WRAAnalysis_Id={a}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysisClimatologyConfiguration WHERE Id={_lit(plan.climato_config_id)}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysis_ClimatologyConfiguration WHERE WRAAnalysis_Id={a}"
            f" AND WRAAnalysisClimatologyConfiguration_Id={_lit(plan.climato_config_id)}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysisElements WHERE WRAAnalysis_Id={a}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysisComputationPointConfidenceMethod"
            f" WHERE WRAAnalysisElement_Id IN (SELECT Id FROM dbo.WRAAnalysisElements"
            f" WHERE WRAAnalysis_Id={a})),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysis_CFDs WHERE WRAAnalysis_Id={a}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysisLargeParkConfiguration WHERE WRAAnalysis_Id={a}),"
            f"(SELECT COUNT(*) FROM dbo.WRAAnalysis_TemporalClimatology WHERE WRAAnalysis_Id={a});"))
        expected = [1, 1, 1, 1, 1, plan.element_count, plan.cpm_insert_count,
                    plan.cfd_count, 1, 1 if plan.temporal else 0]
        got = [int(v) for v in counts[0]]
        checks["counts"] = got == expected

        # CFD 链接必须是工况现役 DASCFDObject
        cfd_ok = _rows(db, (
            f"SELECT COUNT(*) FROM dbo.WRAAnalysis_CFDs r WHERE r.WRAAnalysis_Id={a} "
            "AND EXISTS (SELECT 1 FROM dbo.ComputationObject co JOIN dbo.CFD cfd "
            "ON cfd.Id=co.CFD_Id WHERE co.Id=r.CFD_Id AND co.Parent_Id=" +
            _lit(plan.case_id) + " AND co.Discriminator=" + _lit(DISC_CFD_OBJECT) + ");"))
        checks["cfds_resolve"] = int(cfd_ok[0][0]) == plan.cfd_count

        # CPM 计算点键与克隆源逐点一致（同一物理元素同键）
        cpm_ok = _rows(db, (
            "SELECT ISNULL(SUM(ComputationId),0), COUNT(DISTINCT ComputationId) "
            "FROM dbo.WRAAnalysisComputationPointConfidenceMethod "
            "WHERE WRAAnalysisElement_Id IN (SELECT Id FROM dbo.WRAAnalysisElements "
            f"WHERE WRAAnalysis_Id={a});"))
        src_ok = _rows(db, (
            "SELECT ISNULL(SUM(ComputationId),0), COUNT(DISTINCT ComputationId) "
            "FROM dbo.WRAAnalysisComputationPointConfidenceMethod "
            "WHERE WRAAnalysisElement_Id IN (SELECT Id FROM dbo.WRAAnalysisElements "
            f"WHERE WRAAnalysis_Id={_lit(plan.clone_from_id)});"))
        checks["cpm_ids_match_source"] = (cpm_ok[0][0] == src_ok[0][0]
                                          and cpm_ok[0][1] == src_ok[0][1])

        # 元素集 = 克隆源元素集
        el_ok = _rows(db, (
            "SELECT COUNT(*) FROM dbo.WRAAnalysisElements e WHERE e.WRAAnalysis_Id=" + a +
            " AND EXISTS (SELECT 1 FROM dbo.WRAAnalysisElements s "
            "WHERE s.WRAAnalysis_Id=" + _lit(plan.clone_from_id) +
            " AND s.Element_Id=e.Element_Id AND s.Discriminator=e.Discriminator);"))
        checks["elements_match_source"] = int(el_ok[0][0]) == plan.element_count

        # 气象配置逐列（覆盖项 + 源项）
        cc = _rows(db, (
            "SELECT UseTurbulence, SelectedHeight, Climatology_Id, Element_Id, ComputationId "
            "FROM dbo.WRAAnalysisClimatologyConfiguration WHERE Id=" +
            _lit(plan.climato_config_id) + ";"))
        cc_src = _rows(db, (
            "SELECT UseTurbulence, SelectedHeight, Climatology_Id, Element_Id, ComputationId "
            "FROM dbo.WRAAnalysisClimatologyConfiguration "
            "WHERE Id=(SELECT WRAAnalysisClimatologyConfiguration_Id "
            "FROM dbo.WRAAnalysis_ClimatologyConfiguration WHERE WRAAnalysis_Id=" +
            _lit(plan.clone_from_id) + ");"))
        checks["climato_config"] = bool(cc and cc_src and
                                        cc[0][2] == cc_src[0][2] and cc[0][3] == cc_src[0][3]
                                        and cc[0][4] == cc_src[0][4]
                                        and int(cc[0][0]) == int(bool(plan.spec.use_turbulence))
                                        and (plan.spec.height is not None
                                             or cc[0][1] == cc_src[0][1]))

        # 参数：覆盖项 + 表单外克隆锚点 + 克隆源整体列级抽查
        pr = _rows(db, (
            "SELECT Climatology_ReferenceAirDensity, Climatology_ReferenceAirDensityAltitude, "
            "Global_SectorStep, Global_ComputeWindSpeedProfiles, Global_ComputeWake, "
            "Global_ComputeCurtailment, Global_ComputeTemporalSynthesis, Wake_AlphaMax, "
            "SpeedReductionModel, Weibull_MethodMapping FROM dbo.WRAAnalysisParameters "
            f"WHERE WRAAnalysis_Id={a};"))
        o = plan.spec
        anchor = plan.overrides["reference_param_wake"]
        # S-P3-3：real(24) 存储误差 <1e-7（sqlcmd 以 17 位有效数字渲染），浮点参数用
        # 容差比对；标志位/整数列仍取整严格相等。
        checks["params"] = bool(pr) and (
            abs(float(pr[0][0]) - float(o.density)) < 1e-6
            and abs(float(pr[0][1]) - float(o.altitude)) < 1e-6
            and abs(float(pr[0][2]) - float(o.sector_step)) < 1e-6
            and int(pr[0][3]) == int(o.compute_profiles)
            and int(pr[0][4]) == int(o.compute_wake)
            and int(pr[0][5]) == int(o.compute_curtailment)
            and int(pr[0][6]) == int(o.compute_temporal)
            and pr[0][7] == anchor["Wake_AlphaMax"]
            and pr[0][8] == anchor["SpeedReductionModel"]
            and pr[0][9] == anchor["Weibull_MethodMapping"])

        # 极风参考（侧审 S-P1-1 + P3-F）：默认两列与克隆源一致；显式清空时两列成对
        # (_Id=NULL, PointId=0)。生产只有 (NULL,0) 与 (非空,非0) 两种形状，半清空
        # （_Id=NULL 但 PointId 仍是非零点位）是生产未观测形状 ⇒ 必须当成校验失败。
        ew = _rows(db, (
            "SELECT ExtremeWindReferencePointId, ExtremeWindReference_Id "
            f"FROM dbo.WRAAnalysis WHERE Id={a};"))
        ew_src = _rows(db, (
            "SELECT ExtremeWindReferencePointId, ExtremeWindReference_Id "
            f"FROM dbo.WRAAnalysis WHERE Id={_lit(plan.clone_from_id)};"))
        if plan.clear_extreme_wind_reference:
            checks["extreme_wind_reference"] = bool(ew) and ew[0][1].strip().upper() == "NULL"
            checks["extreme_wind_reference_point_zeroed"] = bool(ew) and _is_zero_number(ew[0][0])
        else:
            checks["extreme_wind_reference"] = bool(ew and ew_src) and (
                ew[0][1] == ew_src[0][1] and ew[0][0] == ew_src[0][0])

        # 编号基线仍成立（并发保护）
        num_ok = _rows(db, (
            "SELECT COUNT(*) FROM dbo.WRAAnalysis a JOIN dbo.ComputationObject c "
            "ON c.Id=a.ComputationObject_Id WHERE c.Parent_Id=" + _lit(plan.case_id) +
            " AND a.SynthesisNumber=" + _num(plan.synthesis_number) +
            " AND a.Id<>" + a + ";"))
        checks["number_unique"] = int(num_ok[0][0]) == 0
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"写后校验查询失败: {exc}")

    bad = [k for k, v in checks.items() if not v]
    if bad:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验不一致: " + ", ".join(sorted(bad)),
                               data={"failed": bad, "checks": checks})
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过",
                           data={"checks": checks})


def apply_plan(db: str, plan: WritePlan) -> mw.ImportResult:
    if mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库")
    if not apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    ok, out, err = mw._run_sql(db, plan.sql, timeout=600)  # 1755 行级 INSERT，LocalDB 冷启可能 >120s
    if not ok:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, f"写库失败: {err or out}")
    verified = _verify(db, plan)
    if not verified.ok:
        # S-P2-5：校验失败说明已提交的事务内容与计划漂移，自动回滚并把结果写进负载。
        rb_ok, rb_out, rb_err = mw._run_sql(db, plan.rollback_sql)
        data = dict(verified.data or {})
        data["rollback"] = {"executed": True, "ok": bool(rb_ok),
                            "output": (rb_out or "")[-2000:], "error": rb_err}
        data["verify_message"] = verified.message
        detail = "（%s）" % verified.message if verified.message else ""
        if rb_ok:
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "写后校验失败%s，已自动回滚" % detail, data=data)
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验失败%s且自动回滚失败（需人工介入）" % detail, data=data)
    return verified


# --- 编排 ----------------------------------------------------------------------

def run(spec: SynthesisSpec, db: str, apply: bool = False,
        out_dir: Optional[Path] = None) -> mw.ImportResult:
    # 生产库一律拒绝（无放行开关，侧审 S-P2-1）；公开 API 与 CLI 走同一守卫。
    if apply and mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库")
    if apply and not apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")

    # S-P3-1：默认产物目录改系统临时目录，不再落仓库工作区。
    work = Path(out_dir or (Path(tempfile.gettempdir()) / "mup_synthesis_import"))
    work.mkdir(parents=True, exist_ok=True)

    planned = build_plan(spec, db)
    if not planned.ok:
        return planned
    plan: WritePlan = planned.data["plan_obj"]

    (work / "plan.json").write_text(json.dumps(planned.data["plan"], ensure_ascii=False,
                                               indent=2, default=str), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库）",
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "cfd_sectors": planned.data["cfd_sectors"],
                                     "warnings": planned.data.get("warnings")})

    applied = apply_plan(db, plan)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message, data={
            "plan": plan.to_dict(), "rollback": str(work / "rollback.sql"),
            "out_dir": str(work), "verify": applied.data,
            "cfd_sectors": planned.data["cfd_sectors"],
            "warnings": planned.data.get("warnings")})
    return mw.ImportResult(True, mw.ImportCode.OK, "写入并校验通过", data={
        "plan": plan.to_dict(), "verify": applied.data, "out_dir": str(work),
        "cfd_sectors": planned.data["cfd_sectors"],
        "warnings": planned.data.get("warnings")})


# --- CLI ----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="综合(WRA Analysis)对象数据层落库：克隆既有综合元素/计算点快照，"
                    "接既有 CFD 结果，只建对象不触发计算")
    p.add_argument("--site", required=True, help="站点 Name 或 Id（如 DEMO）")
    p.add_argument("--case", help="工况 Name 或 Id（站点仅 1 个工况时可省）")
    p.add_argument("--name", help="综合名称（默认 综合{max+1}）")
    p.add_argument("--description", help="描述（默认 NULL，金样口径）")
    p.add_argument("--clone-from", help="克隆源综合（名称/序号/Id；默认选元素集与配置一致的）")
    p.add_argument("--configuration", default=DEFAULT_CONFIGURATION,
                   help=f"风机配置名（默认 {DEFAULT_CONFIGURATION}=GUI「默认配置」）")
    p.add_argument("--stability", type=int, help="CFD 热稳定度过滤（缺省链接全部扇区）")
    p.add_argument("--density", type=float, default=DEFAULT_DENSITY,
                   help=f"空气密度 kg/m3（默认 {DEFAULT_DENSITY}，综合6 表单口径）")
    p.add_argument("--altitude", type=float, default=DEFAULT_ALTITUDE,
                   help=f"参考海拔 m（默认 {DEFAULT_ALTITUDE}）")
    p.add_argument("--sector-step", type=float, default=DEFAULT_SECTOR_STEP,
                   help=f"分离区输出（度，默认 {DEFAULT_SECTOR_STEP}）")
    p.add_argument("--height", type=float, help="气象参考高度 m（默认克隆源值）")
    p.add_argument("--use-turbulence", action="store_true", help="使用湍流数据（默认不勾）")
    p.add_argument("--compute-profiles", action="store_true",
                   help="对所有风机点计算风廓线（默认不勾）")
    p.add_argument("--compute-wake", action="store_true", help="计算尾流效应（默认不勾）")
    p.add_argument("--compute-curtailment", action="store_true", help="计算限功率（默认不勾）")
    p.add_argument("--compute-temporal", action="store_true",
                   help="时间序列综合（默认不勾；勾选才写 TemporalClimatology 链接行）")
    p.add_argument("--compute-iec", type=int, choices=(0, 1), default=None,
                   help="计算 IEC（缺省沿用克隆源）")
    p.add_argument("--compute-extreme-wind", type=int, choices=(0, 1), default=None,
                   help="计算极风（缺省沿用克隆源）")
    p.add_argument("--clear-extreme-wind-reference", dest="clear_extreme_wind_reference",
                   action="store_true",
                   help="清空 ExtremeWindReference_Id（默认原样克隆源行该列，含 NULL/NOT NULL）")
    p.add_argument("--db", required=True, help="目标数据库名（生产库 MUPLocalDb 一律拒绝）")
    p.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    spec = SynthesisSpec(
        site=args.site, case=args.case, name=args.name, description=args.description,
        clone_from=args.clone_from, configuration=args.configuration,
        stability=args.stability, density=args.density, altitude=args.altitude,
        sector_step=args.sector_step, use_turbulence=args.use_turbulence,
        compute_profiles=args.compute_profiles, compute_wake=args.compute_wake,
        compute_curtailment=args.compute_curtailment,
        compute_temporal=args.compute_temporal,
        compute_iec=None if args.compute_iec is None else bool(args.compute_iec),
        compute_extreme_wind=None if args.compute_extreme_wind is None
        else bool(args.compute_extreme_wind),
        height=args.height,
        clear_extreme_wind_reference=args.clear_extreme_wind_reference)
    result = run(spec, args.db, apply=args.apply,
                 out_dir=Path(args.out_dir) if args.out_dir else None)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        plan = result.data.get("plan")
        if plan:
            print(f"  analysis_id={plan.get('analysis_id', '')}")
            print(f"  name={plan.get('name', '')} number={plan.get('synthesis_number', '')}")
            print(f"  clone_from={plan.get('clone_from_name', '')}"
                  f" elements={plan.get('element_count', '')}"
                  f" cpm={plan.get('cpm_insert_count', '')}/{plan.get('cpm_count', '')}"
                  f" cfds={plan.get('cfd_count', '')}")
        for w in result.data.get("warnings") or []:
            print("  warn: " + w.get("message", ""))
        for issue in result.issues:
            print("  issue: " + issue.code + " " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
