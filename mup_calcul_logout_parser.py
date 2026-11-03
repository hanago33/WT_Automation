#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SOURCE: copied verbatim from MUP_recon (reverse-engineering repo)
  E:\\My_RF_Project\\MUP_recon\\07_recon2_mup_entries\\_tools\\mup_calcul_logout_parser.py
  Reverse-engineering evidence (read-only): MTDCFDServices.dll / MTDXmlLogoutServices.
This is a PURE-STDLIB copy so WT_Automation does NOT load/import from MUP_recon.
Do not modify this copy's decode logic independently; keep source of truth in MUP_recon.

mup_calcul_logout_parser.py
===========================
Pure-stdlib parser for MeteorDyn Universe Pro (MUP / MeteodynUniverse) CFD
log-out files:  CALCUL_log_out_<cfdId>.xml

This module ONLY decodes text/XML and NEVER loads, executes, or connects to
anything. It does not touch licence/hasp files (LICENSE-BOUNDED).

READ-ONLY static evidence source
--------------------------------
Field names & semantics were reverse-engineered (read-only) from the CLR
metadata + CIL of:
    H:\\Program Files\\Meteodyn\\MeteodynUniverse\\MTDCFDServices.dll
    type  MTD.Computation.Services.MTDXmlLogoutServices
    MVID  8776e5cc-e387-495c-bbf8-6b29623eb10e  (as reported upstream)

Evidence per field (Observed / Derived):
  * 7 column names are OBSERVED as BOTH:
      - #US heap ldstr literals in the CIL of
        `UpdateOutputsAndReturnProgressionLogsInfo`  (see below)
      - column names read back by `ParseLogout` via
        `DataTable.Columns[i].ColumnName` + `DataRow.Item[column]`.
  * `percent` is OBSERVED as a #US literal (`ldstr`) and DERIVED as the
    progression percentage via `GetNewProgression`
    (Dictionary.ContainsKey + Int32.TryParse on a KeyValuePair value).

How MTDXmlLogoutServices reads the file (Derived from CIL, not original source):
  ParseLogout(MTDFileHandle) -> Dictionary<string,string>:
    if !fileExists: return null
    new DataSet() -> .ReadXml(stream) -> close
    for each DataTable in ds.Tables:            # layer 1
      for each DataRow in table.Rows:           # layer 2
        for each DataColumn:                     # layer 3
          dict[column.ColumnName] = row[column].Trim()
    return dict

  Because the file is consumed via ADO.NET `DataSet.ReadXml`, the exact XML
  nesting/namespace is NOT recoverable from CIL alone (DataSet auto-infers
  schema). This parser therefore accepts multiple nested shapes and is
  tolerant of missing/unknown fields (records `raw`), which is correct while
  the schema hierarchy remains UNKNOWN.

Field -> output mapping (Derived from UpdateOutputsAndReturnProgressionLogsInfo):
  mesh             -> set_Mesh(outputs)
  convergence      -> set_Convergence(outputs)
  message          -> set_ErrorCode
  convergence_step -> set_AutoConvergenceStep
  status           -> Enum.Parse(enum, dict["status"])
  start_time       -> dict["start_time"].Split(' ')[0]  (date part kept)
  time_passed      -> passed straight through
  percent          -> Int32.TryParse -> progression percentage / ProgressReport
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# Evidence notes (Observed / Derived) - keep in sync with the report
# ---------------------------------------------------------------------------
# OBSERVED field names (column names + ldstr literals in MTDXmlLogoutServices):
OBSERVED_FIELD_NAMES = (
    "mesh",
    "convergence",
    "message",
    "convergence_step",
    "status",
    "start_time",
    "time_passed",
)
# OBSERVED extra literal: progression percentage
OBSERVED_PERCENT_NAME = "percent"

# ---------------------------------------------------------------------------
# Data model (matches what mup_completion.py is expected to consume)
# ---------------------------------------------------------------------------


@dataclass
class ProgressReport:
    percent: Optional[int] = None          # Derived: GetNewProgression TryParse(percent)
    convergence: Optional[str] = None      # Observed column
    convergence_step: Optional[str] = None # Observed column
    start_time: Optional[str] = None       # Observed column
    time_passed: Optional[str] = None      # Observed column
    mesh: Optional[str] = None             # Observed column
    raw: Dict[str, Any] = field(default_factory=dict)  # any unknown/excess rows


@dataclass
class CompletionState:
    status: Optional[str] = None    # Observed column / Enum.Parse source string
    message: Optional[str] = None   # Observed column
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _norm(value: Any) -> Optional[str]:
    """Coerce a scalar element/text/attr to a trimmed string (or None)."""
    if value is None:
        return None
    if isinstance(value, ET.Element):
        value = value.text
    if isinstance(value, str):
        value = value.strip()
        return value if value != "" else None
    if isinstance(value, (int, float)):
        s = str(value).strip()
        return s if s != "" else None
    return str(value) if value else None



def _lname(tag: Any) -> str:
    """Return the local (namespace-stripped) name of an ElementTree tag."""
    return ET.QName(tag).text

def _int(v: Any) -> Optional[int]:
    """Best-effort integer coercion; tolerant of bad input."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def extract_fields(root: ET.Element, *names: str) -> Dict[str, Optional[str]]:
    """
    Tolerant multi-shape extraction of the named scalar fields from an XML
    tree whose exact hierarchy is UNKNOWN.

    Covers these shapes (all are plausible for a DataSet.ReadXml source and
    for hand-written MUP variants; parser is deliberately lenient):

      1. top-level child elements:        <root><mesh>..</mesh>...</root>
      2. single nested row container:     <root><row>...</row></root>
      3. columns emitted as <row><col1>v</col1><col2>v</col2>...</row>
      4. attributes on root/row:          <root mesh="..." .../>
      5. dotted / snake concatenations are NOT forced - we only read exact names.

    Because nesting/namespace is UNKNOWN, any field we cannot locate is simply
    left None and recorded in `raw`.
    """
    out: Dict[str, Optional[str]] = {n: None for n in names}

    # -- direct child elements / deeper-but-scalar elements ------------------
    # Map name -> best scalar text anywhere in the tree (first hit wins).
    def first_scalar(name: str) -> Optional[str]:
        # 1) direct children
        for child in root:
            if _lname(child.tag) == name:
                v = _norm(child)
                if v is not None:
                    return v
        # 2) any descendant element, exact local name
        for elem in root.iter():
            if _lname(elem.tag) == name:
                if len(elem) == 0:  # leaf
                    v = _norm(elem)
                    if v is not None:
                        return v
        # 3) attributes anywhere (root or descendants)
        for elem in root.iter():
            for k, val in elem.attrib.items():
                if _lname(k) == name:
                    v = _norm(val)
                    if v is not None:
                        return v
        return None

    for name in names:
        out[name] = first_scalar(name)
    return out


# ---------------------------------------------------------------------------
# Main parse API
# ---------------------------------------------------------------------------


def parse_calcul_logout(xml_text: str) -> Tuple[ProgressReport, CompletionState]:
    """
    Parse a CALCUL_log_out_<cfdId>.xml payload (string) into a
    ProgressReport + CompletionState, tolerantly.

    Pure stdlib (xml.etree). Never touches the network / filesystem / HASP.
    """
    progress = ProgressReport()
    completion = CompletionState()

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        # Partition the XML is unknown -> treat the whole doc as raw.
        raw = {"raw_document": xml_text[:4096], "parse_error": str(exc)}
        progress.raw = raw
        completion.raw = raw
        return progress, completion

    fields = extract_fields(
        root,
        "mesh",
        "convergence",
        "message",
        "convergence_step",
        "status",
        "start_time",
        "time_passed",
        "percent",
    )

    # Map observed columns into the progress / completion model -----------------
    progress.mesh = fields.get("mesh")
    progress.convergence = fields.get("convergence")
    progress.convergence_step = fields.get("convergence_step")
    progress.start_time = fields.get("start_time")
    progress.time_passed = fields.get("time_passed")
    progress.percent = _int(fields.get("percent"))

    completion.status = fields.get("status")
    completion.message = fields.get("message")

    # Record any unknown column names present in the data (schema not settled).
    seen = _collect_leaf_scalars(root)
    known = set(OBSERVED_FIELD_NAMES) | {OBSERVED_PERCENT_NAME}
    unknown = {k: v for k, v in seen.items() if k not in known and v is not None}
    if unknown:
        progress.raw["unknown_fields"] = unknown
        completion.raw["unknown_fields"] = unknown

    return progress, completion


def _collect_leaf_scalars(root: ET.Element) -> Dict[str, Any]:
    """Collect all scalar (leaf) values keyed by local element/attr name."""
    result: Dict[str, Any] = {}
    for elem in root.iter():
        ln = _lname(elem.tag)
        if len(elem) == 0 and elem.text and elem.text.strip():
            result.setdefault(ln, elem.text.strip())
    for elem in root.iter():
        for k, v in elem.attrib.items():
            result.setdefault(_lname(k), v)
    return result


def parse_calcul_logout_file(path: str) -> Tuple[ProgressReport, CompletionState]:
    """Read a log-out XML file and parse it. Pure stdlib read of a text file only."""
    if not path.lower().endswith(".xml"):
        # Be defensive: only ever decode XML text payloads.
        raise ValueError("refusing to treat a non-XML file as a log-out payload")
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return parse_calcul_logout(fh.read())


# ---------------------------------------------------------------------------
# Convenience: JSON round-trip for mup_completion.py
# ---------------------------------------------------------------------------


def progress_to_dict(progress: ProgressReport) -> Dict[str, Any]:
    return asdict(progress)


# ---------------------------------------------------------------------------
# Self-test with SYNTHETIC samples (NOT real MUP output)
# ---------------------------------------------------------------------------


def _selftest() -> int:
    failures = 0

    # --- sample 1: element-nested row shape ---------------------------------
    # FAT-NOTE: synthetic sample invented only to exercise the parser logic.
    xml1 = """<?xml version="1.0" encoding="utf-8"?>
<calc_log_out>
  <calculation>
    <mesh>steady-Mesh-238k</mesh>
    <convergence>0.0134</convergence>
    <message></message>
    <convergence_step>1215</convergence_step>
    <status>completed</status>
    <start_time>2026-10-31 09:14:22</start_time>
    <time_passed>01:23:45.678</time_passed>
    <percent>100</percent>
  </calculation>
</calc_log_out>
"""
    p, c = parse_calcul_logout(xml1)
    assert p.mesh == "steady-Mesh-238k"
    assert p.convergence == "0.0134"
    assert p.convergence_step == "1215"
    assert p.start_time == "2026-10-31 09:14:22"
    assert p.time_passed == "01:23:45.678"
    assert p.percent == 100
    assert c.status == "completed"
    assert c.message is None  # empty element -> treated as absent
    print("[ok] sample1 element-nested shape parsed:", p.percent, c.status)

    # --- sample 2: columns-as-attributes shape + missing fields --------------
    # FAT-NOTE: synthetic sample invented only to exercise the parser logic.
    xml2 = """<ROOT>
      <row mesh="tri-mesh-A" convergence="1e-4" start_time="2026-10-31T09:30Z"
           status="running" percent="42" someFutureUnknownKey="yet-here"/>
    </ROOT>
"""
    p2, c2 = parse_calcul_logout(xml2)
    assert p2.mesh == "tri-mesh-A"
    assert p2.percent == 42
    assert c2.status == "running"
    # message / convergence_step / time_passed are simply ABSENT -> must not crash
    assert p2.convergence_step is None
    assert p2.time_passed is None
    assert c2.message is None  # it lives on CompletionState, not ProgressReport
    # unknown column is captured, parser stays alive
    assert "someFutureUnknownKey" in p2.raw.get("unknown_fields", {})
    print("[ok] sample2 attribute shape parsed, missing fields tolerated, unknown captured")

    # --- sample 3: malformed XML -> must not raise ---------------------------
    p3, c3 = parse_calcul_logout("<calc_log_out><mesh>unterminated")
    assert p3.raw.get("parse_error") is not None
    print("[ok] sample3 malformed XML handled without crashing")

    # --- JSON round-trip smoke ---------------------------------------------
    d = progress_to_dict(p2)
    json.dumps(d)  # must serialize
    print("[ok] sample4 JSON serialization of ProgressReport works")

    print("\nSelf-test PASSED (%d samples, synthetic inputs only)." % 4)
    return failures


if __name__ == "__main__":
    _selftest()