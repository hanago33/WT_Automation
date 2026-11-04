# encoding: utf-8
"""数据层新建 MUP「CFD 计算」对象（ComputationObject/DASCFDObject + CFD 参数快照）。

背景（2026-11-04 数据层侦察，证据文档 99_synthesis/2026-11-04-mapping-cfd-datalayer-codex.md。
该文档不在脚本仓库根 E:\\My_RF_Project 下（2026-11-05 实机核查，P3-J），本机可回读的路径是
H:\\Rea\\rea-main\\99_synthesis\\2026-11-04-mapping-cfd-datalayer-codex.md；金样键序/键数以它
和下面的 GOLDEN_SNAPSHOT_CONSTANTS 取证记录为准）
------------------------------------------------------------------------
「CFD 新计算」表单落库是 **成对的行**，一个（风向 × 热稳定度）= 一对：

    dbo.CFD                Discriminator='DASCFDObjectDataContainer'
                           Direction int / Stability int / Parameters_PersistedParameters varbinary
    dbo.ComputationObject  Discriminator='DASCFDObject'
                           Parent_Id → Cases.Id，CFD_Id → CFD.Id，Micro_AtlasId → CFDAtlas.Id（NULL=不勾 Atlas）

表单的三块参数**不在关系表里**，而是序列化进 CFD.Parameters_PersistedParameters：
两条 .NET BinaryFormatter 流拼接（金样 3189 + 5493 = 8682 字节），
每流是 `List<KeyValuePair<string,string>>`：
  · 第 1 条：40 个 `DAS_*` 键（建模区域/求解器/中微尺度耦合 …）
  · 第 2 条：77 个 `NSParameters_*` 键（nfirst/ratiomax/coefzdomain/kl_falsdt/relin …）
值里带 `|`（十档剖面）的键金样实测只有 7 个，全在第 2 条流里：
`NSParameters_kl_cmu` / `kl_coefcd` / `kl_coefcdk` / `kl_nutmin` /
`NSParameters_keps_cmu` / `keps_coefcd` / `keps_coefcdk`
（2026-11-05 从生产 CFD 行 31ca750d-cc60-4af1-99f6-53da268c6e80 的 blob 独立反解取证；
`DAS_ExpansionCoefficient_Vertical` 和 `NSParameters_nbgrid` 在快照里是标量 1.2 / 0）。
`dbo.Parameters`（Discriminator='MTDDefaultParameter'，419 行）只是**表单默认值**，
金样证据：DEMO 快照里 DAS_InitialResolution_Horizontal=100/Vertical=20，
而 dbo.Parameters 默认是 25/4（即 GUI 新建时的初值），两者不同 ⇒ 快照是逐计算的副本。
（别把这两个数混称“默认”：100/20 是**金样快照值**，25/4 是 **dbo.Parameters 表单默认值**。）

风向候选不是独立表：来自气象对象风频矩阵
`ClimatologyFrequenciesContainers.PersistedPercentagesOfWindFrequencyPerDirection`
（varbinary(max) 里装的 ASCII 文本 "0|1.78;22.5|3.05;…"，16 扇区 ×22.5°），
金样 DEMO 的 16 个 CFD.Direction = trunc(扇区起点 + 22.5) = 22,45,67,…,360。
热稳定度是**类别号**，不是气象分桶：UI 锚点 `DASParameters_ComboBoxWithWarning_Stability`
实测显示 `2 - 中性`，`dbo.Parameters` 默认 `DAS_Stability=2`，DEMO 金样 16 行也用 2；
而 `ClimatologyStabilities.Stability`（本机 C1831-125m 只有 -1）是气象数据自身的分桶
（-1=未按稳定度分类，频率 100%），两套编号不同 ⇒ 默认值取表单默认，不取分桶值。

红线
----
  · 默认 dry-run；写库只允许副本库（*_verify / *_test / *_sandbox），生产库硬拒。
  · **只建对象，不触发计算**：不写 CFDOutputs / PostCFDOutputs / Files，不启动 DASSolver。
  · 单事务 + 回滚脚本 + 写后校验（含库内 blob 的 SHA-256 比对）。
  · blob 构建的工作目录每次调用独占（out_dir/mup_cfd_import/<pid>-<uuid>），并发之间不共享。
  · 读变长值只走带 DATALENGTH 断言的定宽通道（见 mup_weather_import 读通道注释）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mup_weather_import as mw

__all__ = ["CfdSpec", "CfdPlan", "build_plan", "apply_plan", "run", "main"]

DISC_COMPUTATION = "DASCFDObject"
DISC_CFD_CONTAINER = "DASCFDObjectDataContainer"
# Atlas 是 CFD/ComputationObject 的 TPT 子类（DASAtlasModel.dll 里的
# DASAtlasDataContainer : DASCFDObjectDataContainer、DASAtlasObject : DASCFDObject）。
# CFDAtlas.Id 同时是 FK_CFDAtlas_CFD 指向 CFD.Id，所以勾 Atlas 要成对写三张表。
DISC_ATLAS_CONTAINER = "DASAtlasDataContainer"
DISC_ATLAS_OBJECT = "DASAtlasObject"
DEFAULT_CONFIG_NAME = "CaseElementConfiguration"

# 金样快照的键序（生产 DEMO 16 行 CFD 的 blob，SHA-256 99E32B47…1DEB，逐行一致）。
# 无模板可用时按这个顺序自建，保证与 MUP 自己的键集合/次序同构。
GOLDEN_DAS_KEYS = (
    "DAS_ExePath", "DAS_Stability", "DAS_MesoStep", "DAS_MesoHeight_MMC",
    "DAS_SpatialWeightingRatio_MMC", "DAS_WRFResolution_MMC", "DAS_MesoHeight", "DAS_MesoData",
    "DAS_HltForest", "DAS_Rugo2H", "DAS_AutomaticCVG", "DAS_InitialResolution_Horizontal",
    "DAS_InitialResolution_Vertical", "DAS_ExpansionCoefficient_Horizontal",
    "DAS_ExpansionCoefficient_Vertical", "DAS_LidarHorizontalCoefficient_Resolution",
    "DAS_LidarHorizontalCoefficient_Expansion", "DAS_VerticalityParameter",
    "DAS_TurbulenceModel_Type", "DAS_ResolutionCoef", "DAS_DefaultNbCellsPerAxis",
    "DAS_ForestDensity", "DAS_ForestModel", "DAS_SmoothParameter", "DAS_ConvergenceThreshold",
    "DAS_MeshSmooth", "DAS_ZSkyMax", "DAS_NumberIterations", "DAS_ForceAllIteration",
    "DAS_NumberThreads", "DAS_ConvergenceMonitoring", "DAS_MappingMonitoring",
    "DAS_NumberIterationsKeps", "DAS_SeaRoughness", "DAS_LandRoughness",
    "DAS_GenerateFieldInitializationFile", "DAS_UseFieldInitializationFile", "DAS_Orography",
    "DAS_ratiomemory", "DAS_MesoMicroCouplingMode",
)
GOLDEN_NS_KEYS = (
    "NSParameters_nfirst", "NSParameters_ratiomax", "NSParameters_coefzdomain",
    "NSParameters_forestlow", "NSParameters_forestnor", "NSParameters_foresthigh",
    "NSParameters_rlat", "NSParameters_liter", "NSParameters_literk", "NSParameters_icycle",
    "NSParameters_nbgrid", "NSParameters_resfac", "NSParameters_nbprer", "NSParameters_relax",
    "NSParameters_igmres", "NSParameters_ipreco", "NSParameters_igsm", "NSParameters_icyclek",
    "NSParameters_nbgridk", "NSParameters_resfack", "NSParameters_nbrelaxk",
    "NSParameters_nbprerk", "NSParameters_relaxk", "NSParameters_igmresk",
    "NSParameters_iprecok", "NSParameters_igsmk", "NSParameters_ibndh",
    "NSParameters_convergence", "NSParameters_vitreg", "NSParameters_rugoin",
    "NSParameters_rugoavdist", "NSParameters_Nrugoavdist", "NSParameters_Nsmall",
    "NSParameters_ltforest", "NSParameters_seaheight", "NSParameters_kappa", "NSParameters_kout",
    "NSParameters_gcoef", "NSParameters_deltaH4Shear", "NSParameters_labelMesoRef",
    "NSParameters_lengthXMesoRef", "NSParameters_lengthYMesoRef", "NSParameters_heightMesoRef",
    "NSParameters_createMicroField", "NSParameters_l2smooth", "NSParameters_rWindow2Smooth",
    "NSParameters_kl_nbrelax", "NSParameters_kl_falsdt", "NSParameters_kl_falsdtk",
    "NSParameters_kl_relin", "NSParameters_kl_relink", "NSParameters_kl_sigmay",
    "NSParameters_kl_L0", "NSParameters_kl_B1", "NSParameters_kl_cmu", "NSParameters_kl_coefcd",
    "NSParameters_kl_coefcdk", "NSParameters_kl_nutmin", "NSParameters_keps_nbrelax",
    "NSParameters_keps_falsdt", "NSParameters_keps_falsdtk", "NSParameters_keps_relin",
    "NSParameters_keps_relink", "NSParameters_keps_sigmay", "NSParameters_keps_sigmae",
    "NSParameters_keps_L0", "NSParameters_keps_B1", "NSParameters_keps_cmu",
    "NSParameters_keps_coefcd", "NSParameters_keps_coefcdk", "NSParameters_keps_c1e",
    "NSParameters_keps_c2e", "NSParameters_keps_c3e", "NSParameters_keps_c4e",
    "NSParameters_keps_c5e", "NSParameters_keps_c4k", "NSParameters_keps_c5k",
)

# 快照里与站点/机器相关、但派生规则未知的键：沿用模板值，并在计划里显式列出来。
SITE_SPECIFIC_KEYS = (
    "DAS_ExePath", "DAS_Orography", "DAS_HltForest", "DAS_MesoData", "DAS_MesoHeight",
    "DAS_MesoStep", "NSParameters_rlat", "NSParameters_labelMesoRef",
    "NSParameters_lengthXMesoRef", "NSParameters_lengthYMesoRef",
    "NSParameters_heightMesoRef",
)

# 金样快照里有、但 dbo.Parameters 里没有对应行的键（2026-11-05 逐键核对：117 个金样键里
# 恰好这 5 个不在默认值表）。没有这张表时 built-fresh 只能写 35/40 个 DAS 键，
# 键集合就和金样不同构（P3-H）。取值来自生产 CFD 行
# 31ca750d-cc60-4af1-99f6-53da268c6e80 的 blob 反解（键序同 GOLDEN_DAS_KEYS）。
GOLDEN_SNAPSHOT_CONSTANTS = {
    "DAS_ResolutionCoef": "0",
    "DAS_DefaultNbCellsPerAxis": "0",
    "DAS_GenerateFieldInitializationFile": "False",
    "DAS_UseFieldInitializationFile": "False",
    "DAS_MesoMicroCouplingMode": "None",
}

# 表单字段 → 快照键。值一律以字符串写入 blob（与 MUP 一致）。
# 键名与 UI 锚点 DASParameters_*_{HorRes,VerRes,NumberThreads,NbIterations,
# ConvergenceThreshold,HorizontalExpansionCoef,Stability} 一一对齐（2026-11-05 校准）。
FORM_KEY_MAP = {
    "min_h_res": "DAS_InitialResolution_Horizontal",
    "min_v_res": "DAS_InitialResolution_Vertical",
    "exp_coef_h": "DAS_ExpansionCoefficient_Horizontal",
    "iterations": "DAS_NumberIterations",
    "threads": "DAS_NumberThreads",
    "convergence_threshold": "DAS_ConvergenceThreshold",
    "convergence_monitoring": "DAS_ConvergenceMonitoring",
    "auto_cvg": "DAS_AutomaticCVG",
    "force_all_iteration": "DAS_ForceAllIteration",
    "meso_micro_coupling": "DAS_MesoMicroCouplingMode",
    "turbulence_model": "DAS_TurbulenceModel_Type",
}

# 快照里 MUP 侧按 int 解释的表单键：写进非数字就是一格识别不了的读数（把关 P1-2）。
# `_form_value` 是格式化器不是校验器（float() 失败就原样返回文本），所以类型门必须落在
# CfdSpec.validate()：GUI 与 CLI（--iterations/--threads/--set）共用同一个收口。
INTEGER_SNAPSHOT_KEYS = ("DAS_NumberIterations", "DAS_NumberThreads")


def _sql_float(value: float) -> str:
    return repr(float(value))


def _fmt(value: object) -> str:
    """表单值 → 快照字符串（bool 用 MUP 的 'True'/'False'，浮点用最短可往返且以 . 为小数点）。"""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        text = repr(value)
        return text
    return str(value)


PS_BLOB_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$spec = (Get-Content -LiteralPath $env:PAIRS_JSON -Raw -Encoding UTF8) | ConvertFrom-Json
$outDir = $env:OUT_DIR
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$lines = New-Object System.Collections.Generic.List[string]
function Emit($k, $v) { $lines.Add([string]$k + "=" + [string]$v) }
$kvp = [System.Collections.Generic.KeyValuePair[[string],[string]]]
$listT = [System.Collections.Generic.List[object]].GetGenericTypeDefinition().MakeGenericType($kvp)
# PowerShell 会把 return 出来的 IEnumerable 摊平成 Object[]，被序列化的图类型就变了
# （MUP 端反序列化成 List，会直接失败）。所以这里一律就地修改同一个 List 实例，
# 绝不通过函数返回值传递集合。
function New-Pair($k, $v) { return [Activator]::CreateInstance($kvp, [object[]]@([string]$k, [string]$v)) }
function Fill-FromPairs($l, [object]$pairs) {
  if ($null -eq $pairs) { return }
  foreach ($p in @($pairs)) { [void]$l.Add((New-Pair $p.k $p.v)) }
}
function Patch-Into($l, [object]$pairs) {
  if ($null -eq $pairs) { return }
  foreach ($p in @($pairs)) {
    $key = [string]$p.k; $val = [string]$p.v; $hit = -1
    for ($i = 0; $i -lt $l.Count; $i++) { if ($l[$i].Key -eq $key) { $hit = $i; break } }
    if ($hit -ge 0) { $l[$hit] = (New-Pair $key $val) } else { [void]$l.Add((New-Pair $key $val)) }
  }
}
function Save-Serialized($o, [string]$path) {
  $m = New-Object System.IO.MemoryStream
  $bf = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
  $bf.Serialize($m, $o)
  [System.IO.File]::WriteAllBytes($path, $m.ToArray())
}
try {
  $tpl = [string]$spec.template
  $tplFile = ($tpl -ne '') -and (Test-Path -LiteralPath $tpl)
  $l1 = [Activator]::CreateInstance($listT)
  $l2 = [Activator]::CreateInstance($listT)
  # 模板自带的键即“已解释”，孤儿判定只针对既不在期望集合也不在模板里的键
  $tplKeys = @{}
  if ($tplFile) {
    # 模板优先：反序列化 MUP 自己产出的两条流，保留其后备数组容量与键序
    $bytes = [System.IO.File]::ReadAllBytes($tpl)
    $ms = New-Object System.IO.MemoryStream(,$bytes)
    $bf = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $d1 = $bf.Deserialize($ms)
    if ($d1.GetType().FullName -notlike 'System.Collections.Generic.List*') { throw 'template part1 is not a List' }
    $l1 = $d1
    $cut = [int]$ms.Position
    $rest = New-Object byte[] ($bytes.Length - $cut)
    [Array]::Copy($bytes, $cut, $rest, 0, $rest.Length)
    $ms2 = New-Object System.IO.MemoryStream(,$rest)
    $bf2 = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $l2 = $bf2.Deserialize($ms2)
    if ($l2.GetType().FullName -notlike 'System.Collections.Generic.List*') { throw 'template part2 is not a List' }
    foreach ($p in @($l1) + @($l2)) { $tplKeys[[string]$p.Key] = $true }
    Emit 'TPL_KEY_COUNT' $tplKeys.Count
    Emit 'SOURCE' 'template'
    Emit 'TPL_CONSUMED' $cut
    Emit 'TPL_TOTAL' $bytes.Length
    if ($cut -ge $bytes.Length) { Emit 'TPL_WARN' 'template holds a single stream' }
  } else {
    Fill-FromPairs $l1 $spec.das_base
    Fill-FromPairs $l2 $spec.ns_base
    foreach ($p in @($l1) + @($l2)) { $tplKeys[[string]$p.Key] = $true }
    Emit 'SOURCE' 'built-fresh'
  }
  # 表单默认值（dbo.Parameters）+ 用户覆盖，全部就地写进同一个 List 实例
  Patch-Into $l1 $spec.das_base
  Patch-Into $l1 $spec.das_overrides
  Patch-Into $l2 $spec.ns_base
  Patch-Into $l2 $spec.ns_overrides
  $f1 = Join-Path $outDir 'part1.bin'
  $f2 = Join-Path $outDir 'part2.bin'
  Save-Serialized $l1 $f1
  Save-Serialized $l2 $f2
  $b1 = [System.IO.File]::ReadAllBytes($f1)
  $b2 = [System.IO.File]::ReadAllBytes($f2)
  $all = New-Object byte[] ($b1.Length + $b2.Length)
  [Array]::Copy($b1, 0, $all, 0, $b1.Length)
  [Array]::Copy($b2, 0, $all, $b1.Length, $b2.Length)
  $binPath = Join-Path $outDir 'cfd_params.bin'
  [System.IO.File]::WriteAllBytes($binPath, $all)
  Emit 'PART1_TYPE' $l1.GetType().Name
  Emit 'PART2_TYPE' $l2.GetType().Name
  Emit 'PART1_COUNT' $l1.Count
  Emit 'PART2_COUNT' $l2.Count
  Emit 'PART1_LEN' $b1.Length
  Emit 'PART2_LEN' $b2.Length
  Emit 'BLOB_LEN' $all.Length
  $sha = [System.Security.Cryptography.SHA256]::Create().ComputeHash($all)
  Emit 'BLOB_SHA' (([BitConverter]::ToString($sha)) -replace '-','')
  # 反解自检：把产物再读回来比对，而不是信任内存里的对象
  $want = @{}
  foreach ($name in 'das_base','ns_base','das_overrides','ns_overrides') {
    $arr = $spec.$name
    if ($null -ne $arr) { foreach ($p in @($arr)) { $want[[string]$p.k] = [string]$p.v } }
  }
  $rb = [System.IO.File]::ReadAllBytes($binPath)
  $rs = New-Object System.IO.MemoryStream(,$rb)
  $rf = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
  $rl1 = $rf.Deserialize($rs)
  $rcut = [int]$rs.Position
  $rr = New-Object byte[] ($rb.Length - $rcut)
  [Array]::Copy($rb, $rcut, $rr, 0, $rr.Length)
  $rs2 = New-Object System.IO.MemoryStream(,$rr)
  $rf2 = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
  $rl2 = $rf2.Deserialize($rs2)
  $bad = 0
  $orphans = @()
  foreach ($pair in (@($rl1) + @($rl2))) {
    $pk = [string]$pair.Key
    if (-not $want.ContainsKey($pk)) {
      if (-not $tplKeys.ContainsKey($pk)) { $orphans += $pk }
      continue
    }
    if ([string]$want[$pk] -ne [string]$pair.Value) { $bad++ }
  }
  Emit 'RT_PART1_TYPE' $rl1.GetType().FullName
  Emit 'RT_CUT' $rcut
  Emit 'RT_PART1_COUNT' $rl1.Count
  Emit 'RT_PART2_COUNT' $rl2.Count
  Emit 'ROUNDTRIP_MISMATCH' $bad
  Emit 'TEMPLATE_ONLY_KEYS' ($orphans -join ',')
  # 站点/机器相关的键：沿用模板值，但必须让调用方看见（派生规则未知）
  $watch = @()
  if ($null -ne $spec.watch_keys) { $watch = @($spec.watch_keys) }
  $carried = @()
  if ($watch.Count -gt 0) {
    foreach ($pair in (@($rl1) + @($rl2))) {
      foreach ($wk in $watch) {
        if ([string]$pair.Key -eq [string]$wk) {
          $val = [string]$pair.Value
          if ($val.Length -gt 60) { $val = $val.Substring(0, 60) + '...' }
          $carried += ([string]$wk + '=' + $val)
        }
      }
    }
  }
  Emit 'CARRIED_KEYS' ($carried -join ' | ')
  if ($tplFile) {
    $g = [System.IO.File]::ReadAllBytes($tpl)
    $identical = ($g.Length -eq $all.Length)
    if ($identical) {
      for ($i = 0; $i -lt $g.Length; $i++) {
        if ($g[$i] -ne $all[$i]) { $identical = $false; Emit 'TEMPLATE_FIRST_DIFF' $i; break }
      }
    }
    Emit 'SAME_AS_TEMPLATE' $identical
  }
  Emit 'STATUS' 'OK'
} catch {
  Emit 'STATUS' 'FAIL'
  Emit 'ERROR' $_.Exception.Message
}
[System.IO.File]::WriteAllLines((Join-Path $outDir 'result.txt'), $lines, [System.Text.Encoding]::UTF8)
exit 0
'''

# 库内 blob 反解直证（P1-B 的自检补位）：从 dbo.CFD 取字节、拆两条流、逐个断言
# 「计划请求的表单项在库里就是那个值」。这条断言不看 blob SHA，所以并发覆盖
# pairs.json/blob 时不会被「计划自己的 SHA == 库里的 SHA」自证掩盖。
PS_READBACK_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$lines = New-Object System.Collections.Generic.List[string]
function Emit($k, $v) { [void]$lines.Add([string]$k + "=" + [string]$v) }
try {
  $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MUP_READBACK_B64)) | ConvertFrom-Json
  $cs = "Server=" + $env:MUP_READBACK_SERVER + ";Database=" + $env:MUP_READBACK_DB + ";Integrated Security=True;"
  $conn = New-Object System.Data.SqlClient.SqlConnection $cs
  $conn.Open()
  foreach ($item in @($payload.rows)) {
    $id = [string]$item.id
    $q = $conn.CreateCommand()
    $q.CommandText = 'SELECT Parameters_PersistedParameters FROM dbo.CFD WHERE Id=@id'
    [void]$q.Parameters.AddWithValue('@id', $id)
    $o = $q.ExecuteScalar()
    if ($null -eq $o) { Emit ('NOROW_' + $id) '1'; continue }
    $bytes = [byte[]]$o
    $ms = New-Object System.IO.MemoryStream(,$bytes)
    $bf = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $p1 = $bf.Deserialize($ms)
    $cut = [int]$ms.Position
    $rest = New-Object byte[] ($bytes.Length - $cut)
    [Array]::Copy($bytes, $cut, $rest, 0, $rest.Length)
    $ms2 = New-Object System.IO.MemoryStream(,$rest)
    $bf2 = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $p2 = $bf2.Deserialize($ms2)
    if ($p1.GetType().FullName -notlike 'System.Collections.Generic.List*') { Emit ('NOTLIST1_' + $id) $p1.GetType().Name }
    if ($p2.GetType().FullName -notlike 'System.Collections.Generic.List*') { Emit ('NOTLIST2_' + $id) $p2.GetType().Name }
    $have = @{}
    foreach ($pair in (@($p1) + @($p2))) { $have[[string]$pair.Key] = [string]$pair.Value }
    $bad = @()
    $absent = @()
    foreach ($want in @($item.expected)) {
      $wk = [string]$want.k
      $wv = [string]$want.v
      if (-not $have.ContainsKey($wk)) { $absent += $wk }
      elseif ($have[$wk] -ne $wv) { $bad += ($wk + '!=' + $have[$wk]) }
    }
    Emit ('META_' + $id) ($bytes.Length.ToString() + '|' + $cut + '|' + $p1.Count + '|' + $p2.Count)
    Emit ('BAD_' + $id) $bad.Count
    Emit ('ABSENT_' + $id) $absent.Count
    if ($bad.Count -gt 0) { Emit ('BADKEYS_' + $id) (($bad) -join ';') }
    if ($absent.Count -gt 0) { Emit ('ABSENTKEYS_' + $id) (($absent) -join ';') }
  }
  $conn.Close()
  Emit 'STATUS' 'OK'
} catch {
  Emit 'STATUS' 'FAIL'
  Emit 'ERROR' $_.Exception.Message
}
[System.IO.File]::WriteAllLines($env:MUP_READBACK_OUT, $lines, [System.Text.Encoding]::UTF8)
exit 0
'''


@dataclass
class CfdSpec:
    site: str
    # 工况选择器（Name 或 Id）。None/"" = 不指定：单工况时自动取，多工况时 fail-closed（P2-E）。
    case: Optional[str] = None
    directions: Optional[list[int]] = None
    stability: Optional[int] = None
    climatology: str = ""
    overrides: dict[str, str] = field(default_factory=dict)
    atlas: bool = False
    atlas_nb_micro_x: int = 0
    atlas_nb_micro_y: int = 0
    # Atlas 行的 CFD 父行取值（Direction/Stability/快照）在本机没有任何金样可对照
    # （CFDAtlas 0 行，DEMO/test 都未勾 Atlas）⇒ 只有显式接受未验证才写。
    allow_unverified_atlas: bool = False
    template_cfd_id: str = ""
    require_mapping: bool = True
    require_environment: bool = True
    require_element: bool = True

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if not str(self.site or "").strip():
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "站点名称/Id 不能为空"))
        if self.case is not None and not str(self.case).strip():
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "--case 给了值但是空白：要么不给（单工况时自动取），要么给工况 Name/Id"))
        if self.directions is not None:
            if not self.directions:
                issues.append(mw.ImportIssue(
                    mw.ImportCode.INPUT, "风向列表为空：GUI 里 0 个选中时不会产生任何计算对象"))
            for value in self.directions:
                if int(value) < 0 or int(value) > 360:
                    issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "风向必须在 0-360: %s" % value))
        if self.stability is not None and int(self.stability) < -1:
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT,
                                         "热稳定度只能是 -1（全类）或 >=0 的类别编号: %s" % self.stability))
        if self.atlas and (self.atlas_nb_micro_x <= 0 or self.atlas_nb_micro_y <= 0):
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT,
                                         "--atlas 需要同时给正的 --atlas-nb-micro-x/--atlas-nb-micro-y"))
        if self.atlas and not self.allow_unverified_atlas:
            issues.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                "Atlas 是 CFD 的 TPT 子类：勾上要多写 CFD 父行(DASAtlasDataContainer) + CFDAtlas + "
                "计算对象(DASAtlasObject) 三行。本库 CFDAtlas 有 0 行，没有任何金样能告诉我们 Atlas "
                "父行的 Direction/Stability/参数快照该填什么；实测只写 CFDAtlas 会被 FK_CFDAtlas_CFD "
                "拒掉。确认接受这份未验证写入才加 --allow-unverified-atlas"))
        for key, value in (self.overrides or {}).items():
            if not str(key).startswith(("DAS_", "NSParameters_")):
                issues.append(mw.ImportIssue(
                    mw.ImportCode.INPUT, "参数键必须以 DAS_ 或 NSParameters_ 开头: " + str(key)))
            if key == "DAS_ConvergenceThreshold":
                issues.extend(_convergence_threshold_issues(value))
            elif key in INTEGER_SNAPSHOT_KEYS:
                issues.extend(_positive_int_issues(key, value))
        return issues


def _convergence_threshold_issues(value: object) -> list[mw.ImportIssue]:
    """收敛阈值按 (0,100] 的百分数校验（默认表 95，应用下限键 90）。"""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return [mw.ImportIssue(mw.ImportCode.INPUT,
                               "DAS_ConvergenceThreshold 需要是数字（百分数）: %s" % value)]
    if not 0.0 < number <= 100.0:
        return [mw.ImportIssue(
            mw.ImportCode.INPUT,
            "DAS_ConvergenceThreshold=%s 超出 (0,100] 的百分数区间" % value)]
    return []


_POSITIVE_INT_RE = re.compile(r"[1-9][0-9]*")


def _positive_int_issues(key: str, value: object) -> list[mw.ImportIssue]:
    """按整数解释的快照键只收规范十进制正整数："abc"/""/"0"/"-3"/"10.5"/"1e3" 一律 INPUT。

    和收敛阈值同级（把关 P1-2）：这两个键在 MUP 里是 NumericUpDown，快照里出现非整数
    字符串会在「打开副本库识别该 CFD 对象」时变成读不出的参数，而那时已经没有回滚入口。
    只收 `1-9` 起头的纯数字：`007`/`+10`/`10.` 这类写法即便能 float() 成功，也不是
    `_form_value` 会产出的规范形态，一律弹回比按 int() 静默取整更诚实。
    """
    if not _POSITIVE_INT_RE.fullmatch(str(value).strip()):
        return [mw.ImportIssue(mw.ImportCode.INPUT,
                               "%s 需要是正整数: %s" % (key, value))]
    return []


@dataclass
class CfdPlan:
    spec: CfdSpec
    site_id: str
    case_id: str
    rows: list[dict]
    atlas_rows: list[dict]
    blob: bytes
    blob_sha: str
    template_source: str
    template_cfd_id: str
    stability: int
    # 计划请求写进快照的表单项（键→值）。写后校验拿它和**库内** blob 反解结果对比，
    # 这条断言不依赖 blob_sha，所以 blob 被并发覆盖时不会被自证掩盖（P1-B）。
    expected_pairs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sql: str = ""
    rollback_sql: str = ""

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "case_id": self.case_id,
            "row_count": len(self.rows),
            "directions": [r["direction"] for r in self.rows],
            "stability": self.stability,
            "blob_len": len(self.blob),
            "blob_sha256": self.blob_sha,
            "template_source": self.template_source,
            "template_cfd_id": self.template_cfd_id,
            "expected_pairs": dict(self.expected_pairs),
            "atlas_rows": len(self.atlas_rows),
            "atlas_cfd_ids": [a["atlas_cfd_id"] for a in self.atlas_rows],
            "atlas_computation_ids": [a["atlas_computation_id"] for a in self.atlas_rows],
            "computation_ids": [r["computation_id"] for r in self.rows],
            "cfd_ids": [r["cfd_id"] for r in self.rows],
            "notes": self.notes,
        }


# --- 只读探测 --------------------------------------------------------------
def _site_and_case(db: str, key: str,
                   case_selector: Optional[str] = None) -> tuple[Optional[dict], mw.ImportResult]:
    """定位站点与工况。多行命中一律 fail-closed（P2-E）。

    以前这里对 `dbo.Cases … ORDER BY Name` 直接取 cases[0]：站点有多个工况时，
    CFD 对象会被静默写进「名字最前」的那个工况（实测第二个工况叫 AAlphaSecondCase 时
    就选中了它，且没有任何歧义提示）。现在与 mup_synthesis_import._resolve_case 同口径：
    多工况必须显式 --case，选择器命中多行也要改用 Id。
    """
    lit = mw._sql_literal
    rows = mw._query_rows(db, "SELECT Id, Name, CenterX, CenterY FROM dbo.Sites WHERE Name="
                              + lit(key) + " OR Id=" + lit(key) + ";")
    if not rows:
        return None, mw.ImportResult(False, mw.ImportCode.DB_GUARD, "站点不存在: " + str(key))
    if len(rows) > 1:
        return None, mw.ImportResult(
            False, mw.ImportCode.INPUT,
            "站点选择器 %s 命中 %d 行，请用 Sites.Id（同名站点在库里是合法的）" % (key, len(rows)))
    site = {"id": rows[0][0], "name": rows[0][1], "center_x": float(rows[0][2]),
            "center_y": float(rows[0][3])}
    cases = mw._query_rows(db, "SELECT Id, Name FROM dbo.Cases WHERE Parent_Id="
                               + lit(site["id"]) + " ORDER BY Name;")
    if not cases:
        # P4：缺前置条件不是库守卫失败，编排层要看到的是「补 Case」而不是「换库名」
        # （与 mup_mapping_import.py 同口径，同一个语义在两个接口里不能有两个 code）。
        return None, mw.ImportResult(
            False, mw.ImportCode.PRECONDITION,
            "站点下没有 Case，无法挂 CFD 计算对象：先用 mup_site_import.py 建站点/工况再来",
            issues=[mw.ImportIssue(mw.ImportCode.PRECONDITION,
                                   "站点 %s (Id=%s) 下没有 Case 行" % (site["name"], site["id"]),
                                   {"site_id": site["id"], "missing": "dbo.Cases"})])
    if case_selector:
        hit = [c for c in cases if c[0] == case_selector or c[1] == case_selector]
        if not hit:
            names = ", ".join("%s(%s)" % (c[1], c[0]) for c in cases)
            return None, mw.ImportResult(
                False, mw.ImportCode.INPUT,
                "工况 %s 不在站点 %s 下；可用: %s" % (case_selector, site["name"], names))
        if len(hit) > 1:
            return None, mw.ImportResult(
                False, mw.ImportCode.INPUT,
                "工况选择器 %s 命中 %d 行，请用 Cases.Id" % (case_selector, len(hit)))
        site["case_id"] = hit[0][0]
        site["case_name"] = hit[0][1]
    elif len(cases) > 1:
        names = ", ".join("%s(%s)" % (c[1], c[0]) for c in cases)
        return None, mw.ImportResult(
            False, mw.ImportCode.PRECONDITION,
            "站点有 %d 个工况，请用 --case 指定: %s" % (len(cases), names))
    else:
        site["case_id"] = cases[0][0]
        site["case_name"] = cases[0][1]
    site["case_count"] = len(cases)
    return site, mw.ImportResult(True, mw.ImportCode.OK, "站点/案例已定位")


def _decode_percentages(text: str) -> list[tuple[float, float]]:
    """'deg|pct;…' → [(扇区起点, 频率%)]。库内是 varbinary(max) 装的 ASCII 文本，
    个别导出通道会再包一层 base64，故两种都吃。"""
    raw = (text or "").strip()
    if not raw:
        return []
    if "|" not in raw:
        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            return []
    out: list[tuple[float, float]] = []
    for chunk in raw.split(";"):
        if "|" not in chunk:
            continue
        head, _, tail = chunk.partition("|")
        try:
            out.append((float(head), float(tail)))
        except ValueError:
            continue
    return out


def _sector_directions(pairs: list[tuple[float, float]]) -> list[int]:
    """风频矩阵扇区 → CFD.Direction（金样规则：trunc(起点 + 扇区宽)）。"""
    if not pairs:
        return []
    step = 360.0 / len(pairs)
    return [int(math.floor(start + step + 1e-6)) for start, pct in pairs if pct > 0.0]


def _dotnet_scalar_to_file(db: str, sql: str, out_path: Path) -> mw.ImportResult:
    """取回可能被 sqlcmd 截断/与 '|' 分隔符冲突的标量值：用 .NET SqlClient 直读落文件。

    为什么必须绕开 sqlcmd：PersistedPercentagesOfWindFrequencyPerDirection 是
    varbinary(max) 装的 'deg|pct;…' 文本，里面的 '|' 会打断 _query_rows 的列切分，
    而 sqlcmd 对变长列还有显示宽度截断。
    """
    checked = _check_db_name(db)
    if not checked.ok:
        return checked
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$cs='Server=" + os.environ.get("MUP_LOCALDB_SERVER", r"(localdb)\Meteodyn")
        + ";Database=" + checked.data["db"] + ";Integrated Security=True;'\n"
        "$c=New-Object System.Data.SqlClient.SqlConnection $cs;$c.Open()\n"
        "$q=$c.CreateCommand();$q.CommandText=@'\n" + sql + "\n'@\n"
        "$v=$q.ExecuteScalar()\n"
        "$c.Close()\n"
        "if ($null -eq $v) { [System.IO.File]::WriteAllText('" + str(out_path) + "','',[Text.Encoding]::UTF8); exit 0 }\n"
        "if ($v -is [byte[]]) { $v=[System.Text.Encoding]::ASCII.GetString($v) }\n"
        "[System.IO.File]::WriteAllText('" + str(out_path) + "',[string]$v,[System.Text.Encoding]::UTF8)\n")
    tmp = out_path.with_suffix(".ps1")
    tmp.write_text(script, encoding="utf-8-sig")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(tmp)], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return mw.ImportResult(False, mw.ImportCode.COMPUTE, "读取标量失败: " + str(exc))
    finally:
        tmp.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file():
        return mw.ImportResult(False, mw.ImportCode.COMPUTE,
                               "读取标量失败: " + proc.stderr.decode("utf-8", "replace")[-400:])
    return mw.ImportResult(True, mw.ImportCode.OK, "标量已取回",
                           data={"text": out_path.read_text(encoding="utf-8-sig").strip()})


def _climatology_candidates(db: str, site_id: str, work_dir: Path) -> list[dict]:
    """站点测风塔 Label ↔ Climatologies.Name 同名关联（库内无 FK，这是观测到的唯一纽带）。"""
    lit = mw._sql_literal
    rows = mw._query_rows(
        db,
        "SELECT c.Id, c.Name, h.Id, h.Meter, s.Id, s.Stability, s.FrequencyPercentage, "
        "ISNULL(fc.Id,'') "
        "FROM dbo.Climatologies c "
        "JOIN dbo.ClimatologyHeights h ON h.Climatology_Id=c.Id "
        "JOIN dbo.ClimatologyStabilities s ON s.ClimatologyHeight_Id=h.Id "
        "LEFT JOIN dbo.ClimatologyFrequenciesContainers fc ON fc.ClimatologyStability_Id=s.Id "
        "WHERE c.Name IN (SELECT Label FROM dbo.Elements WHERE Parent_Id=" + lit(site_id)
        + " AND Discriminator='MTDElementMast') ORDER BY c.Name, h.Meter, s.Stability;")
    out = []
    for r in rows:
        text = ""
        container_id = r[7] if len(r) > 7 else ""
        if container_id:
            got = _dotnet_scalar_to_file(
                db, "SELECT CONVERT(varchar(max), PersistedPercentagesOfWindFrequencyPerDirection) "
                    "FROM dbo.ClimatologyFrequenciesContainers WHERE Id=" + lit(container_id) + ";",
                work_dir / ("pct_%s.txt" % container_id))
            if got.ok:
                text = got.data["text"]
        out.append({"climatology_id": r[0], "climatology_name": r[1], "height_id": r[2],
                    "meter": float(r[3]), "stability_id": r[4], "stability": int(r[5]),
                    "frequency_percentage": float(r[6] or 0),
                    "percentages_raw": text,
                    "directions": _sector_directions(_decode_percentages(text))})
    return out


def _decode_hex_text(cell: str) -> str:
    """dbo.Parameters.Value 走 hex 通道：值里就有 '|'（如 kl_coefcd 的十档剖面），
    而读通道用 '|' 切列，直接读会被截成第一段。

    注意这条通道本身不保证「读全」：hex 只能保字符集，不保长度。长度由
    _default_parameters 里的 DATALENGTH 断言负责（P1-A）。
    """
    raw = bytes.fromhex(cell)
    for enc in ("utf-16-le", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


_DEFAULT_PARAMETER_WHERE = (
    "WHERE Discriminator='MTDDefaultParameter' "
    "AND ([Key] LIKE 'DAS!_%' ESCAPE '!' OR [Key] LIKE 'NSParameters!_%' ESCAPE '!')")


def _default_parameters(db: str) -> mw.ImportResult:
    """dbo.Parameters 的 DAS_/NSParameters_ 表单默认值（data={"params": {key: value}}）。

    值既含 '|'（十档剖面，会被列分隔符切断），也可能远超 sqlcmd 默认 256 字符显示宽
    （`NSParameters_kl_cmu` 真值 108 字符 / 10 段，默认通道只读回 64 字符 / 6 段，
    而且 rc 仍是 0）。所以：先探最大字节数定 -y 宽度，再带 DATALENGTH 回来逐行断言
    `len(hex) == 2*DATALENGTH`；任何一行不完整就整体 fail-closed（VERIFY），
    绝不带被裁剪的默认值去拼参数快照（P1-A）。
    """
    probe = ("SELECT ISNULL(MAX(DATALENGTH(Value)),0) FROM dbo.Parameters "
             + _DEFAULT_PARAMETER_WHERE + ";")
    sql = ("SELECT [Key], ISNULL(DATALENGTH(Value),0), "
           "CONVERT(varchar(max), CAST(ISNULL(Value,'') AS varbinary(max)),2) "
           "FROM dbo.Parameters " + _DEFAULT_PARAMETER_WHERE + ";")
    try:
        width = mw.var_column_width(db, probe)
        rows = mw.read_sized_rows(db, sql, width)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "读取 dbo.Parameters 默认值的通道不可用: " + str(exc))
    out: dict[str, str] = {}
    for r in rows:
        if len(r) < 3:
            return mw.ImportResult(
                False, mw.ImportCode.VERIFY,
                "dbo.Parameters 默认值行只有 %d 个字段（需要 键/DATALENGTH/hex 三个），"
                "读通道列错位: %r" % (len(r), r))
        reason = mw.hex_length_reason(r[2], r[1])
        if reason:
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "默认值 %s 读取不完整: %s" % (r[0], reason))
        try:
            out[r[0]] = _decode_hex_text(r[2])
        except ValueError:
            # 不是合法 hex：CONVERT(...,2) 不会产出这种东西，原样保留并让它进计划备注
            out[r[0]] = r[2]
    return mw.ImportResult(True, mw.ImportCode.OK, "表单默认值已读取",
                           data={"params": out, "hex_width": width})


def _pick_template(db: str, explicit: str) -> tuple[str, str]:
    """选一个已有 CFD 行做 blob 模板：显式 Id 优先，否则任取带快照的行（金样 DEMO）。"""
    lit = mw._sql_literal
    if explicit:
        rows = mw._query_rows(db, "SELECT Id FROM dbo.CFD WHERE Id=" + lit(explicit)
                              + " AND Parameters_PersistedParameters IS NOT NULL;")
        return (rows[0][0] if rows else "", "")
    rows = mw._query_rows(
        db, "SELECT TOP 1 Id FROM dbo.CFD WHERE Parameters_PersistedParameters IS NOT NULL "
            "ORDER BY DATALENGTH(Parameters_PersistedParameters) DESC, Id;")
    return (rows[0][0] if rows else "", "")


def _existing_direction_stability(db: str, case_id: str) -> dict[int, set[int]]:
    """该工况已有的 CFD `Direction → {Stability}`，给重复拒闸门用（P2-D）。

    dbo.CFD 上没有 (Direction, Stability) 唯一约束，本接口又是「只追加」，所以两次相同
    的 --directions/--stability 会落两份同参数计算对象（实测 3→4 行）。库里查不出来，
    就只能由接口自己把重复挡在计划期。
    """
    rows = mw._query_rows(
        db, "SELECT DISTINCT c.Direction, c.Stability FROM dbo.CFD c "
            "JOIN dbo.ComputationObject co ON co.CFD_Id=c.Id "
            "WHERE co.Parent_Id=" + mw._sql_literal(case_id) + ";")
    out: dict[int, set[int]] = {}
    for r in rows:
        if len(r) < 2:
            raise RuntimeError("已有 CFD 行不是 (Direction, Stability) 两列: %r" % (r,))
        out.setdefault(int(float(r[0])), set()).add(int(float(r[1])))
    return out


def _download_blob(db: str, cfd_id: str, out_path: Path) -> mw.ImportResult:
    """把模板 blob 完整取到本地：sqlcmd 会截断长文本，所以用 .NET SqlClient 直读。"""
    checked = _check_db_name(db)
    if not checked.ok:
        return checked
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$cs='Server=" + os.environ.get("MUP_LOCALDB_SERVER", r"(localdb)\Meteodyn")
        + ";Database=" + checked.data["db"] + ";Integrated Security=True;'\n"
        "$c=New-Object System.Data.SqlClient.SqlConnection $cs;$c.Open()\n"
        "$q=$c.CreateCommand();$q.CommandText=\"SELECT Parameters_PersistedParameters FROM dbo.CFD"
        " WHERE Id='" + cfd_id.replace("'", "''") + "'\"\n"
        "$b=$q.ExecuteScalar();$c.Close()\n"
        "[System.IO.File]::WriteAllBytes('" + str(out_path).replace("'", "''") + "',$b)\n"
        "Write-Output $b.Length\n")
    tmp = out_path.with_suffix(".ps1")
    tmp.write_text(script, encoding="utf-8-sig")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(tmp)], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return mw.ImportResult(False, mw.ImportCode.COMPUTE, "读取模板 blob 失败: " + str(exc))
    finally:
        tmp.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file():
        return mw.ImportResult(False, mw.ImportCode.COMPUTE,
                               "读取模板 blob 失败: " + proc.stderr.decode("utf-8", "replace")[-500:])
    return mw.ImportResult(True, mw.ImportCode.OK, "模板 blob 已取回",
                           data={"size": int(out_path.stat().st_size)})


def _check_db_name(db: str) -> mw.ImportResult:
    """库名要拼进 .NET 连接串，只允许裸标识符（副本库命名规范 *_verify/_test/_sandbox 天然满足）。"""
    name = str(db or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "库名不是裸标识符，拒绝拼入连接串: " + name)
    return mw.ImportResult(True, mw.ImportCode.OK, "库名可用", data={"db": name})


def _host_int(kv: dict[str, str], key: str) -> Optional[int]:
    """宿主 result.txt 里的整数计数；缺失或不合法 ⇒ None（调用方按失败处理，不猜 0）。"""
    raw = str(kv.get(key, "")).strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_readback_kv(kv: dict[str, str], rows: list[dict]) -> mw.ImportResult:
    """把宿主 result.txt 的读数折成布尔判定；任何一项不符 ⇒ VERIFY 并保留明细。

    判定只用「库里反解出来的键值」和「计划请求的表单项」这两份事实，不看 blob SHA。
    """
    checks: dict[str, bool] = {}
    detail: dict[str, dict[str, object]] = {}
    for row in rows:
        rid = str(row["id"])
        tag = rid[:8]
        meta = str(kv.get("META_" + rid) or "")
        chunks = meta.split("|")
        if len(chunks) != 4:
            checks["readback_row_" + tag] = False
            detail[rid] = {"error": "库里没有这行或反解没有返回 META"}
            continue
        checks["readback_row_" + tag] = True
        try:
            byte_len, cut, n1, n2 = (int(chunks[0]), int(chunks[1]), int(chunks[2]), int(chunks[3]))
        except ValueError:
            checks["readback_meta_" + tag] = False
            detail[rid] = {"error": "META 不是 4 个整数: " + meta}
            continue
        bad = _host_int(kv, "BAD_" + rid)
        absent = _host_int(kv, "ABSENT_" + rid)
        checks["readback_streams_" + tag] = byte_len > 0 and n1 > 0 and n2 > 0
        checks["readback_type_" + tag] = not (kv.get("NOTLIST1_" + rid) or kv.get("NOTLIST2_" + rid))
        checks["readback_pairs_" + tag] = (bad == 0 and absent == 0)
        detail[rid] = {"bytes": byte_len, "cut": cut, "part1": n1, "part2": n2,
                       "bad": bad, "absent": absent,
                       "bad_keys": kv.get("BADKEYS_" + rid, ""),
                       "absent_keys": kv.get("ABSENTKEYS_" + rid, "")}
    if not all(checks.values()):
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "库内参数快照内容与计划请求的表单项不一致（与 SHA 无关的直证）",
                               data={"checks": checks, "readback": detail})
    return mw.ImportResult(True, mw.ImportCode.OK, "库内参数快照内容直证通过",
                           data={"checks": checks, "readback": detail})


def _readback_blob_pairs(db: str, rows: list[dict], timeout: int = 180) -> mw.ImportResult:
    """库内 blob 反解直证：断言「计划请求的表单项 == 库里快照里的值」，与 blob SHA 无关。

    rows = [{"id": CFD.Id, "expected": {key: value}}, ...]
    为什么不能只比 SHA：`plan.blob_sha` 就是本次构建产物自己的 SHA，一旦 pairs.json/blob
    被并发覆盖，计划和库里会同时是那份错的值 ⇒ 计数对、内容错也照样「校验通过」（P1-B）。
    宿主脚本和产物都放在本次调用独占的临时目录里（不复用 mup_cfd_import 共享目录）。
    """
    checked = _check_db_name(db)
    if not checked.ok:
        return checked
    payload = {"rows": [{"id": str(row["id"]),
                         "expected": [{"k": k, "v": v}
                                      for k, v in sorted((row["expected"] or {}).items())]}
                        for row in rows]}
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    with tempfile.TemporaryDirectory(prefix="mup_cfd_verify_") as tmp:
        script = Path(tmp) / "cfd_blob_readback.ps1"
        out_path = Path(tmp) / "result.txt"
        script.write_text(PS_READBACK_SCRIPT, encoding="utf-8-sig")
        env = os.environ.copy()
        env.update({"MUP_READBACK_B64": b64,
                    "MUP_READBACK_DB": str(checked.data["db"]),
                    "MUP_READBACK_SERVER": os.environ.get("MUP_LOCALDB_SERVER",
                                                          r"(localdb)\Meteodyn"),
                    "MUP_READBACK_OUT": str(out_path)})
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                   "-File", str(script)], capture_output=True, timeout=timeout,
                                   env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return mw.ImportResult(False, mw.ImportCode.COMPUTE,
                                   "库内 blob 反解宿主无法启动: " + str(exc))
        kv = mw._parse_kv(out_path)
        stderr_tail = proc.stderr.decode("utf-8", "replace")[-500:]
    if kv.get("STATUS") != "OK":
        return mw.ImportResult(False, mw.ImportCode.COMPUTE,
                               kv.get("ERROR") or "库内 blob 反解宿主没有返回 STATUS=OK",
                               data={"kv": kv, "stderr": stderr_tail})
    return _parse_readback_kv(kv, rows)


def build_blob(pairs_payload: dict, work_dir: Path, timeout: int = 120) -> mw.ImportResult:
    """PS/.NET 宿主生成 CFD.Parameters_PersistedParameters（与 mup_turbine_import 同范式）。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = work_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs_json = work_dir / "pairs.json"
    pairs_json.write_text(json.dumps(pairs_payload, ensure_ascii=False), encoding="utf-8")
    ps1 = work_dir / "cfd_blob_build.ps1"
    ps1.write_text(PS_BLOB_SCRIPT, encoding="utf-8-sig")
    env = os.environ.copy()
    env.update({"PAIRS_JSON": str(pairs_json), "OUT_DIR": str(out_dir)})
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(ps1)], capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return mw.ImportResult(False, mw.ImportCode.COMPUTE, "blob 宿主执行失败: " + str(exc))
    kv = mw._parse_kv(out_dir / "result.txt")
    if kv.get("STATUS") != "OK":
        return mw.ImportResult(False, mw.ImportCode.COMPUTE,
                               kv.get("ERROR") or ("blob 宿主未返回 STATUS=OK (rc=%s)" % proc.returncode),
                               data={"kv": kv,
                                     "stderr": proc.stderr.decode("utf-8", "replace")[-800:]})
    if str(kv.get("ROUNDTRIP_MISMATCH", "0")) not in ("0", ""):
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "blob 反解自检有 %s 个键值与期望不符，拒绝继续" % kv.get("ROUNDTRIP_MISMATCH"),
                               data=dict(kv))
    for name in ("RT_PART1_TYPE",):
        if "System.Collections.Generic.List" not in str(kv.get(name, "")):
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "blob 反解类型不是 List（%s=%s），MUP 端会读不出参数"
                                   % (name, kv.get(name)), data=dict(kv))
    # 条数也要对得上：第二段流被截断时类型仍是 List，但键会少
    for part, written, read_back in (("part1", "PART1_COUNT", "RT_PART1_COUNT"),
                                     ("part2", "PART2_COUNT", "RT_PART2_COUNT")):
        if str(kv.get(written, "")) != str(kv.get(read_back, "")):
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "blob %s 自读条数 %s 与写入条数 %s 不一致"
                                   % (part, kv.get(read_back), kv.get(written)),
                                   data=dict(kv))
    blob_path = out_dir / "cfd_params.bin"
    if not blob_path.is_file():
        return mw.ImportResult(False, mw.ImportCode.COMPUTE, "blob 产物缺失")
    blob = blob_path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest().upper()
    if kv.get("BLOB_SHA") != digest:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "blob SHA 不一致（产物被改动？）",
                               data={"expected": kv.get("BLOB_SHA"), "actual": digest})
    if int(kv.get("BLOB_LEN") or -1) != len(blob):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "blob 长度不一致", data=dict(kv))
    return mw.ImportResult(True, mw.ImportCode.OK, "参数快照 blob 已生成",
                           data={"blob": blob, "sha": digest, "kv": kv})


# --- 独占工作目录（P1-B）----------------------------------------------------
def _fresh_work_dir(out_dir: Optional[Path] = None) -> Path:
    """本次调用的 blob 构建工作目录：<out_dir>/mup_cfd_import/<pid>-<uuid>。

    为什么必须独占：pairs.json、template_<id>.bin、C# 宿主产物全是固定文件名。共用
    %TEMP%\\mup_cfd_import 时，两个并发 apply 会互相覆盖 —— 实测 Q 请求的 (33,4) 被
    P 请求的 (11,2) 覆盖后照样 rc=0 报「校验通过」，8 线程写 8 个不同请求只塌成
    2 个 blob SHA。--out-dir 以前只影响最终产物、不参与 blob 构建，所以补上这一层。
    """
    base = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    return base / "mup_cfd_import" / ("%d-%s" % (os.getpid(), uuid.uuid4().hex[:12]))


def _remove_work_dir(work: Path) -> str:
    """删除独占工作目录；成功返回空串，失败返回原因（保留现场，不假装删掉了）。"""
    try:
        shutil.rmtree(work)
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, exc)
    return ""


# --- 计划 ------------------------------------------------------------------
def build_plan(spec: CfdSpec, db: str,
               out_dir: Optional[Path] = None) -> mw.ImportResult:
    """生成 CFD 写入计划。blob 构建用独占临时工作目录，成功后删除，失败时保留现场。"""
    work = _fresh_work_dir(out_dir)
    result = _build_plan_at(spec, db, work)
    # work_dir 一律回报：run() 的产物（plan.json/apply.sql/rollback.sql/cfd_params.bin）
    # 必须落进同一个独占目录，否则不带 --out-dir 的并发 apply 会互相覆盖证据文件（P1-B 同族）。
    result.data["work_dir"] = str(work)
    if result.ok:
        reason = _remove_work_dir(work)
        result.data["work_dir_cleanup"] = {"removed": not reason, "reason": reason}
    else:
        result.data["work_dir_cleanup"] = {"removed": False, "reason": "计划失败，保留现场供排查"}
    return result


def _build_plan_at(spec: CfdSpec, db: str, work: Path) -> mw.ImportResult:  # noqa: C901 - 计划装配为单一线性流程
    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)
    try:
        site, res = _site_and_case(db, spec.site, spec.case)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "无法读取目标库: " + str(exc))
    if not site:
        return res
    lit = mw._sql_literal
    notes: list[str] = []
    try:
        env_rows = mw._query_rows(
            db, "SELECT e.Discriminator FROM dbo.Case_EnvironmentDatas ce "
                "JOIN dbo.EnvironmentData e ON e.Id=ce.EnvironmentDataId "
                "WHERE ce.CaseId=" + lit(site["case_id"]) + ";")
        map_rows = mw._query_rows(
            db, "SELECT COUNT(*) FROM dbo.Elements WHERE Parent_Id=" + lit(site["id"])
                + " AND Discriminator='MTDElementMapping';")
        elem_rows = mw._query_rows(
            db, "SELECT COUNT(*) FROM dbo.Elements WHERE Parent_Id=" + lit(site["id"])
                + " AND Discriminator IN ('MTDElementMast','MTDElementWindTurbine');")
        existing = mw._query_rows(
            db, "SELECT COUNT(*) FROM dbo.ComputationObject co "
                "JOIN dbo.CFD c ON c.Id=co.CFD_Id WHERE co.Parent_Id=" + lit(site["case_id"]) + ";")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "读取前置数据失败: " + str(exc))

    discs = {r[0] for r in env_rows if r}
    if spec.require_environment and "MTDSiteOrographyData" not in discs:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION,
                               "该 Case 未挂地形（Case_EnvironmentDatas 无 MTDSiteOrographyData）；"
                               "CFD 需要地形，先用 mup_terrain_import.py 落地形（或 --allow-missing-environment）")
    if spec.require_mapping and int((map_rows[0] or ["0"])[0] or 0) == 0:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION,
                               "站点没有绘图 Mapping 元素（金样 CFD 的 .surf 文件名里带 Mapping 元素 Id）；"
                               "先用 mup_mapping_import.py（或 --allow-no-mapping）")
    if spec.require_element and int((elem_rows[0] or ["0"])[0] or 0) == 0:
        return mw.ImportResult(False, mw.ImportCode.PRECONDITION,
                               "站点没有测风塔/风机元素，无气象参考点可用（或 --allow-no-element）")
    if int((existing[0] or ["0"])[0] or 0):
        notes.append("该 Case 已有 %s 个计算对象，本计划在其后追加" % existing[0][0])
    if "MTDSiteRoughnessDataWithDatabase" not in discs:
        notes.append("该 Case 未挂粗糙度数据（金样有），CFD 地面粗糙度将走默认")

    got_defaults = _default_parameters(db)
    if not got_defaults.ok:
        return got_defaults
    defaults: dict[str, str] = got_defaults.data["params"]
    if not defaults:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               "dbo.Parameters 里没有 DAS_/NSParameters_ 默认值，无法构造参数快照")
    work.mkdir(parents=True, exist_ok=True)
    candidates = (_climatology_candidates(db, site["id"], work)
                  if spec.directions is None else [])
    if spec.climatology:
        candidates = [c for c in candidates if c["climatology_name"] == spec.climatology]
    directions = spec.directions
    stability = spec.stability
    if directions is None:
        usable = [c for c in candidates if c["directions"]]
        if not usable:
            return mw.ImportResult(
                False, mw.ImportCode.PRECONDITION,
                "未能从站点测风塔关联的气象对象解出风向扇区（风频矩阵为空或缺失）；"
                "请显式 --directions 22,45,...（或 --climatology NAME）")
        chosen = usable[0]
        if len({c["climatology_name"] for c in usable}) > 1:
            notes.append("多个气象对象可用，取 %s-%sm" % (
                chosen["climatology_name"], int(chosen["meter"])))
        else:
            notes.append("风向候选来自气象参考点 %s-%sm 的风频矩阵（%d 个非零扇区）"
                         % (chosen["climatology_name"], int(chosen["meter"]), len(chosen["directions"])))
        directions = chosen["directions"]
    if stability is None:
        # 热稳定度默认取表单默认值（dbo.Parameters 的 DAS_Stability，本机=2「2 - 中性」），
        # 与 UI 锚点 DASParameters_ComboBoxWithWarning_Stability 实测一致；
        # ClimatologyStabilities.Stability 是气象数据的分桶号（本机只有 -1=未分类），
        # 不是这里的候选，金样 DEMO 也用 2 而非 -1。
        stability = int(defaults.get("DAS_Stability", "2"))
        notes.append("热稳定度取表单默认 DAS_Stability=%d（UI 下拉显示 “2 - 中性”）" % stability)
    buckets = sorted({c["stability"] for c in candidates})
    if buckets and int(stability) not in buckets:
        notes.append("站点气象数据自身的稳定度分桶是 %s（-1=未按稳定度分类），"
                     "与 CFD.Stability 的类别号不是同一套编号；写入按 --stability/表单默认值"
                     % buckets)

    planned_dirs = sorted(set(int(d) for d in directions))
    try:
        taken = _existing_direction_stability(db, site["case_id"])
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               "读取该工况已有 CFD 的 (Direction, Stability) 失败: " + str(exc))
    clash = [(d, int(stability)) for d in planned_dirs if int(stability) in taken.get(d, set())]
    if clash:
        # 库里没有 (Direction,Stability) 唯一约束，本接口又只追加不覆盖 ⇒ 重复只能在计划期拒。
        pairs = ", ".join("direction %d × stability %d" % (d, s) for d, s in clash)
        return mw.ImportResult(
            False, mw.ImportCode.CONFLICT,
            "该 Case 已有同 (Direction, Stability) 的计算对象：%s。重复执行会堆出两份同参数 "
            "CFD/ComputationObject；本接口只追加、不删既有行：请换 --directions/--stability/--case，"
            "或先按 rollback.sql/GUI 清掉既有对象" % pairs,
            data={"clash": [[d, s] for d, s in clash]})

    # 模板先行：本库已有 MUP 自产的 CFD 快照时，以那份快照为基（保留 MUP 的键序与 List
    # 后备数组容量）。金样实测只有 7 个键的值真的带 '|'：NSParameters_{kl,keps}_{cmu,
    # coefcd,coefcdk} + kl_nutmin + keps_cmu；而 DAS_ExpansionCoefficient_Vertical 与
    # NSParameters_nbgrid 在快照里是标量 1.2 / 0（旧注释拿它们举例是错的，P3-I）。
    template_id, _ = _pick_template(db, spec.template_cfd_id)
    if spec.template_cfd_id and not template_id:
        return mw.ImportResult(
            False, mw.ImportCode.INPUT,
            "--template-cfd %s 在本库没有带参数快照的 CFD 行；不静默换别的模板，"
            "也不静默改用 built-fresh" % spec.template_cfd_id)
    template_path = ""
    if template_id:
        template_file = work / ("template_%s.bin" % template_id)
        got = _download_blob(db, template_id, template_file)
        if not got.ok:
            return mw.ImportResult(
                False, got.code,
                "模板 blob 取回失败 ⇒ 拒绝退化成 built-fresh（少键/不同键序的快照会静默改变"
                "计算设置，见 P1-B/P3-H）: " + got.message)
        template_path = str(template_file)
        notes.append("参数快照模板取自 CFD 行 %s（%s 字节，MUP 自产 blob）"
                     % (template_id, got.data["size"]))
    else:
        notes.append("库内没有可参考的 CFD blob 模板 ⇒ 按金样键序自建：键集 = 金样 40+77，"
                     "键值取 dbo.Parameters 表单默认值 + 金样常量表，不是本站点/MUP 实产快照")

    overrides = dict(spec.overrides or {})
    overrides["DAS_Stability"] = _fmt(int(stability))
    das_over = [{"k": k, "v": _fmt(v)} for k, v in sorted(overrides.items())
                if k.startswith("DAS_")]
    ns_over = [{"k": k, "v": _fmt(v)} for k, v in sorted(overrides.items())
               if k.startswith("NSParameters_")]
    if template_path:
        das_base, ns_base = [], []
        known = set(GOLDEN_DAS_KEYS) | set(GOLDEN_NS_KEYS)
        for item in das_over + ns_over:
            if item["k"] not in known:
                notes.append("参数 %s 不在金样快照的键集合里（新增到快照，MUP 未必读它）" % item["k"])
    else:
        # built-fresh 的键集合必须与金样同构：dbo.Parameters 少的那 5 个键由
        # GOLDEN_SNAPSHOT_CONSTANTS 补（P3-H）。还缺就拒，不写短快照。
        base_values = dict(defaults)
        for key in GOLDEN_SNAPSHOT_CONSTANTS:
            base_values.setdefault(key, GOLDEN_SNAPSHOT_CONSTANTS[key])
        gap = [key for key in list(GOLDEN_DAS_KEYS) + list(GOLDEN_NS_KEYS)
               if key not in base_values]
        if gap:
            return mw.ImportResult(
                False, mw.ImportCode.VERIFY,
                "没有模板可用，且这些金样快照键在 dbo.Parameters 里没有默认值、也不在金样常量表里："
                "自建快照会与金样键集合不同构（拒绝写短快照，请 --set 补齐或提供 --template-cfd）: %s"
                % ", ".join(gap))
        das_base = [{"k": key, "v": base_values[key]} for key in GOLDEN_DAS_KEYS]
        ns_base = [{"k": key, "v": base_values[key]} for key in GOLDEN_NS_KEYS]
        for item in das_over + ns_over:
            if item["k"] not in {b["k"] for b in das_base + ns_base}:
                notes.append("参数 %s 不在 dbo.Parameters 默认集合里（新增到快照）" % item["k"])

    built = build_blob({"template": template_path, "das_base": das_base, "ns_base": ns_base,
                        "das_overrides": das_over, "ns_overrides": ns_over,
                        "watch_keys": list(SITE_SPECIFIC_KEYS)},
                       work)
    if not built.ok:
        return built
    blob: bytes = built.data["blob"]
    kv = built.data["kv"]
    source = kv.get("SOURCE") or "unknown"
    # 计划请求写进快照的键值（base + overrides，overrides 覆盖同名 base 项）。
    # 这是与 blob SHA 无关的事实，_verify 用它比对库内反解结果（P1-B 的自检补位）。
    expected_pairs = {item["k"]: item["v"] for item in das_base + ns_base + das_over + ns_over}
    if source == "template" and str(kv.get("SAME_AS_TEMPLATE")).lower() == "true":
        notes.append("未改任何参数值：生成 blob 与模板（MUP 自产）逐字节相同")
    if source == "template" and int(str(kv.get("TPL_KEY_COUNT") or 0)) > 0:
        notes.append("快照以模板为基（%s 个键），只覆盖显式给定的参数键"
                     % kv.get("TPL_KEY_COUNT"))
    carried = [chunk.strip() for chunk in str(kv.get("CARRIED_KEYS", "")).split("|")
               if chunk.strip()]
    if carried:
        if source == "template":
            notes.append("这些站点/机器相关的键沿用模板快照的值（派生规则未知，必要时 --set 覆盖）: "
                         + ", ".join(carried))
        else:
            notes.append("没有模板，这些站点/机器相关的键取的是 dbo.Parameters 默认值/金样常量，"
                         "不是本站点的值（派生规则未知，必要时 --set 覆盖）: " + ", ".join(carried))
    orphans = [k for k in str(kv.get("TEMPLATE_ONLY_KEYS", "")).split(",") if k]
    if orphans:
        notes.append("这些键既不在 dbo.Parameters 默认值里也不在模板快照里（异常，请核对）: "
                     + ", ".join(orphans))
    if source == "built-fresh":
        notes.append("自建 blob 与 MUP 原生产物不是逐字节相同（List 后备数组容量不同），"
                     "但反序列化后键集合与金样同为 %d+%d；值只保证「等于表单默认值/金样常量」，"
                     "不保证等于 MUP 实产快照" % (len(GOLDEN_DAS_KEYS), len(GOLDEN_NS_KEYS)))

    rows = []
    for direction in planned_dirs:
        rows.append({"direction": direction, "stability": int(stability),
                     "cfd_id": str(uuid.uuid4()), "computation_id": str(uuid.uuid4())})
    atlas_rows: list[dict] = []
    for row in rows:
        if spec.atlas:
            # 每个风向配一个 Atlas（金样缺失，粒度未知；1:1 是最少假设的写法）
            atlas_rows.append({"atlas_cfd_id": str(uuid.uuid4()),
                               "atlas_computation_id": str(uuid.uuid4()),
                               "direction": row["direction"], "stability": row["stability"],
                               "computation_id": row["computation_id"]})
    if atlas_rows:
        notes.append("Atlas 行为未验证写入：Direction/Stability 沿用配对风向，参数快照沿用同一 blob")

    plan = CfdPlan(spec=spec, site_id=site["id"], case_id=site["case_id"], rows=rows,
                   atlas_rows=atlas_rows, blob=blob, blob_sha=built.data["sha"],
                   template_source=source, template_cfd_id=template_id, stability=int(stability),
                   expected_pairs=expected_pairs,
                   notes=notes)
    plan.sql = _insert_sql(plan)
    plan.rollback_sql = _rollback_sql(plan)
    return mw.ImportResult(True, mw.ImportCode.OK, "CFD 计算写入计划已生成",
                           data={"plan": plan.to_dict(), "plan_obj": plan, "blob_kv": kv})


def _atlas_sql(plan: CfdPlan, atlas: dict, hex_blob: str) -> list[str]:
    """Atlas = CFD 的 TPT 子类：CFD 父行 + CFDAtlas 子表（同 Id）+ DASAtlasObject 计算对象。

    CFDAtlas.Id 有 FK 指向 CFD.Id，所以三行必须一起写、Id 必须一致。
    """
    lit = mw._sql_literal
    return [
        "INSERT INTO dbo.CFD (Id,Discriminator,Direction,Stability,"
        "Parameters_PersistedParameters) VALUES ("
        + lit(atlas["atlas_cfd_id"]) + "," + lit(DISC_ATLAS_CONTAINER) + ","
        + str(int(atlas["direction"])) + "," + str(int(atlas["stability"])) + ","
        + hex_blob + ");",
        "INSERT INTO dbo.CFDAtlas (Id,NbMicroX,NbMicroY) VALUES ("
        + lit(atlas["atlas_cfd_id"]) + "," + str(int(plan.spec.atlas_nb_micro_x)) + ","
        + str(int(plan.spec.atlas_nb_micro_y)) + ");",
        "INSERT INTO dbo.ComputationObject (Id,Discriminator,Parent_Id,CFD_Id,Micro_AtlasId)"
        " VALUES (" + lit(atlas["atlas_computation_id"]) + "," + lit(DISC_ATLAS_OBJECT) + ","
        + lit(plan.case_id) + "," + lit(atlas["atlas_cfd_id"]) + ",NULL);",
    ]


def _insert_sql(plan: CfdPlan) -> str:
    lit = mw._sql_literal
    hex_blob = mw._sql_hex(plan.blob)
    by_computation = {a["computation_id"]: a for a in plan.atlas_rows}
    stmts: list[str] = []
    for row in plan.rows:
        atlas = by_computation.get(row["computation_id"])
        if atlas:
            stmts.extend(_atlas_sql(plan, atlas, hex_blob))
        stmts.append("INSERT INTO dbo.CFD (Id,Discriminator,Direction,Stability,"
                     "Parameters_PersistedParameters) VALUES ("
                     + lit(row["cfd_id"]) + "," + lit(DISC_CFD_CONTAINER) + ","
                     + str(int(row["direction"])) + "," + str(int(row["stability"])) + ","
                     + hex_blob + ");")
        stmts.append("INSERT INTO dbo.ComputationObject (Id,Discriminator,Parent_Id,CFD_Id,"
                     "Micro_AtlasId) VALUES ("
                     + lit(row["computation_id"]) + "," + lit(DISC_COMPUTATION) + ","
                     + lit(plan.case_id) + "," + lit(row["cfd_id"]) + ","
                     + lit(atlas["atlas_cfd_id"] if atlas else None) + ");")
    return ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
            + chr(10).join(stmts) + chr(10) + "COMMIT;" + chr(10))


def _rollback_sql(plan: CfdPlan) -> str:
    lit = mw._sql_literal
    ids = [lit(row["computation_id"]) for row in plan.rows]
    ids += [lit(a["atlas_computation_id"]) for a in plan.atlas_rows]
    cfd_ids = [lit(row["cfd_id"]) for row in plan.rows]
    cfd_ids += [lit(a["atlas_cfd_id"]) for a in plan.atlas_rows]
    # 删除顺序按 FK：ComputationObject → CFDAtlas → CFD
    stmts = ["DELETE FROM dbo.ComputationObject WHERE Id IN (" + ",".join(ids) + ");"]
    if plan.atlas_rows:
        stmts.append("DELETE FROM dbo.CFDAtlas WHERE Id IN ("
                     + ",".join(lit(a["atlas_cfd_id"]) for a in plan.atlas_rows) + ");")
    stmts.append("DELETE FROM dbo.CFD WHERE Id IN (" + ",".join(cfd_ids) + ");")
    return ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;" + chr(10)
            + chr(10).join(stmts) + chr(10) + "COMMIT;" + chr(10))


def _verify(db: str, plan: CfdPlan) -> mw.ImportResult:
    lit = mw._sql_literal
    cfd_ids = ",".join(lit(row["cfd_id"]) for row in plan.rows)
    try:
        rows = mw._query_rows(
            db,
            "SELECT c.Id, c.Direction, c.Stability, c.Discriminator,"
            " CONVERT(varchar(64), HASHBYTES('SHA2_256', c.Parameters_PersistedParameters), 2),"
            " DATALENGTH(c.Parameters_PersistedParameters),"
            " ISNULL((SELECT co.Id FROM dbo.ComputationObject co WHERE co.CFD_Id=c.Id),''),"
            " ISNULL((SELECT co.Parent_Id FROM dbo.ComputationObject co WHERE co.CFD_Id=c.Id),''),"
            " ISNULL((SELECT co.Micro_AtlasId FROM dbo.ComputationObject co"
            " WHERE co.CFD_Id=c.Id),'')"
            " FROM dbo.CFD c WHERE c.Id IN (" + cfd_ids + ") ORDER BY c.Direction;")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验失败: " + str(exc))
    want = {row["cfd_id"]: row for row in plan.rows}
    atlas_link_of_computation = {a["computation_id"]: a["atlas_cfd_id"] for a in plan.atlas_rows}
    checks = {"row_count": len(rows) == len(plan.rows)}
    seen: set[str] = set()
    for r in rows:
        row = want.get(r[0])
        if not row:
            checks["unexpected_row"] = False
            continue
        seen.add(r[0])
        checks["dir_" + r[0][:8]] = int(r[1]) == row["direction"]
        checks["stab_" + r[0][:8]] = int(r[2]) == row["stability"]
        checks["disc_" + r[0][:8]] = r[3] == DISC_CFD_CONTAINER
        checks["blob_sha_" + r[0][:8]] = (r[4].upper() == plan.blob_sha)
        checks["blob_len_" + r[0][:8]] = int(r[5]) == len(plan.blob)
        checks["comp_" + r[0][:8]] = (r[6] == row["computation_id"] and r[7] == plan.case_id)
        # Micro_AtlasId 是「主计算对象 → Atlas 父行」的关联列：勾 Atlas 时必须等于配对
        # Atlas 的 CFD Id，不勾必须是 NULL（ISNULL 后是空串）。以前这里完全不覆盖，
        # 关联列写错也不会报警（P3-G）。
        want_link = atlas_link_of_computation.get(row["computation_id"], "")
        got_link = (r[8].strip() if len(r) > 8 else "<缺列>")
        checks["atlas_link_" + r[0][:8]] = got_link == want_link
    if len(seen) != len(plan.rows):
        checks["all_rows_present"] = False
    if plan.atlas_rows:
        # Atlas 是三行组：TPT 子表行与 DASAtlasObject 计算对象都要在，且 Id 对得上
        atlas_ids = ",".join(lit(a["atlas_cfd_id"]) for a in plan.atlas_rows)
        want_atlas = {a["atlas_cfd_id"]: a for a in plan.atlas_rows}
        try:
            atlas_rows = mw._query_rows(
                db,
                "SELECT c.Id, c.Discriminator, a.NbMicroX, a.NbMicroY,"
                " ISNULL((SELECT co.Id FROM dbo.ComputationObject co WHERE co.CFD_Id=c.Id"
                " AND co.Discriminator=" + lit(DISC_ATLAS_OBJECT) + "),''),"
                " ISNULL((SELECT co.Micro_AtlasId FROM dbo.ComputationObject co"
                " WHERE co.CFD_Id=c.Id AND co.Discriminator=" + lit(DISC_ATLAS_OBJECT) + "),'')"
                " FROM dbo.CFD c JOIN dbo.CFDAtlas a ON a.Id=c.Id WHERE c.Id IN ("
                + atlas_ids + ");")
        except Exception as exc:
            return mw.ImportResult(False, mw.ImportCode.VERIFY, "Atlas 写后校验失败: " + str(exc))
        checks["atlas_row_count"] = len(atlas_rows) == len(plan.atlas_rows)
        for r in atlas_rows:
            atlas = want_atlas.get(r[0])
            if not atlas:
                checks["unexpected_atlas_row"] = False
                continue
            checks["atlas_disc_" + r[0][:8]] = (r[1] == DISC_ATLAS_CONTAINER)
            checks["atlas_x_" + r[0][:8]] = int(r[2]) == int(plan.spec.atlas_nb_micro_x)
            checks["atlas_y_" + r[0][:8]] = int(r[3]) == int(plan.spec.atlas_nb_micro_y)
            checks["atlas_obj_" + r[0][:8]] = (r[4] == atlas["atlas_computation_id"])
            # Atlas 自己的计算对象不再向下挂 Atlas（DASAtlasObject.Micro_AtlasId = NULL）
            checks["atlas_self_link_" + r[0][:8]] = (len(r) > 5 and r[5].strip() == "")
        if len(atlas_rows) != len(plan.atlas_rows):
            checks["all_atlas_rows_present"] = False
    if not all(checks.values()):
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致", data={"checks": checks})
    # SHA 无关的内容直证（P1-B）：先把关系列比完，再花一次 .NET 宿主把库里 blob 反解回来，
    # 断言「计划请求的表单项在库里就是那个值」。expected_pairs 为空时这里必须显式失败，
    # 不能变成「没有断言所以通过」。
    checks["expected_pairs_declared"] = bool(plan.expected_pairs)
    if not checks["expected_pairs_declared"]:
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验不一致：计划里没有可断言的表单项（expected_pairs 为空）",
                               data={"checks": checks})
    content = _readback_blob_pairs(
        db, [{"id": row["cfd_id"], "expected": plan.expected_pairs} for row in plan.rows])
    if not content.ok:
        content.data["checks"] = {**checks, **(content.data.get("checks") or {})}
        return content
    checks.update(content.data["checks"])
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过",
                           data={"checks": checks, "blob_sha256": plan.blob_sha,
                                 "blob_len": len(plan.blob), "rows": len(plan.rows),
                                 "readback": content.data.get("readback"),
                                 "asserted_form_keys": sorted(plan.expected_pairs)})


def apply_plan(db: str, plan: CfdPlan) -> mw.ImportResult:
    """写库 + 写后校验；校验失败立刻执行 rollback.sql 并把回滚结果写进负载（P2-C）。

    与 mup_synthesis_import 的 S-P2-5 / mup_element_import 的 E-P2-2 同范式。以前这里直接
    `return _verify(db, plan)`：事务内失败确实整批回滚，但「已提交又与计划漂移」的校验失败
    只会留下残留行，rollback.sql 落盘却没人执行（实测 3→4 行，负载里没有 rollback 字段）。
    """
    if mw.is_prod_db(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "拒绝写生产库 " + db + "；请用副本库")
    if not mw.apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "目标库不在允许列表: " + db)
    ok, out, err = mw._run_sql(db, plan.sql, timeout=180)
    if not ok:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, "写库失败: " + (err or out))
    verified = _verify(db, plan)
    if not verified.ok:
        rb_ok, rb_out, rb_err = mw._run_sql(db, plan.rollback_sql, timeout=180)
        data = dict(verified.data or {})
        data["rollback"] = {"executed": True, "ok": bool(rb_ok),
                            "output": (rb_out or "")[-2000:], "error": rb_err}
        # 校验失败原因必须活着出来，否则负载里只剩「失败」两个字（编排侧无法分岔）。
        data["verify_message"] = verified.message
        detail = "（%s）" % verified.message if verified.message else ""
        if rb_ok:
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   "写后校验失败%s，已自动回滚" % detail, data=data)
        return mw.ImportResult(False, mw.ImportCode.VERIFY,
                               "写后校验失败%s且自动回滚失败（需人工介入）" % detail, data=data)
    return verified


def run(spec: CfdSpec, db: str, apply: bool = False,
        out_dir: Optional[Path] = None) -> mw.ImportResult:
    if apply and (mw.is_prod_db(db) or not mw.apply_ok(db)):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, "拒绝写目标库: " + db)
    planned = build_plan(spec, db, out_dir=out_dir)
    if not planned.ok:
        return planned
    plan: CfdPlan = planned.data["plan_obj"]
    # 产物落本次调用的独占工作目录（P1-B）：过去不带 --out-dir 时固定写 %TEMP%\\mup_cfd_import，
    # 并发 apply 之间会互相覆盖 cfd_params.bin / rollback.sql（回滚证据错位）。
    work = Path(planned.data["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    (work / "plan.json").write_text(json.dumps({"spec": {k: v for k, v in spec.__dict__.items()},
                                                "plan": plan.to_dict()},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")
    (work / "cfd_params.bin").write_bytes(plan.blob)
    summary = ("计划 %d 个 CFD 计算对象（风向 %s × 稳定度 %d，blob %d 字节，模板=%s）"
               % (len(plan.rows), ",".join(str(r["direction"]) for r in plan.rows),
                  plan.stability, len(plan.blob), plan.template_source))
    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库）：" + summary,
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "work_dir": str(work), "blob": str(work / "cfd_params.bin"),
                                     "rollback": str(work / "rollback.sql")})
    applied = apply_plan(db, plan)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message,
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "work_dir": str(work),
                                     "blob": str(work / "cfd_params.bin"),
                                     "rollback": str(work / "rollback.sql"),
                                     "verify": applied.data})
    return mw.ImportResult(True, mw.ImportCode.OK, "CFD 计算对象写入并校验通过：" + summary,
                           data={"plan": plan.to_dict(), "verify": applied.data,
                                 "out_dir": str(work), "work_dir": str(work),
                                 "blob": str(work / "cfd_params.bin"),
                                 "rollback": str(work / "rollback.sql")})


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="数据层新建 MUP CFD 计算对象（只建对象，不触发计算）")
    p.add_argument("--site", required=True, help="站点名称或 Id")
    p.add_argument("--case", default=None,
                   help="工况名或 Cases.Id。站点有多个工况时必须给：不给且有多于一个工况时"
                        "拒绝执行，不再静默取「名字最前」的那个（P2-E）")
    p.add_argument("--directions", default="",
                   help="风向列表，逗号分隔；留空=按气象风频矩阵自动取全部非零扇区")
    p.add_argument("--climatology", default="", help="指定气象对象名（自动解析风向时用）")
    p.add_argument("--stability", type=int, default=None,
                   help="热稳定度类别号（留空=表单默认 DAS_Stability，本机 2=“2 - 中性”；"
                        "-1=不分桶。这是类别号，不是 ClimatologyStabilities 的分桶号）")
    p.add_argument("--convergence-threshold", dest="convergence_threshold", default=None,
                   help="收敛阈值（UI 锚点 DASParameters_NumericUpDownWithWarning_"
                        "ConvergenceThreshold；默认表 DAS_ConvergenceThreshold=95，"
                        "应用下限 MTDApplication_MinimumCFDConvergenceThresholdInPercent=90）")
    p.add_argument("--min-h-res", dest="min_h_res", default=None, help="最小水平分辨率")
    p.add_argument("--min-v-res", dest="min_v_res", default=None, help="最小垂直分辨率")
    p.add_argument("--exp-coef-h", dest="exp_coef_h", default=None, help="水平扩展系数")
    p.add_argument("--iterations", default=None,
                   help="最大迭代次数（正整数，非整数在 validate 即拒，不写进参数快照）")
    p.add_argument("--threads", default=None,
                   help="每方向核数（正整数，非整数在 validate 即拒，不写进参数快照）")
    p.add_argument("--convergence-monitoring", dest="convergence_monitoring",
                   default=None, choices=["true", "false"], help="收敛跟踪")
    p.add_argument("--auto-cvg", dest="auto_cvg", default=None, choices=["true", "false"],
                   help="自动收敛")
    p.add_argument("--force-all-iteration", dest="force_all_iteration", default=None,
                   choices=["true", "false"], help="强制全部迭代")
    p.add_argument("--meso-micro-coupling", dest="meso_micro_coupling", default=None,
                   help="中微尺度耦合模式（金样默认 None=不勾）")
    p.add_argument("--turbulence-model", dest="turbulence_model", default=None,
                   help="湍流模型（KL / KEPs）")
    p.add_argument("--set", dest="sets", action="append", default=[],
                   metavar="KEY=VALUE", help="直接覆盖快照键值，可重复；"
                                             "如 --set NSParameters_nfirst=10")
    p.add_argument("--atlas", action="store_true", help="勾选 Atlas（建 CFDAtlas 行）")
    p.add_argument("--allow-unverified-atlas", dest="allow_unverified_atlas", action="store_true",
                   help="明确接受 Atlas 三行组的未验证写入（本库无 Atlas 金样可对照）")
    p.add_argument("--atlas-nb-micro-x", type=int, default=0)
    p.add_argument("--atlas-nb-micro-y", type=int, default=0)
    p.add_argument("--template-cfd", default="", help="指定作为参数快照模板的 CFD 行 Id")
    p.add_argument("--job-priority", default=None,
                   help="仅记录：计算作业优先级在数据层未落表，接口拒绝静默丢弃")
    p.add_argument("--allow-no-mapping", action="store_true")
    p.add_argument("--allow-missing-environment", action="store_true")
    p.add_argument("--allow-no-element", action="store_true")
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true")
    return p


def _form_value(key: str, value: object) -> str:
    """表单值 → 快照字符串：布尔转 True/False，数字去掉多余小数，其余原样。"""
    if isinstance(value, bool):
        return _fmt(value)
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return _fmt(text.lower() == "true")
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer() and "." not in text and "e" not in text.lower():
        return str(int(number))
    return repr(number)


def _collect_overrides(args: argparse.Namespace) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for name, key in FORM_KEY_MAP.items():
        value = getattr(args, name, None)
        if value is None:
            continue
        overrides[key] = _form_value(key, value)
    for item in args.sets or []:
        head, sep, tail = str(item).partition("=")
        if not sep:
            raise ValueError("--set 需要 KEY=VALUE 形式: " + str(item))
        overrides[head.strip()] = tail.strip()
    return overrides


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.job_priority:
        print("[FAIL] 计算作业优先级在本机数据层没有任何落表位置"
              "（dbo.Parameters 无 Priorit*/Job* 键，CFD blob 亦无），已拒绝静默丢弃；"
              "如需自动化请走 HPC/Cluster 侧接口")
        return 2
    try:
        overrides = _collect_overrides(args)
    except ValueError as exc:
        print("[FAIL] " + str(exc))
        return 2
    directions = None
    if str(args.directions or "").strip():
        directions = [int(chunk) for chunk in str(args.directions).replace("，", ",").split(",")
                      if chunk.strip()]
    spec = CfdSpec(
        site=args.site, directions=directions, stability=args.stability,
        case=(str(args.case).strip() or None) if args.case else None,
        climatology=args.climatology, overrides=overrides, atlas=bool(args.atlas),
        allow_unverified_atlas=bool(args.allow_unverified_atlas),
        atlas_nb_micro_x=args.atlas_nb_micro_x, atlas_nb_micro_y=args.atlas_nb_micro_y,
        template_cfd_id=args.template_cfd, require_mapping=not args.allow_no_mapping,
        require_environment=not args.allow_missing_environment,
        require_element=not args.allow_no_element)
    result = run(spec, args.db, apply=args.apply,
                 out_dir=Path(args.out_dir) if args.out_dir else None)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        for issue in result.issues:
            print("  " + issue.code + ": " + issue.message)
        for note in (result.data.get("plan") or {}).get("notes", []):
            print("  note: " + note)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
