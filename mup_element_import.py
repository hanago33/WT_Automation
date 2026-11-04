# encoding: utf-8
"""数据层写入 MUP 站点元素（测风点/风机/结果点）并挂载到配置。

背景（2026-11-04 生产库 (localdb)\\Meteodyn / MUPLocalDb 只读侦察，
见 99_synthesis/2026-11-04-element-datalayer-zcode.md）
----
· dbo.Elements 按 Discriminator 分型（TPH 单表），153 行实测值域只有 3 个：
    MTDElementMast        测风点 [Observed]：Point_X/Y/HZ + Point_HZCoordType=1，无 VertProfiles。
    MTDElementWindTurbine 风机   [Observed]：同上 + WT_Diameter=0.0/WT_PosRes=0（全库 147 行皆如此）
                                + 恰 1 行 dbo.VertProfiles 风廓线。占位值主导金样 = 144/147 行
                                HMin=20/HMax=200/Step=20（轮毂+10 档，综合侧计算点 ComputationId
                                同口径）；test 站 JWD 3 行 0/0/0 是少数态（侧审 E-P1-2）。
                                默认写 20/200/20，--vp-hmin/--vp-hmax/--vp-step 可覆盖。
    MTDElementMapping     绘图   [Observed]：Quad_* 几何（GUI 资源 MBAElementEnum_Mapping=绘图）。
                          本脚本不写它 —— 归 mup_mapping_import.py（Quad 金样已逐字段解码）。
· 结果点（GUI 资源 MBAElementEnum_Point=结果点 [Observed]）对应 C# 类
  MTD.Site.Model.MTDElementPoint（MTDSiteModel.dll TypeDef，继承 PointBase→ElementObject；
  MUPXmlMigrations.dll 亦含独立字符串）—— 生产库 0 行（尚未有人建过）。
  判别值按 EF6 TPH「判别值=类名」惯例取 'MTDElementPoint' [Inferred]；该惯例对全部 3 个已观测
  判别值与 MUPSiteObject 成立。同族类型另有 MTDElementVerticalProfile=风廓线（VP_* 列）、
  MTDElementComputationPoint（elementId+subsetKey+ComputationId 包装器，非 Elements 行）。
· dbo.Elements.Parent_Id → dbo.Sites.Id（ON DELETE CASCADE）；元素不直接挂 Case。
· dbo.Configuration_Element(ConfigurationId, ElementId) 把元素挂到 Configurations；
  默认配置 Name=CaseElementConfiguration（mup_site_import.py 创建）。
· 风机元素不携带机型引用（全库无 Elements→WTType* 外键/列）：机型在 WRA 分析设置时经
  WRAAnalysisElements.WindTurbineType_Id → WRAWTType（分析级快照）落库。
  --turbine 的 wtg 参数仅做目标库存在性校验（WTType.TurbineModel / WTTypeVersion.Name）。
· test 站点全部元素 PersistentComputationModel=NULL、ElementColor=NULL 仍正常显示 —— 写入路径无需这些列。

设计
----
· 默认 dry-run；--apply 显式写库；目标库必须命中副本白名单 *_verify/_test/_sandbox，
  生产库 MUPLocalDb 一律拒绝（复用 mup_weather_import 闸门）。
· 结果点判别值 MTDElementPoint 属 inference（EF6 判别值=类名惯例 + 类型层次观测，
  未经 MUP GUI 打开站点验收）：--result 必须搭配 --allow-unverified-result-point 才放行
  （风格同 mup_cfd_import 的 --allow-unverified-atlas，侧审 E-P1-1）；plan.json 按
  discriminator_evidence 记录各判别值的证据分级。
· 单事务（XACT_ABORT）+ rollback.sql（Configuration_Element → VertProfiles → Elements 逆序）
  + 写后校验（逐元素判别值/Label/坐标/State/Parent_Id/配置挂载数/VP 行数与 HMin/HMax/Step）。
· 写后校验失败时自动执行 rollback.sql 并把回滚结果写进返回负载（侧审 E-P2-2：回滚成功
  = 失败态可解释；回滚失败 = 升级为需人工介入）。
· 同站点 (Discriminator, Label) 重复 → 拒（CONFLICT）；批内同型重名 → 拒（INPUT）。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import mup_weather_import as mw

__all__ = [
    "ElementSpec", "ElementPlan", "parse_element_arg", "build_plan", "apply_plan",
    "run", "main",
]

DISC_MAST = "MTDElementMast"
DISC_TURBINE = "MTDElementWindTurbine"
DISC_RESULT = "MTDElementPoint"   # 结果点：GUI-zh MBAElementEnum_Point + TypeDef 观测；EF6 判别值=类名
DISC_BY_KIND = {"mast": DISC_MAST, "turbine": DISC_TURBINE, "result": DISC_RESULT}
DEFAULT_CASE_NAME = "WT"
DEFAULT_CONFIG_NAME = "CaseElementConfiguration"

_KIND_LABEL = {"mast": "测风点", "turbine": "风机", "result": "结果点"}


# --- 输入模型 ---------------------------------------------------------------
@dataclass
class ElementSpec:
    kind: str            # mast | turbine | result
    label: str
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    wtg: Optional[str] = None   # 仅 turbine：仅校验目标库存在性，不写入元素

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if self.kind not in DISC_BY_KIND:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, f"未知元素类型: {self.kind}"))
        if not str(self.label or "").strip():
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, f"{_KIND_LABEL.get(self.kind, self.kind)} 名称不能为空"))
        if self.kind in ("mast", "turbine", "result") and (
                self.x is None or self.y is None or self.z is None):
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT,
                                         f"{self.label}: {self.kind} 必须提供 x,y,z 坐标"))
        return issues


def parse_element_arg(kind: str, text: str) -> tuple[Optional[ElementSpec], Optional[str]]:
    """解析 'label,x,y,z[,wtg]'；返回 (spec, error)。"""
    parts = [p.strip() for p in str(text).split(",")]
    label = parts[0] if parts else ""
    if kind == "mast":
        if len(parts) != 4:
            return None, f"--mast 需要 label,x,y,z 共 4 段（收到 {len(parts)} 段: {text}）"
        wtg = None
    elif kind == "turbine":
        if len(parts) not in (4, 5):
            return None, f"--turbine 需要 label,x,y,z[,wtg] 共 4-5 段（收到 {len(parts)} 段: {text}）"
        wtg = parts[4] if len(parts) == 5 else None
    elif kind == "result":
        if len(parts) != 4:
            return None, f"--result 需要 label,x,y,z 共 4 段（收到 {len(parts)} 段: {text}）"
        wtg = None
    else:
        return None, f"未知元素类型: {kind}"
    spec = ElementSpec(kind=kind, label=label, wtg=wtg)
    if len(parts) >= 4:
        try:
            spec.x, spec.y, spec.z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError as exc:
            return None, f"{label}: 坐标解析失败（{exc}）"
    return spec, None


# --- 工具 -------------------------------------------------------------------
def _num(value: float) -> str:
    # repr(float) 是 Python 最短往返表示，SQL float 可直接解析。
    return repr(float(value))


def _coord_or_null(value: Optional[float]) -> str:
    return _num(value) if value is not None else "NULL"


# --- 解析站点/案例/配置 -------------------------------------------------------
def _resolve_site_and_case(db: str, site: Optional[str], case: str
                           ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """返回 (site_id, site_name, case_id, error)。"""
    lit = mw._sql_literal
    if site:
        try:
            rows = mw._query_rows(db, f"SELECT Id, Name FROM dbo.Sites WHERE Name={lit(site)};")
        except Exception as exc:
            return None, None, None, f"无法读取目标库: {exc}"
        if not rows:
            return None, None, None, f"站点不存在: {site}"
        if len(rows) > 1:
            return None, None, None, f"站点名不唯一: {site}（{len(rows)} 行）"
        site_id, site_name = rows[0][0], rows[0][1]
        try:
            crows = mw._query_rows(db, f"SELECT Id FROM dbo.Cases WHERE Parent_Id={lit(site_id)} "
                                       f"AND Name={lit(case)};")
            if not crows:
                names = mw._query_rows(db, f"SELECT Name FROM dbo.Cases WHERE Parent_Id={lit(site_id)};")
                avail = "、".join(str(r[0]) for r in names) or "(无)"
                return None, None, None, f"站点 {site} 下无计算案例 {case}（可用: {avail}）"
        except Exception as exc:
            return None, None, None, f"无法读取目标库: {exc}"
        return site_id, site_name, crows[0][0], None
    try:
        rows = mw._query_rows(db, "SELECT c.Id, c.Parent_Id, s.Name FROM dbo.Cases c "
                                  f"JOIN dbo.Sites s ON c.Parent_Id=s.Id WHERE c.Name={lit(case)};")
    except Exception as exc:
        return None, None, None, f"无法读取目标库: {exc}"
    if not rows:
        return None, None, None, f"计算案例不存在: {case}"
    if len(rows) > 1:
        sites = "、".join(str(r[2]) for r in rows)
        return None, None, None, f"案例名 {case} 在多个站点下存在（{sites}）；请加 --site 指定站点"
    return rows[0][1], rows[0][2], rows[0][0], None


def _resolve_config(db: str, case_id: str, config_name: str
                    ) -> tuple[Optional[str], Optional[str]]:
    lit = mw._sql_literal
    try:
        rows = mw._query_rows(db, f"SELECT Id FROM dbo.Configurations WHERE Case_Id={lit(case_id)} "
                                  f"AND Name={lit(config_name)};")
        if not rows:
            names = mw._query_rows(db, f"SELECT Name FROM dbo.Configurations WHERE Case_Id={lit(case_id)};")
            avail = "、".join(str(r[0]) for r in names) or "(无)"
            return None, f"配置不存在: {config_name}（该案例可用: {avail}）"
    except Exception as exc:
        return None, f"无法读取目标库: {exc}"
    return rows[0][0], None


def _check_wtg(db: str, wtg: str) -> tuple[Optional[dict], Optional[str]]:
    """元素不携带机型引用；此处仅校验目标库存在该机型。"""
    lit = mw._sql_literal
    try:
        rows = mw._query_rows(db, f"SELECT Id FROM dbo.WTType WHERE TurbineModel={lit(wtg)};")
        if rows:
            return {"matched_table": "WTType", "matched_id": rows[0][0], "query": wtg}, None
        rows = mw._query_rows(db, f"SELECT Id FROM dbo.WTTypeVersion WHERE Name={lit(wtg)};")
        if rows:
            return {"matched_table": "WTTypeVersion", "matched_id": rows[0][0], "query": wtg}, None
    except Exception as exc:
        return None, f"无法读取目标库: {exc}"
    return None, (f"目标库不存在风机机型 {wtg}（元素本身不携带机型引用，"
                  f"机型如需入请先运行 mup_turbine_import.py）")


# --- 计划 -------------------------------------------------------------------
@dataclass
class ElementPlan:
    site_id: str
    site_name: str
    case_id: str
    config_id: str
    config_name: str
    elements: list[dict] = field(default_factory=list)
    sql: str = ""
    rollback_sql: str = ""

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id, "site_name": self.site_name,
            "case_id": self.case_id, "config_id": self.config_id,
            "config_name": self.config_name,
            "elements": [{k: v for k, v in e.items() if k != "spec"} for e in self.elements],
        }


def _insert_element(elem: dict, site_id: str, referential: Optional[str]) -> str:
    kind = elem["kind"]
    # 三型金样/惯例：点型元素（mast/turbine/result）坐标齐全且 Point_HZCoordType=1；
    # Quad_* 只属于 MTDElementMapping（绘图，mup_mapping_import.py），此处恒 NULL。
    hzct = "1"
    quadct = "NULL"
    if kind == "turbine":
        diam, posres = "0.0", "0"   # 生产全库 147 行皆 0/0
    else:
        diam, posres = "NULL", "NULL"
    return (
        "INSERT INTO dbo.Elements (Id,Label,ReferentialFileName,IsFromPostCFD,State,"
        "Point_X,Point_Y,Point_HZ,Point_HZCoordType,Quad_HZCoordType,WT_Diameter,WT_PosRes,"
        "Discriminator,Parent_Id) VALUES ("
        f"{mw._sql_literal(elem['id'])},{mw._sql_literal(elem['label'])},"
        f"{mw._sql_literal(referential)},0,0,"
        f"{_coord_or_null(elem['x'])},{_coord_or_null(elem['y'])},{_coord_or_null(elem['z'])},"
        f"{hzct},{quadct},{diam},{posres},"
        f"{mw._sql_literal(DISC_BY_KIND[kind])},{mw._sql_literal(site_id)});"
    )


def build_plan(specs: list[ElementSpec], db: str, site: Optional[str] = None,
               case: str = DEFAULT_CASE_NAME, config_name: str = DEFAULT_CONFIG_NAME,
               referential: Optional[str] = None,
               vp_hmin: float = 20.0, vp_hmax: float = 200.0, vp_step: float = 20.0,
               allow_unverified_result: bool = False) -> mw.ImportResult:
    issues: list[mw.ImportIssue] = []
    for spec in specs:
        issues.extend(spec.validate())
    # 批内同型重名拒绝（GUI 计数器按类型编号，如 Mapping(1)）。
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        key = (spec.kind, str(spec.label or "").strip())
        if key in seen:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, f"批内重名: {key[1]}（{key[0]}）"))
        seen.add(key)
    if not specs:
        issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "至少提供一个 --mast/--turbine/--result"))
    # E-P1-1：结果点判别值（MTDElementPoint）属 inference、未经 MUP GUI 验收，
    # 必须显式确认才放行（风格同 mup_cfd_import 的 --allow-unverified-atlas）。
    if any(s.kind == "result" for s in specs) and not allow_unverified_result:
        issues.append(mw.ImportIssue(
            mw.ImportCode.INPUT,
            "--result 的判别值 MTDElementPoint 属 inference（EF6 判别值=类名惯例 + "
            "MTDSiteModel 类型层次观测），尚未经 MUP 打开站点渲染验收；确认接受这份"
            "未验证写入才加 --allow-unverified-result-point"))
    # E-P1-2：风机风廓线占位默认对齐主导金样（144/147 行 = 20/200/20）。
    # 全 0 三元组是 test 站观测到的少数态（3/147），显式给定三 0 时放行。
    degenerate = vp_hmin == 0 and vp_hmax == 0 and vp_step == 0
    if not degenerate and (vp_step <= 0 or vp_hmax < vp_hmin or vp_hmin < 0):
        issues.append(mw.ImportIssue(
            mw.ImportCode.INPUT,
            f"风廓线占位参数不合法: HMin={vp_hmin} HMax={vp_hmax} Step={vp_step}"
            "（需 Step>0 且 HMax>=HMin>=0；主导金样 20/200/20；test 站少数态为三 0）"))
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)

    site_id, site_name, case_id, err = _resolve_site_and_case(db, site, case)
    if err:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, err)
    config_id, err = _resolve_config(db, case_id, config_name)
    if err:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, err)

    # 同站点同型重名拒绝（对照生产现有元素）。
    try:
        existing = mw._query_rows(db, f"SELECT Discriminator, Label FROM dbo.Elements "
                                      f"WHERE Parent_Id={mw._sql_literal(site_id)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取目标库: {exc}")
    existing_keys = {(str(r[0]), str(r[1])) for r in existing}
    conflicts = [f"{s.label}（{s.kind}）" for s in specs
                 if (DISC_BY_KIND[s.kind], str(s.label)) in existing_keys]
    if conflicts:
        return mw.ImportResult(False, mw.ImportCode.CONFLICT,
                               "站点内同型同名元素已存在: " + "、".join(conflicts))

    # wtg 仅校验存在性（元素不携带机型引用）。
    wtg_checks: dict[str, dict] = {}
    for spec in specs:
        if spec.kind == "turbine" and spec.wtg:
            matched, err = _check_wtg(db, spec.wtg)
            if err:
                return mw.ImportResult(False, mw.ImportCode.INPUT, err)
            wtg_checks[spec.label] = matched

    stmts: list[str] = []
    elements: list[dict] = []
    for spec in specs:
        elem = {
            "id": str(uuid.uuid4()), "kind": spec.kind, "label": spec.label,
            "x": spec.x, "y": spec.y, "z": spec.z,
            "discriminator": DISC_BY_KIND[spec.kind],
            "vp_id": str(uuid.uuid4()) if spec.kind == "turbine" else None,
            "vp": (float(vp_hmin), float(vp_hmax), float(vp_step))
                  if spec.kind == "turbine" else None,
            "spec": spec,
        }
        elements.append(elem)
        stmts.append(_insert_element(elem, site_id, referential))
        if spec.kind == "turbine":
            # 主导金样（生产 144/147 行）：HMin=20/HMax=200/Step=20（轮毂+10 档，
            # 综合侧计算点同口径）；test 站 JWD 3 行 0/0/0 是少数态，需复现时用
            # --vp-hmin 0 --vp-hmax 0 --vp-step 0 显式选择（侧审 E-P1-2）。
            hmin, hmax, step = elem["vp"]
            stmts.append(
                "INSERT INTO dbo.VertProfiles (Id,Label,CoordX,CoordY,HMin,HMax,Step,WTElement_Id) "
                f"VALUES ({mw._sql_literal(elem['vp_id'])},N'',0.0,0.0,"
                f"{_num(hmin)},{_num(hmax)},{_num(step)},{mw._sql_literal(elem['id'])});"
            )
    for elem in elements:
        stmts.append(
            "INSERT INTO dbo.Configuration_Element (ConfigurationId, ElementId) VALUES ("
            f"{mw._sql_literal(config_id)},{mw._sql_literal(elem['id'])});"
        )

    sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
           + chr(10).join(stmts) + chr(10) + "COMMIT;" + chr(10))
    ids = ",".join(mw._sql_literal(e["id"]) for e in elements)
    rollback = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
                + f"DELETE FROM dbo.Configuration_Element WHERE ElementId IN ({ids});" + chr(10)
                + f"DELETE FROM dbo.VertProfiles WHERE WTElement_Id IN ({ids});" + chr(10)
                + f"DELETE FROM dbo.Elements WHERE Id IN ({ids});" + chr(10) + "COMMIT;" + chr(10))

    plan = ElementPlan(site_id, site_name, case_id, config_id, config_name, elements, sql, rollback)
    return mw.ImportResult(True, mw.ImportCode.OK, "元素写入计划已生成",
                           data={"plan": plan.to_dict(), "plan_obj": plan,
                                 "wtg_checks": wtg_checks})


# --- 写后校验 ----------------------------------------------------------------
_NULL_SENTINEL = "-1e+18"


def _verify(db: str, plan: ElementPlan) -> mw.ImportResult:
    ids = ",".join(mw._sql_literal(e["id"]) for e in plan.elements)
    sql = (
        "SELECT e.Id, e.Label, e.Discriminator, e.State, e.Parent_Id, "
        "ISNULL(e.Point_X,-1e18), ISNULL(e.Point_Y,-1e18), ISNULL(e.Point_HZ,-1e18), "
        "ISNULL(e.Point_HZCoordType,-1), ISNULL(e.Quad_HZCoordType,-1), "
        "(SELECT COUNT(*) FROM dbo.Configuration_Element ce WHERE ce.ElementId=e.Id "
        f"AND ce.ConfigurationId={mw._sql_literal(plan.config_id)}), "
        "(SELECT COUNT(*) FROM dbo.VertProfiles vp WHERE vp.WTElement_Id=e.Id), "
        "(SELECT ISNULL(vp.HMin,-1e18) FROM dbo.VertProfiles vp WHERE vp.WTElement_Id=e.Id), "
        "(SELECT ISNULL(vp.HMax,-1e18) FROM dbo.VertProfiles vp WHERE vp.WTElement_Id=e.Id), "
        "(SELECT ISNULL(vp.Step,-1e18) FROM dbo.VertProfiles vp WHERE vp.WTElement_Id=e.Id) "
        f"FROM dbo.Elements e WHERE e.Id IN ({ids});"
    )
    try:
        rows = mw._query_rows(db, sql)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"写后校验失败: {exc}")
    if len(rows) != len(plan.elements):
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               f"写后校验行数不一致: 期望 {len(plan.elements)} 实得 {len(rows)}")
    by_id = {str(r[0]): r for r in rows}
    checks: dict[str, bool] = {}
    for elem in plan.elements:
        r = by_id.get(elem["id"])
        if r is None:
            checks[elem["label"]] = False
            continue
        # E-P2-1：Label 一并比对（列 1），插入端转义/编码漂移不得静默。
        ok = (str(r[1]) == elem["label"]
              and str(r[2]) == elem["discriminator"] and int(r[3]) == 0
              and str(r[4]) == plan.site_id and int(r[10]) == 1
              and int(r[11]) == (1 if elem["kind"] == "turbine" else 0))
        for idx, want in ((5, elem["x"]), (6, elem["y"]), (7, elem["z"])):
            if want is None:
                ok = ok and float(r[idx]) == -1e18
            else:
                ok = ok and abs(float(r[idx]) - float(want)) < 1e-6
        # 金样/惯例：三型点元素 Point_HZCoordType 恒为 1；Quad_HZCoordType 只属绘图。
        want_hzct = 1
        want_quadct = -1
        ok = ok and int(r[8]) == want_hzct and int(r[9]) == want_quadct
        # E-P1-2：风机的 VP 占位值按计划值（默认 20/200/20）逐列回读比对。
        if elem["kind"] == "turbine":
            hmin, hmax, step = elem["vp"]
            for idx, want in ((12, hmin), (13, hmax), (14, step)):
                ok = ok and abs(float(r[idx]) - float(want)) < 1e-6
        checks[elem["label"]] = bool(ok)
    if not all(checks.values()):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                               data={"checks": checks})
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过",
                           data={"checks": checks, "element_count": len(plan.elements)})


def apply_plan(db: str, plan: ElementPlan) -> mw.ImportResult:
    if mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库")
    if not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    ok, out, err = mw._run_sql(db, plan.sql)
    if not ok:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, f"写库失败: {err or out}")
    verified = _verify(db, plan)
    if not verified.ok:
        # E-P2-2：校验失败说明已提交的事务内容与计划漂移，自动回滚并把结果写进负载。
        rb_ok, rb_out, rb_err = mw._run_sql(db, plan.rollback_sql)
        data = dict(verified.data or {})
        data["rollback"] = {"executed": True, "ok": bool(rb_ok),
                            "output": (rb_out or "")[-2000:], "error": rb_err}
        if rb_ok:
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "写后校验失败，已自动回滚", data=data)
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验失败且自动回滚失败（需人工介入）", data=data)
    return verified


# --- 编排 -------------------------------------------------------------------
def run(specs: list[ElementSpec], db: str, site: Optional[str] = None,
        case: str = DEFAULT_CASE_NAME, config_name: str = DEFAULT_CONFIG_NAME,
        referential: Optional[str] = None, apply: bool = False,
        out_dir: Optional[Path] = None,
        vp_hmin: float = 20.0, vp_hmax: float = 200.0, vp_step: float = 20.0,
        allow_unverified_result: bool = False) -> mw.ImportResult:
    if apply and mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"拒绝写生产库 {db}")
    if apply and not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    work = Path(out_dir or (Path(tempfile.gettempdir()) / "mup_element_import"))
    work.mkdir(parents=True, exist_ok=True)

    planned = build_plan(specs, db, site=site, case=case, config_name=config_name,
                         referential=referential, vp_hmin=vp_hmin, vp_hmax=vp_hmax,
                         vp_step=vp_step, allow_unverified_result=allow_unverified_result)
    if not planned.ok:
        return planned
    plan: ElementPlan = planned.data["plan_obj"]
    # 判别值证据分级（侧审 E-P1-1：plan.json 必须标注哪些判别值属 inference）。
    evidence = {
        DISC_MAST: "observed: 生产金样 4 行",
        DISC_TURBINE: "observed: 生产金样 147 行",
        DISC_RESULT: "inference: EF6 判别值=类名惯例 + MTDSiteModel 类型层次观测；"
                     "未经 MUP 打开站点渲染验收，写入需 --allow-unverified-result-point 人工放行",
    }
    (work / "plan.json").write_text(json.dumps({
        "site": plan.site_name, "case": case,
        "config": plan.config_name, "db": db, "apply": bool(apply),
        "discriminator_evidence": {e["discriminator"]: evidence[e["discriminator"]]
                                   for e in plan.elements},
        "vp_defaults": {"hmin": vp_hmin, "hmax": vp_hmax, "step": vp_step},
        "plan": plan.to_dict(), "wtg_checks": planned.data["wtg_checks"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库）",
                               data={"plan": plan.to_dict(),
                                     "wtg_checks": planned.data["wtg_checks"],
                                     "out_dir": str(work),
                                     "rollback": str(work / "rollback.sql")})
    applied = apply_plan(db, plan)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message,
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "rollback": str(work / "rollback.sql"),
                                     "verify": applied.data})
    return mw.ImportResult(True, mw.ImportCode.OK,
                           f"元素写入并校验通过（{len(plan.elements)} 个）",
                           data={"plan": plan.to_dict(), "verify": applied.data,
                                 "wtg_checks": planned.data["wtg_checks"],
                                 "out_dir": str(work),
                                 "rollback": str(work / "rollback.sql")})


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="数据层写入 MUP 站点元素（测风点/风机/结果点）并挂载到配置")
    p.add_argument("--site", help="站点名（省略时按案例名全库解析，重名即拒）")
    p.add_argument("--case", default=DEFAULT_CASE_NAME, help=f"计算案例名（默认 {DEFAULT_CASE_NAME}）")
    p.add_argument("--config", default=DEFAULT_CONFIG_NAME,
                   help=f"挂载配置名（默认 {DEFAULT_CONFIG_NAME}）")
    p.add_argument("--mast", action="append", default=[],
                   help="测风点 label,x,y,z（可重复）")
    p.add_argument("--turbine", action="append", default=[],
                   help="风机 label,x,y,z[,wtg]（wtg 仅做存在性校验，可重复）")
    p.add_argument("--result", action="append", default=[],
                   help="结果点 label,x,y,z（可重复；判别值 MTDElementPoint 属 inference，"
                        "须搭配 --allow-unverified-result-point）")
    p.add_argument("--referential", default=None,
                   help="ReferentialFileName（默认 NULL；test 金样 3 行为 'JWD_Project_CGCS2000 43'，"
                        "生产其余 150 行 NULL）")
    p.add_argument("--vp-hmin", type=float, default=20.0,
                   help="风机 VertProfiles 占位 HMin（默认 20，主导金样 144/147 行口径）")
    p.add_argument("--vp-hmax", type=float, default=200.0,
                   help="风机 VertProfiles 占位 HMax（默认 200，主导金样口径）")
    p.add_argument("--vp-step", type=float, default=20.0,
                   help="风机 VertProfiles 占位 Step（默认 20，主导金样口径；"
                        "0/0/0 为 test 站少数态，需显式三参全 0）")
    p.add_argument("--allow-unverified-result-point", dest="allow_unverified_result",
                   action="store_true",
                   help="结果点判别值 MTDElementPoint 属 inference 未经 MUP 验收，"
                        "显式确认后才写 --result（风格同 --allow-unverified-atlas）")
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true", help="显式写库（默认 dry-run）")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    specs: list[ElementSpec] = []
    issues: list[mw.ImportIssue] = []
    for kind, values in (("mast", args.mast), ("turbine", args.turbine), ("result", args.result)):
        for text in values:
            spec, err = parse_element_arg(kind, text)
            if err:
                issues.append(mw.ImportIssue(mw.ImportCode.INPUT, err))
            elif spec:
                specs.append(spec)
    if issues:
        result = mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print("[FAIL] " + result.message)
            for issue in result.issues:
                print("  " + issue.code + ": " + issue.message)
        return result.exit_code()
    result = run(specs, args.db, site=args.site, case=args.case, config_name=args.config,
                 referential=args.referential, apply=args.apply,
                 out_dir=Path(args.out_dir) if args.out_dir else None,
                 vp_hmin=args.vp_hmin, vp_hmax=args.vp_hmax, vp_step=args.vp_step,
                 allow_unverified_result=args.allow_unverified_result)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        for issue in result.issues:
            print("  " + issue.code + ": " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
