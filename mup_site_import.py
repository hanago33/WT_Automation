# encoding: utf-8
"""数据层新建 MUP 建模站点（Sites + Cases + 默认 Configurations）。

背景
----
MUP「主面板 → 新建建模」最终落三张表：
  · Sites          : 站点元数据 + 投影 WKT + 中心/半径 + 访问级别
  · Cases          : 站点下的计算案例（MTDCTSCaseObject），**中心/半径必须与 Sites 同步**
  · Configurations : 案例的默认配置（Name=CaseElementConfiguration）
（Projects / ProjectDocuments 在本机金样里为 0 行，建站不需要。）

设计
----
  · 默认 dry-run；写库只允许副本库（*_verify / *_test / *_sandbox）。
  · 单事务 + 回滚脚本 + 写后校验（投影长度、Cases 中心联动、默认配置存在）。
  · 投影：官方 EPSG:4531 WKT 725 字符（内嵌常量 OFFICIAL_WKT_4531；脚本同目录
    wkt_4531_official.txt 存在时优先读文件），不依赖任何外部审查目录。
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import mup_weather_import as mw

__all__ = ["SiteSpec", "SitePlan", "build_plan", "apply_plan", "run", "main"]

DISC_SITE = "MUPSiteObject"
DISC_CASE = "MTDCTSCaseObject"
DEFAULT_CASE_NAME = "WT"
DEFAULT_CONFIG_NAME = "CaseElementConfiguration"

# 官方 EPSG:4531 WKT（725 字符，与 MUP 投影选择器做字符串全等比较）。
# 内嵌常量 + 脚本同目录资产双保险：不再依赖审查目录 H:/Rea/...（P2-A）。
OFFICIAL_WKT_4531 = 'PROJCS["CGCS2000 / 3-degree Gauss-Kruger zone 43", GEOGCS["China Geodetic Coordinate System 2000", DATUM["China 2000", SPHEROID["CGCS2000", 6378137, 298.257222101, AUTHORITY["EPSG", "1024"]], AUTHORITY["EPSG", "1043"]], PRIMEM["Greenwich", 0, AUTHORITY["EPSG", "8901"]], UNIT["degree", 0.0174532925199433], AXIS["Geodetic longitude", EAST], AXIS["Geodetic latitude", NORTH], AUTHORITY["EPSG", "4490"]], UNIT["m", 1], PROJECTION["Transverse_Mercator", AUTHORITY["EPSG", "9807"]], PARAMETER["central_meridian", 129], PARAMETER["latitude_of_origin", 0], PARAMETER["scale_factor", 1], PARAMETER["false_easting", 43500000], PARAMETER["false_northing", 0], AUTHORITY["EPSG", "4531"], AXIS["Easting", EAST], AXIS["Northing", NORTH]]'
DEFAULT_WKT_PATH = Path(__file__).with_name("wkt_4531_official.txt")

ACCESS_LEVELS = {"public": 0, "group": 1, "private": 2}


@dataclass
class SiteSpec:
    name: str
    description: str = ""
    projection_wkt: str = ""
    center_x: float = 0.0
    center_y: float = 0.0
    radius: float = 0.0
    access_level: int = 0
    owner_id: Optional[str] = None
    case_name: str = DEFAULT_CASE_NAME

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if not str(self.name or "").strip():
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "站点名称不能为空"))
        if int(self.access_level) not in (0, 1, 2):
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "访问级别只能是 0/1/2"))
        if float(self.radius) <= 0:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "站点半径必须大于 0（当前 %s）" % self.radius))
        if self.projection_wkt:
            if "PROJCS[" not in self.projection_wkt or "4531" not in self.projection_wkt:
                issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "投影 WKT 不是 EPSG:4531"))
            if abs(float(self.center_x)) < 1e-6 and abs(float(self.center_y)) < 1e-6:
                issues.append(mw.ImportIssue(mw.ImportCode.INPUT,
                                             "EPSG:4531 投影下中心坐标不能为 (0, 0)"))
        return issues


@dataclass
class SitePlan:
    spec: SiteSpec
    site_id: str
    case_id: str
    config_id: str
    owner_id: str
    sql: str
    rollback_sql: str

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "case_id": self.case_id,
            "config_id": self.config_id,
            "name": self.spec.name,
            "projection_len": len(self.spec.projection_wkt or ""),
            "center": [self.spec.center_x, self.spec.center_y],
            "radius": self.spec.radius,
        }


def load_official_wkt(path: Optional[Path] = None) -> str:
    # 官方 EPSG:4531 WKT：显式路径优先（存在即读），缺省路径无文件时回退内嵌常量。
    # 显式指定的文件不存在则 fail-closed（避免用户以为读的是自己的 WKT）。
    if path is None and not DEFAULT_WKT_PATH.is_file():
        return OFFICIAL_WKT_4531
    p = Path(path or DEFAULT_WKT_PATH)
    if not p.is_file():
        raise RuntimeError(f"官方 WKT 文件不存在: {p}")
    text = p.read_text(encoding="utf-8-sig").strip()
    if "PROJCS[" not in text:
        raise RuntimeError(f"WKT 文件内容不合法: {p}")
    return text


# Cases / Configurations 的上游从属表：外键为 ON DELETE NO_ACTION，直接 DELETE 会违反约束。
_DEPENDENCY_SQL = (
    "SELECT "
    "(SELECT COUNT(*) FROM dbo.Configuration_Element WHERE ConfigurationId IN "
    "(SELECT Id FROM dbo.Configurations WHERE Case_Id IN "
    "(SELECT Id FROM dbo.Cases WHERE Parent_Id={site}))),"
    "(SELECT COUNT(*) FROM dbo.Case_EnvironmentDatas WHERE CaseId IN "
    "(SELECT Id FROM dbo.Cases WHERE Parent_Id={site})),"
    "(SELECT COUNT(*) FROM dbo.ComputationObject WHERE Parent_Id IN "
    "(SELECT Id FROM dbo.Cases WHERE Parent_Id={site})),"
    "(SELECT COUNT(*) FROM dbo.WindResourceMaps WHERE Case_Id IN "
    "(SELECT Id FROM dbo.Cases WHERE Parent_Id={site}));"
)
_DEPENDENCY_NAMES = ("configuration_element", "case_environment_data",
                     "computation_object", "wind_resource_map")


def _dependent_row_counts(db: str, site_id: str) -> dict[str, int]:
    # 只统计「直接挡在 DELETE 前面」的从属表；更深层从属（DataSource/VertProfiles/...）
    # 由单事务 + XACT_ABORT 兜底：失败即整事务回滚，不会留下半删状态。
    sql = _DEPENDENCY_SQL.replace("{site}", mw._sql_literal(site_id))
    rows = mw._query_rows(db, sql)
    if not rows or not rows[0]:
        return {}
    try:
        return {name: int(value) for name, value in zip(_DEPENDENCY_NAMES, rows[0])}
    except ValueError:
        return {}


def _dependency_blockers(db: str, site_id: str) -> mw.ImportResult:
    try:
        counts = _dependent_row_counts(db, site_id)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法检查从属数据: {exc}")
    blockers = {k: v for k, v in counts.items() if v}
    if blockers:
        return mw.ImportResult(
            False, mw.ImportCode.CONFLICT,
            "--replace 拒绝执行：同名站点存在从属数据（"
            + "、".join(f"{k}={v}" for k, v in blockers.items())
            + "），其外键为 ON DELETE NO_ACTION；请先在 GUI 或显式级联流程中清理后再重建")
    return mw.ImportResult(True, mw.ImportCode.OK, "无从属数据")


def build_plan(spec: SiteSpec, db: str, owner_id: Optional[str] = None,
               replace: bool = False) -> mw.ImportResult:
    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)

    try:
        existing = mw._query_rows(
            db, f"SELECT Id FROM dbo.Sites WHERE Name={mw._sql_literal(spec.name)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取目标库: {exc}")
    if existing and not replace:
        return mw.ImportResult(False, mw.ImportCode.CONFLICT,
                               f"同名站点已存在: {spec.name}（副本库可用 --replace）")

    resolved_owner, err = mw._pick_owner(db, owner_id or spec.owner_id)
    if not resolved_owner:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "无法确定 OwnerId: " + (err or ""))

    site_id, case_id, config_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    lit = mw._sql_literal
    stmts: list[str] = []
    if replace and existing:
        old_site = existing[0][0]
        allowed = _dependency_blockers(db, old_site)
        if not allowed.ok:
            return allowed
        stmts.append(
            "DELETE FROM dbo.Configurations WHERE Case_Id IN "
            f"(SELECT Id FROM dbo.Cases WHERE Parent_Id={lit(old_site)});")
        stmts.append(f"DELETE FROM dbo.Cases WHERE Parent_Id={lit(old_site)};")
        stmts.append(f"DELETE FROM dbo.Sites WHERE Id={lit(old_site)};")
    stmts.append(
        "INSERT INTO dbo.Sites (Id,Name,Description,Version,AppType,CenterX,CenterY,Radius,"
        "Projection,OfflineTimeStampStr,TimeStampStr,CreationDate,ModificationDate,"
        "Discriminator,CreatorId,OwnerId,AccessLevel) VALUES ("
        # 空描述统一写 SQL NULL（与生产金样 DEMO 站点同构；test 站点为「测试」）。
        f"{lit(site_id)},{lit(spec.name)},{lit(spec.description or None)},NULL,NULL,"
        f"{float(spec.center_x)},{float(spec.center_y)},{float(spec.radius)},"
        f"{lit(spec.projection_wkt or None)},NULL,NULL,SYSDATETIME(),SYSDATETIME(),"
        f"{lit(DISC_SITE)},{lit(resolved_owner)},{lit(resolved_owner)},{int(spec.access_level)});"
    )
    stmts.append(
        "INSERT INTO dbo.Cases (Id,Name,Radius,CenterX,CenterY,Parent_Id,Discriminator) VALUES ("
        f"{lit(case_id)},{lit(spec.case_name or DEFAULT_CASE_NAME)},{float(spec.radius)},"
        f"{float(spec.center_x)},{float(spec.center_y)},{lit(site_id)},{lit(DISC_CASE)});"
    )
    stmts.append(
        "INSERT INTO dbo.Configurations (Id,Name,Case_Id) VALUES ("
        f"{lit(config_id)},{lit(DEFAULT_CONFIG_NAME)},{lit(case_id)});"
    )

    sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
           + chr(10).join(stmts) + chr(10) + "COMMIT;" + chr(10))
    rollback = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
                + f"DELETE FROM dbo.Configurations WHERE Id={lit(config_id)};" + chr(10)
                + f"DELETE FROM dbo.Cases WHERE Id={lit(case_id)};" + chr(10)
                + f"DELETE FROM dbo.Sites WHERE Id={lit(site_id)};" + chr(10) + "COMMIT;" + chr(10))
    plan = SitePlan(spec, site_id, case_id, config_id, resolved_owner, sql, rollback)
    return mw.ImportResult(True, mw.ImportCode.OK, "站点写入计划已生成",
                           data={"plan": plan.to_dict(), "plan_obj": plan})


def _verify(db: str, plan: SitePlan) -> mw.ImportResult:
    lit = mw._sql_literal
    try:
        rows = mw._query_rows(
            db,
            "SELECT "
            f"ISNULL(LEN(Projection),0), CenterX, CenterY, Radius, Name, "
            f"(SELECT COUNT(*) FROM dbo.Cases WHERE Parent_Id={lit(plan.site_id)}), "
            f"(SELECT COUNT(*) FROM dbo.Configurations WHERE Case_Id={lit(plan.case_id)}) "
            f"FROM dbo.Sites WHERE Id={lit(plan.site_id)};"
        )
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"写后校验失败: {exc}")
    if not rows:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "站点行不存在")
    r = rows[0]
    want_proj = len(plan.spec.projection_wkt or "")
    checks = {
        "projection_len": int(r[0]) == want_proj,
        "center_x": abs(float(r[1]) - float(plan.spec.center_x)) < 1e-6,
        "center_y": abs(float(r[2]) - float(plan.spec.center_y)) < 1e-6,
        "radius": abs(float(r[3]) - float(plan.spec.radius)) < 1e-6,
        "case_count": int(r[5]) == 1,
        "config_count": int(r[6]) == 1,
    }
    if not all(checks.values()):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                               data={"checks": checks, "row": r})
    # Cases 中心必须与 Sites 同步（antigravity 已证实 MTDLocationViewModel 读 Cases）
    try:
        case = mw._query_rows(
            db,
            f"SELECT CenterX,CenterY,Radius FROM dbo.Cases WHERE Id={lit(plan.case_id)};")
        if case:
            c = case[0]
            checks["case_center_sync"] = (
                abs(float(c[0]) - float(plan.spec.center_x)) < 1e-6
                and abs(float(c[1]) - float(plan.spec.center_y)) < 1e-6
                and abs(float(c[2]) - float(plan.spec.radius)) < 1e-6)
    except Exception:
        checks["case_center_sync"] = False
    if not checks.get("case_center_sync", False):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "Cases 中心未同步",
                               data={"checks": checks})
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过",
                           data={"checks": checks, "site_id": plan.site_id})


def apply_plan(db: str, plan: SitePlan) -> mw.ImportResult:
    if mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库")
    if not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    ok, out, err = mw._run_sql(db, plan.sql)
    if not ok:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, f"写库失败: {err or out}")
    return _verify(db, plan)


def run(spec: SiteSpec, db: str, apply: bool = False, replace: bool = False,
        out_dir: Optional[Path] = None) -> mw.ImportResult:
    if apply and mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"拒绝写生产库 {db}")
    if apply and not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    work = Path(out_dir or (Path(tempfile.gettempdir()) / "mup_site_import"))
    work.mkdir(parents=True, exist_ok=True)

    planned = build_plan(spec, db, replace=replace)
    if not planned.ok:
        return planned
    plan: SitePlan = planned.data["plan_obj"]
    (work / "plan.json").write_text(json.dumps({
        "spec": spec.__dict__, "plan": plan.to_dict(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库）",
                               data={"plan": plan.to_dict(), "out_dir": str(work)})
    applied = apply_plan(db, plan)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message,
                               data={"plan": plan.to_dict(), "out_dir": str(work)})
    return mw.ImportResult(True, mw.ImportCode.OK, "站点写入并校验通过",
                           data={"plan": plan.to_dict(), "verify": applied.data,
                                 "out_dir": str(work)})


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="数据层新建 MUP 站点（Sites+Cases+Configurations）")
    p.add_argument("--name", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--wkt-file", default=str(DEFAULT_WKT_PATH))
    p.add_argument("--center-x", type=float, default=0.0)
    p.add_argument("--center-y", type=float, default=0.0)
    p.add_argument("--radius", type=float, default=0.0)
    p.add_argument("--access-level", default="public", choices=sorted(ACCESS_LEVELS))
    p.add_argument("--case-name", default=DEFAULT_CASE_NAME)
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--replace", action="store_true", help="副本库内删除同名站点后重建")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        wkt = load_official_wkt(Path(args.wkt_file))
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 2
    spec = SiteSpec(
        name=args.name, description=args.description, projection_wkt=wkt,
        center_x=args.center_x, center_y=args.center_y, radius=args.radius,
        access_level=ACCESS_LEVELS[args.access_level], case_name=args.case_name,
    )
    result = run(spec, args.db, apply=args.apply, replace=args.replace,
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
