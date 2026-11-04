# encoding: utf-8
"""数据层新增 MUP「绘图 Mapping」元素（Elements/MTDElementMapping + 配置挂载）。

背景（2026-11-04 数据层侦察，见
H:\\Rea\\rea-main\\99_synthesis\\2026-11-04-mapping-cfd-datalayer-codex.md；
P3-J：本仓 E:\\My_RF_Project\\MUP_recon\\99_synthesis\\ 下没有这份，引用改绑实存位置）
------------------------------------------------------------------------
MUP「绘图」侧栏的 Mapping **不是** WindResourceMaps（该表本机 0 行），而是一条
`dbo.Elements` 行：Discriminator='MTDElementMapping'，Parent_Id = **Sites.Id**（不是 Case），
几何全部落在 Quad_* 列：

    Quad_Origin_X / Y      = 西南角（X=min, Y=min）
    Quad_Origin_Z          = 高度（GUI 的「高度」）
    Quad_Edge1_X/Y/Z       = (1,0,0) 东向单位向量
    Quad_Edge1_StepLength  = 步长；Quad_Edge1_NbStep = (东南X-西北X)/步长
    Quad_Edge2_X/Y/Z       = (0,1,0) 北向单位向量
    Quad_Edge2_StepLength  = 步长；Quad_Edge2_NbStep = (西北Y-东南Y)/步长
    Quad_HZCoordType       = 1（与两条生产金样一致）

金样（生产 test 站 Mapping(1)）：Origin(43538274, 5130516, 125)、步长 100、
Edge1_NbStep=317（31700 m）、Edge2_NbStep=296（29600 m）→ 西北角(43538274,5160116)、
东南角(43569974,5130516)，与 GUI 显示逐字段吻合。

挂载：`dbo.Configuration_Element` 把元素挂到该 Case 的 `CaseElementConfiguration`
（金样：Mapping 只挂在 CaseElementConfiguration，用户后建的「配置1」不含它）。

UI 校准（2026-11-05，锚点表
H:\\Rea\\rea-main\\99_synthesis\\mup-form-modules-uipath-codebuddy.md）
----------------------------------------------------------------
  · 「绘图」不是独立 tab，是**元素模块里的一个元素类别**：侧栏只有 建模/元素/CFD/综合/载入，
    新建绘图走 元素 → `InterestAreas_Button_Add` → `MBAElementSingleCreatorView`；
    与"元素实体是 Elements 行 + 配置是 Configurations/Configuration_Element"完全一致。
  · 元素属性行锚点 `MBAMappingViewModel_PropertyLine_XMin/_XMax/_YMin/_YMax/_Height/_Step`
    ⇔ 上面的 Quad_* 五元组，所以脚本同时接受角点式与范围式两种输入。
  · 创建器还有「追加高度/删除高度」(`ElementMultipleCreatorView_Button_AddHeight/_DeleteHeight`)
    ⇒ 一张绘图可能有多高度；但库里一行只有一个 `Quad_Origin_Z`，[推断] MUP 用**多行**表达多高度，
    本机没有多高度金样可证 ⇒ 本脚本一次只写一个高度，多次调用即多条 Mapping(n)。

设计（与 mup_site_import.py 同范式）
------------------------------------
  · 默认 dry-run；写库只允许副本库（*_verify / *_test / *_sandbox）。
  · 单事务 + 回滚脚本 + 写后校验（逐列回读比对 + 角点反算）。
  · 写后校验失败时自动执行 rollback.sql 并把回滚结果写进返回负载 data["rollback"]
    （G-1/G-2，zcode 双轮实机：过去非整数跨度会「先落库、校验才拒」且库里留残留行）。
  · 跨度必须是步长的整数倍，否则计划期 input_invalid 硬拒（不 note+取整，见 MappingSpec.validate）。
  · 「站点没有 Case」「站点没有 0-9000 m 测风/风机元素」属前置条件缺失
    → precondition_missing(exit 7)，与「同名已存在」name_exists(exit 6) 分开（P4）。
  · 站点合理性闸门：网格框必须与站点中心/半径同量级（"别天上地下"），
    且站点的测风塔/风机元素高度须落在 0-9000 m（复刻 GUI 的前置门）。
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mup_weather_import as mw

__all__ = ["MappingSpec", "MappingPlan", "build_plan", "apply_plan", "run", "main"]

DISC_MAPPING = "MTDElementMapping"
DEFAULT_CONFIG_NAME = "CaseElementConfiguration"
DEFAULT_HZ_COORD_TYPE = 1
ELEMENT_HEIGHT_MIN = 0.0
ELEMENT_HEIGHT_MAX = 9000.0


def _sql_float(value: float) -> str:
    """float → SQL 字面量（repr 为 shortest round-trip，写 float(53) 无损）。"""
    return repr(float(value))


@dataclass
class MappingSpec:
    site: str
    nw_x: float
    nw_y: float
    se_x: float
    se_y: float
    height: float = 125.0
    step: float = 100.0
    label: str = ""
    hz_coord_type: int = DEFAULT_HZ_COORD_TYPE
    config_name: str = DEFAULT_CONFIG_NAME
    max_center_offset_factor: float = 3.0
    require_reference_element: bool = True

    @property
    def width(self) -> float:
        return self.se_x - self.nw_x

    @property
    def north_south(self) -> float:
        return self.nw_y - self.se_y

    @classmethod
    def from_bounds(cls, site: str, x_min: float, x_max: float, y_min: float, y_max: float,
                    **kwargs) -> "MappingSpec":
        """按 GUI 属性行的 XMin/XMax/YMin/YMax 给范围（锚点
        `MBAMappingViewModel_PropertyLine_XMin/_XMax/_YMin/_YMax`）。
        库里存的是西南角原点 + 两条边，所以西北角=(XMin,YMax)、东南角=(XMax,YMin)。"""
        return cls(site=site, nw_x=float(x_min), nw_y=float(y_max),
                   se_x=float(x_max), se_y=float(y_min), **kwargs)

    def nb_steps(self) -> tuple[float, float]:
        """(东西向, 南北向) 的「跨度 / 步长」，未取整。
        取整口径只有这一处解释者：validate() 用它判整数倍，build_plan() 用它落 NbStep。"""
        return (self.width / float(self.step), self.north_south / float(self.step))

    def _span_multiple_issues(self) -> list[mw.ImportIssue]:
        """G-1：跨度不是步长整数倍 ⇒ 输入不合法（不是 note）。"""
        issues: list[mw.ImportIssue] = []
        for name, value in zip(("东西向", "南北向"), self.nb_steps()):
            if abs(value - round(value)) <= 1e-6:
                continue
            rounded = int(round(value))
            delta = abs(value - rounded) * float(self.step)
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "%s跨度 %.3f m 不是步长 %s m 的整数倍（%.6f 步）。Quad_Edge*_NbStep 是整数列，"
                "库里只能存 %d 步，反算出的角点会比给定值%s %.3f m：GUI 会显示取整后的角点，"
                "而脚本无法既保留给定角点又保留整数步数。请改角点或步长（就近整倍数：跨度 %.0f m）"
                % (name, value * float(self.step), self.step, value, rounded,
                   "东缘/南缘" if name == "东西向" else "北缘", delta, rounded * float(self.step)),
                {"axis": name, "span_m": value * float(self.step), "step_m": float(self.step),
                 "nb_step_exact": value, "nb_step_rounded": rounded, "corner_delta_m": delta}))
        return issues

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if not str(self.site or "").strip():
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "站点名称/Id 不能为空"))
        for name, value in (("西北角X", self.nw_x), ("西北角Y", self.nw_y),
                            ("东南角X", self.se_x), ("东南角Y", self.se_y),
                            ("高度", self.height)):
            if not math.isfinite(float(value)):
                issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "%s 不是有限数值: %s" % (name, value)))
        if float(self.step) <= 0:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "步长必须大于 0（当前 %s）" % self.step))
        if self.width <= 0 or self.north_south <= 0:
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "角点顺序不对：需 东南X>西北X 且 西北Y>东南Y（当前宽=%s 高=%s）" % (self.width, self.north_south)))
        # G-1（zcode 双轮实机）：非整数跨度过去只在计划期 note + int(round())，apply 照样落库
        # （库里 NbStep 已是取整值），到写后校验才因角点反算失配报 verify_failed(exit 5)。
        # 调用方把 exit 5 读成「输入被拒」，实则残留一行 + 一条挂载。NbStep 是整数列，GUI 也是
        # 靠它反算角点 ⇒ 计划期硬拒才是 fail-closed，也与 element 侧的输入门对称。
        if float(self.step) > 0 and self.width > 0 and self.north_south > 0:
            issues.extend(self._span_multiple_issues())
        if float(self.height) < ELEMENT_HEIGHT_MIN or float(self.height) > ELEMENT_HEIGHT_MAX:
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "高度 %s m 超出 GUI 允许区间 %s-%s" % (self.height, ELEMENT_HEIGHT_MIN, ELEMENT_HEIGHT_MAX)))
        if int(self.hz_coord_type) not in (0, 1, 2):
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "Quad_HZCoordType 只能是 0/1/2（金样=1）"))
        return issues

    def sanity_issues(self, center_x: float, center_y: float, radius: float) -> list[mw.ImportIssue]:
        """「别天上地下」闸门：网格框必须与站点中心同量级，否则判为投影/坐标系填错。"""
        issues: list[mw.ImportIssue] = []
        cx = (self.nw_x + self.se_x) / 2.0
        cy = (self.nw_y + self.se_y) / 2.0
        offset = math.hypot(cx - float(center_x), cy - float(center_y))
        limit = max(abs(float(radius)) * float(self.max_center_offset_factor), 5000.0)
        if offset > limit:
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "绘图框中心 (%.1f, %.1f) 距站点中心 (%s, %s) = %.1f m，超过 %.1f m 上限；"
                "多半是坐标系/带号填错（坐标必须与 Sites.Projection 同投影）" % (
                    cx, cy, center_x, center_y, offset, limit),
                {"grid_center": [cx, cy], "offset_m": offset, "limit_m": limit}))
        span = max(abs(self.width), abs(self.north_south))
        if float(radius) > 0 and span > float(radius) * 40.0:
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "绘图框跨度 %.0f m 远大于站点半径 %.0f m，疑似单位或坐标填错" % (span, radius)))
        return issues


@dataclass
class MappingPlan:
    spec: MappingSpec
    element_id: str
    site_id: str
    case_id: str
    configuration_id: str
    label: str
    nb_step_x: int
    nb_step_y: int
    origin_x: float
    origin_y: float
    link_configuration: bool
    notes: list[str] = field(default_factory=list)
    sql: str = ""
    rollback_sql: str = ""

    def to_dict(self) -> dict:
        return {
            "element_id": self.element_id,
            "label": self.label,
            "site_id": self.site_id,
            "case_id": self.case_id,
            "configuration_id": self.configuration_id,
            "link_configuration": self.link_configuration,
            "height": self.spec.height,
            "step": self.spec.step,
            "nb_step_x": self.nb_step_x,
            "nb_step_y": self.nb_step_y,
            "origin": [self.origin_x, self.origin_y],
            "north_west": [self.spec.nw_x, self.spec.nw_y],
            "south_east": [self.spec.se_x, self.spec.se_y],
            "hz_coord_type": self.spec.hz_coord_type,
            "notes": self.notes,
        }


def _resolve_site(db: str, key: str) -> tuple[Optional[dict], mw.ImportResult]:
    lit = mw._sql_literal
    rows = mw._query_rows(
        db,
        "SELECT Id, Name, CenterX, CenterY, ISNULL(Radius,0), LEN(ISNULL(Projection,'')) "
        "FROM dbo.Sites WHERE Name=" + lit(key) + " OR Id=" + lit(key) + ";")
    if not rows:
        return None, mw.ImportResult(False, mw.ImportCode.DB_GUARD, "站点不存在: " + str(key))
    r = rows[0]
    return ({"id": r[0], "name": r[1], "center_x": float(r[2]), "center_y": float(r[3]),
             "radius": float(r[4]), "projection_len": int(r[5])},
            mw.ImportResult(True, mw.ImportCode.OK, "站点已定位"))


def _next_label(db: str, site_id: str, wanted: str) -> str:
    if wanted:
        return wanted
    rows = mw._query_rows(
        db,
        "SELECT Label FROM dbo.Elements WHERE Discriminator=" + mw._sql_literal(DISC_MAPPING)
        + " AND Parent_Id=" + mw._sql_literal(site_id) + ";")
    used = {r[0] for r in rows if r}
    index = 1
    while "Mapping(%d)" % index in used:
        index += 1
    return "Mapping(%d)" % index


def build_plan(spec: MappingSpec, db: str, link_configuration: bool = True) -> mw.ImportResult:
    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)
    try:
        site, res = _resolve_site(db, spec.site)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "无法读取目标库: " + str(exc))
    if not site:
        return res
    if site["projection_len"] == 0:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               "站点无投影（Sites.Projection 为空），绘图坐标无意义；"
                               "先用 mup_site_import.py 建投影")
    issues = spec.sanity_issues(site["center_x"], site["center_y"], site["radius"])
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "绘图坐标未通过站点合理性闸门", issues=issues)

    try:
        case_rows = mw._query_rows(
            db, "SELECT Id FROM dbo.Cases WHERE Parent_Id=" + mw._sql_literal(site["id"])
                + " ORDER BY Name;")
        conf_rows = mw._query_rows(
            db, "SELECT cf.Id FROM dbo.Configurations cf WHERE cf.Case_Id IN ("
                "SELECT Id FROM dbo.Cases WHERE Parent_Id=" + mw._sql_literal(site["id"]) + ")"
                " AND cf.Name=" + mw._sql_literal(spec.config_name) + ";")
        ref_rows = mw._query_rows(
            db, "SELECT COUNT(*) FROM dbo.Elements WHERE Parent_Id=" + mw._sql_literal(site["id"])
                + " AND Discriminator IN ('MTDElementMast','MTDElementWindTurbine')"
                + " AND Point_HZ BETWEEN " + repr(ELEMENT_HEIGHT_MIN) + " AND "
                + repr(ELEMENT_HEIGHT_MAX) + ";")
        dup = mw._query_rows(
            db, "SELECT Id FROM dbo.Elements WHERE Parent_Id=" + mw._sql_literal(site["id"])
                + " AND Discriminator=" + mw._sql_literal(DISC_MAPPING)
                + " AND Label=" + mw._sql_literal(spec.label) + ";") if spec.label else []
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "读取站点从属数据失败: " + str(exc))

    notes: list[str] = []
    if not case_rows:
        # P4：「缺前置条件」不是「库守卫失败」，上层编排要看到的是补 Case，而不是换库名。
        return mw.ImportResult(
            False, mw.ImportCode.PRECONDITION,
            "站点下没有 Case，无法挂载配置：先用站点/案例写入接口补一个 Case 再来",
            issues=[mw.ImportIssue(mw.ImportCode.PRECONDITION,
                                   "站点 %s (Id=%s) 下没有 Case 行" % (site["name"], site["id"]),
                                   {"site_id": site["id"], "missing": "dbo.Cases"})])
    if spec.require_reference_element and int((ref_rows[0] or ["0"])[0] or 0) == 0:
        return mw.ImportResult(
            False, mw.ImportCode.PRECONDITION,
            "站点没有高度落在 0-9000 m 的测风塔/风机元素，GUI 同样拒绝新增绘图；"
            "先写元素（mup_turbine_import.py 等）再来")
    if dup:
        return mw.ImportResult(False, mw.ImportCode.CONFLICT,
                               "同名绘图元素已存在: " + spec.label + "（留空 Label 可自动编号）")
    if link_configuration and not conf_rows:
        notes.append("未找到 %s，只写 Elements 行、不写 Configuration_Element" % spec.config_name)
        link_configuration = False

    # G-1：非整数跨度已在 spec.validate() 计划期硬拒，这里拿到的必然是整倍数。
    nb_x, nb_y = spec.nb_steps()

    plan = MappingPlan(
        spec=spec, element_id=str(uuid.uuid4()), site_id=site["id"], case_id=case_rows[0][0],
        configuration_id=(conf_rows[0][0] if conf_rows else ""),
        label=_next_label(db, site["id"], spec.label),
        nb_step_x=int(round(nb_x)), nb_step_y=int(round(nb_y)),
        origin_x=spec.nw_x, origin_y=spec.se_y, link_configuration=bool(link_configuration),
        notes=notes)
    plan.sql = _insert_sql(plan)
    plan.rollback_sql = _rollback_sql(plan)
    return mw.ImportResult(True, mw.ImportCode.OK, "绘图写入计划已生成",
                           data={"plan": plan.to_dict(), "plan_obj": plan})


def _insert_sql(plan: MappingPlan) -> str:
    lit = mw._sql_literal
    stmts = [
        "INSERT INTO dbo.Elements (Id,Label,Discriminator,Parent_Id,State,IsFromPostCFD,"
        "Quad_HZCoordType,Quad_Origin_X,Quad_Origin_Y,Quad_Origin_Z,"
        "Quad_Edge1_X,Quad_Edge1_Y,Quad_Edge1_Z,Quad_Edge1_NbStep,Quad_Edge1_StepLength,"
        "Quad_Edge2_X,Quad_Edge2_Y,Quad_Edge2_Z,Quad_Edge2_NbStep,Quad_Edge2_StepLength) VALUES ("
        + lit(plan.element_id) + "," + lit(plan.label) + "," + lit(DISC_MAPPING) + ","
        + lit(plan.site_id) + ",0,0,"
        + str(int(plan.spec.hz_coord_type)) + ","
        + _sql_float(plan.origin_x) + "," + _sql_float(plan.origin_y) + ","
        + _sql_float(plan.spec.height) + ","
        + "1,0,0," + str(plan.nb_step_x) + "," + _sql_float(plan.spec.step) + ","
        + "0,1,0," + str(plan.nb_step_y) + "," + _sql_float(plan.spec.step) + ");"
    ]
    if plan.link_configuration:
        stmts.append("INSERT INTO dbo.Configuration_Element (ConfigurationId,ElementId) VALUES ("
                     + lit(plan.configuration_id) + "," + lit(plan.element_id) + ");")
    return ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
            + chr(10).join(stmts) + chr(10) + "COMMIT;" + chr(10))


def _rollback_sql(plan: MappingPlan) -> str:
    lit = mw._sql_literal
    return ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
            + "DELETE FROM dbo.Configuration_Element WHERE ElementId=" + lit(plan.element_id) + ";"
            + chr(10)
            + "DELETE FROM dbo.Elements WHERE Id=" + lit(plan.element_id) + ";" + chr(10)
            + "COMMIT;" + chr(10))


def _verify(db: str, plan: MappingPlan) -> mw.ImportResult:
    lit = mw._sql_literal
    try:
        rows = mw._query_rows(
            db,
            "SELECT Label, Discriminator, Parent_Id, State, Quad_HZCoordType,"
            "Quad_Origin_X, Quad_Origin_Y, Quad_Origin_Z,"
            "Quad_Edge1_X, Quad_Edge1_Y, Quad_Edge1_NbStep, Quad_Edge1_StepLength,"
            "Quad_Edge2_X, Quad_Edge2_Y, Quad_Edge2_NbStep, Quad_Edge2_StepLength,"
            "(SELECT COUNT(*) FROM dbo.Configuration_Element WHERE ElementId="
            + lit(plan.element_id) + ") FROM dbo.Elements WHERE Id=" + lit(plan.element_id) + ";")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验失败: " + str(exc))
    if not rows:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "绘图元素行不存在")
    r = rows[0]
    # 回读形状本身也要断言：列数不足/值不可解析必须是 verify_failed（进而自动回滚），
    # 不能变成 IndexError 冒到调用方 —— 那会让库里留下没有回滚的残留行（P1-A 同族）。
    if len(r) < 17:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验回读列数不足：期望 17，实得 %d（%r）" % (len(r), r))
    try:
        return _check_row(plan, r)
    except (ValueError, TypeError, IndexError) as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验回读无法解析: %s（row=%r）" % (exc, r))


def _check_row(plan: MappingPlan, r: list) -> mw.ImportResult:
    """逐列比对 + 角点反算。调用方保证 r 有 17 列；解析异常由 _verify 转成 verify_failed。"""
    spec = plan.spec

    def num(idx: int) -> float:
        return float(r[idx])

    def integer(idx: int) -> int:
        return int(r[idx])

    checks = {
        "label": r[0] == plan.label,
        "discriminator": r[1] == DISC_MAPPING,
        "parent_is_site": r[2] == plan.site_id,
        "state": integer(3) == 0,
        "hz_coord_type": integer(4) == int(spec.hz_coord_type),
        "origin_x": abs(num(5) - plan.origin_x) < 1e-6,
        "origin_y": abs(num(6) - plan.origin_y) < 1e-6,
        "height": abs(num(7) - float(spec.height)) < 1e-6,
        "edge1_axis": abs(num(8) - 1.0) < 1e-9 and abs(num(9)) < 1e-9,
        "edge1_nbstep": integer(10) == plan.nb_step_x,
        "edge1_step": abs(num(11) - float(spec.step)) < 1e-6,
        "edge2_axis": abs(num(12)) < 1e-9 and abs(num(13) - 1.0) < 1e-9,
        "edge2_nbstep": integer(14) == plan.nb_step_y,
        "edge2_step": abs(num(15) - float(spec.step)) < 1e-6,
        "config_link": integer(16) == (1 if plan.link_configuration else 0),
    }
    # 角点反算：Origin + Edge1/Edge2 必须还原用户给定的西北/东南角
    checks["corner_east_edge"] = abs(num(5) + num(11) * integer(10) - spec.se_x) < 1e-6
    checks["corner_north_edge"] = abs(num(6) + num(15) * integer(14) - spec.nw_y) < 1e-6
    if not all(checks.values()):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                               data={"checks": checks, "row": r})
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过",
                           data={"checks": checks, "element_id": plan.element_id})


def apply_plan(db: str, plan: MappingPlan) -> mw.ImportResult:
    if mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "拒绝写生产库 " + db + "；请用副本库")
    if not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "目标库不在允许列表: " + db)
    ok, out, err = mw._run_sql(db, plan.sql)
    if not ok:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, "写库失败: " + (err or out))
    verified = _verify(db, plan)
    if verified.ok:
        return verified
    # G-2（对齐 element 的 E-P2-2）：校验失败说明已提交事务与计划漂移，自动跑 rollback.sql，
    # 并把回滚结果写进负载 —— 过去只落盘不执行，失败路径会在库里留下元素行 + 配置挂载。
    rb_ok, rb_out, rb_err = mw._run_sql(db, plan.rollback_sql)
    data = dict(verified.data or {})
    data["rollback"] = {"executed": True, "ok": bool(rb_ok),
                        "output": (rb_out or "")[-2000:], "error": rb_err}
    # 校验失败的原因必须活着出来：负载被回滚结果顶掉后，编排侧只能看到「失败」两个字。
    data["verify_message"] = verified.message
    detail = "（%s）" % verified.message if verified.message else ""
    if rb_ok:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验失败%s，已自动回滚" % detail, data=data)
    return mw.ImportResult(False, mw.ImportCode.VERIFY,
                           "写后校验失败%s且自动回滚失败（需人工介入）" % detail, data=data)


def run(spec: MappingSpec, db: str, apply: bool = False, link_configuration: bool = True,
        out_dir: Optional[Path] = None) -> mw.ImportResult:
    if apply and (mw.is_prod_db(db) or not mw.apply_ok(db)):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "拒绝写目标库: " + db)
    work = Path(out_dir or (Path(tempfile.gettempdir()) / "mup_mapping_import"))
    work.mkdir(parents=True, exist_ok=True)
    planned = build_plan(spec, db, link_configuration=link_configuration)
    if not planned.ok:
        return planned
    plan: MappingPlan = planned.data["plan_obj"]
    (work / "plan.json").write_text(json.dumps({"spec": spec.__dict__, "plan": plan.to_dict()},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")
    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库）",
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "rollback": str(work / "rollback.sql")})
    applied = apply_plan(db, plan)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message,
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "rollback": str(work / "rollback.sql"),
                                     "verify": applied.data})
    return mw.ImportResult(True, mw.ImportCode.OK, "绘图写入并校验通过",
                           data={"plan": plan.to_dict(), "verify": applied.data,
                                 "out_dir": str(work), "rollback": str(work / "rollback.sql")})


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="数据层新增 MUP 绘图 Mapping 元素")
    p.add_argument("--site", required=True, help="站点名称或 Id")
    # 两种写法等价，对应 GUI 的两个位置：侧栏的西北角/东南角，元素属性行的 XMin/XMax/YMin/YMax。
    corner = p.add_argument_group("角点式（侧栏显示：西北角 / 东南角）")
    corner.add_argument("--nw-x", dest="nw_x", type=float, default=None, help="西北角 X")
    corner.add_argument("--nw-y", dest="nw_y", type=float, default=None, help="西北角 Y")
    corner.add_argument("--se-x", dest="se_x", type=float, default=None, help="东南角 X")
    corner.add_argument("--se-y", dest="se_y", type=float, default=None, help="东南角 Y")
    bounds = p.add_argument_group("范围式（属性行锚点 MBAMappingViewModel_PropertyLine_XMin/_XMax/_YMin/_YMax）")
    bounds.add_argument("--x-min", dest="x_min", type=float, default=None, help="XMin")
    bounds.add_argument("--x-max", dest="x_max", type=float, default=None, help="XMax")
    bounds.add_argument("--y-min", dest="y_min", type=float, default=None, help="YMin")
    bounds.add_argument("--y-max", dest="y_max", type=float, default=None, help="YMax")
    p.add_argument("--height", type=float, default=125.0, help="高度 m（Quad_Origin_Z）")
    p.add_argument("--step", type=float, default=100.0, help="步长 m（两条边同值）")
    p.add_argument("--label", default="", help="留空自动 Mapping(n)")
    p.add_argument("--hz-coord-type", type=int, default=DEFAULT_HZ_COORD_TYPE)
    p.add_argument("--config-name", default=DEFAULT_CONFIG_NAME)
    p.add_argument("--no-link", action="store_true", help="只写 Elements，不挂配置")
    p.add_argument("--allow-no-reference-element", action="store_true",
                   help="跳过「站点须有 0-9000 m 测风/风机元素」的 GUI 前置门")
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true")
    return p


def _spec_from_args(args: argparse.Namespace) -> tuple[Optional[MappingSpec], str]:
    """角点式与范围式二选一；两者都给时按范围式（属性行是权威显示），并提示差异。"""
    common = dict(height=args.height, step=args.step, label=args.label,
                  hz_coord_type=args.hz_coord_type, config_name=args.config_name,
                  require_reference_element=not args.allow_no_reference_element)
    corners = [args.nw_x, args.nw_y, args.se_x, args.se_y]
    bounds = [args.x_min, args.x_max, args.y_min, args.y_max]
    has_corners = all(value is not None for value in corners)
    has_bounds = all(value is not None for value in bounds)
    if has_bounds:
        spec = MappingSpec.from_bounds(args.site, args.x_min, args.x_max,
                                      args.y_min, args.y_max, **common)
        if has_corners and [spec.nw_x, spec.nw_y, spec.se_x, spec.se_y] != corners:
            return spec, ("两种写法都给了且不一致，按范围式 XMin/XMax/YMin/YMax 为准: "
                          "角点式算出 (%s, %s, %s, %s)" % tuple(corners))
        return spec, ""
    if has_corners:
        return MappingSpec(site=args.site, nw_x=args.nw_x, nw_y=args.nw_y,
                           se_x=args.se_x, se_y=args.se_y, **common), ""
    return None, ("必须给全角点式 --nw-x/--nw-y/--se-x/--se-y，"
                  "或全范围式 --x-min/--x-max/--y-min/--y-max")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    spec, warning = _spec_from_args(args)
    if spec is None:
        print("[FAIL] " + warning)
        return 2
    if warning:
        print("[WARN] " + warning)
    result = run(spec, args.db, apply=args.apply, link_configuration=not args.no_link,
                 out_dir=Path(args.out_dir) if args.out_dir else None)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        for issue in result.issues:
            print("  " + issue.code + ": " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
