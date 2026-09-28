# -*- coding: utf-8 -*-
import sys, json

def norm(v):
    t = str(v or "").strip()
    if not t: return ""
    if t.lower() in {"property does not exist", "[null]", "none", "null"}: return ""
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")): return t[1:-1]
    return t

def split_parts(text):
    parts, buf, i = [], [], 0
    text = str(text or "")
    while i < len(text):
        c = text[i]
        if c == "\\" and i+1 < len(text) and text[i+1] == ",":
            buf.append(","); i += 2; continue
        if c == ",":
            parts.append("".join(buf).strip()); buf = []; i += 1; continue
        buf.append(c); i += 1
    parts.append("".join(buf).strip())
    return [norm(x) for x in parts if norm(x)]

def vm(a, e):
    a, e = norm(a), norm(e)
    if not e: return True
    return a == e or e in a

def parse_ui(p):
    out = []
    raw = str(p or "")
    seps = ("->", ">", "/")
    segs = None
    for s in seps:
        if s in raw:
            segs = [x.strip() for x in raw.split(s) if x.strip()]
            break
    if segs is None:
        segs = [raw.strip()] if raw.strip() else []
    for part in segs:
        if "||" in part:
            n, t = part.rsplit("||", 1)
        else:
            n, t = part, ""
        out.append((norm(n), norm(t)))
    return out

def score(cand, ctrl):
    methods = split_parts(ctrl.get("targetMethod", ""))
    values = split_parts(ctrl.get("targetValue", ""))
    if not methods: return 0
    if len(methods) != len(values): return -1
    sc = 0
    ctype = norm(cand.get("controlType", ""))
    name = norm(cand.get("name", ""))
    for m, e in zip(methods, values):
        m = m.strip()
        if m == "automation_id":
            if not vm(cand.get("automationId", ""), e): return -1
            sc += 100
        elif m == "control_type":
            if not vm(ctype, e): return -1
            sc += 10
        elif m == "name":
            if not vm(name, e): return -1
            sc += 110
        elif m == "ui_path":
            recorded = parse_ui(e)
            ancestors = cand.get("ancestors", [])
            actual = [(name, ctype)]
            for anc in reversed(ancestors):
                actual.append((norm(anc), ""))
            ok = True
            for rs, asg in zip(reversed(recorded), actual):
                rn, rt = rs; an, at = asg
                if rn and an and not vm(an, rn): ok = False; break
                if rt and at and not vm(at, rt): ok = False; break
            if not ok: return -1
            sc += 30
    return sc

m1_list = {"name": "M1", "controlType": "Text", "automationId": "", "ancestors": [
    "Window_Main", "MicroScaleMainView_View_Main", "MicroScale_TabControl_Site",
    "MUP.MSC.SmartClient.Base.ViewModel.MUPSiteEditorViewModel", "MUPMicroScaleView",
    "MTDClimatologySelectorControl", "PART_ItemsScrollViewer"]}
m1_map = {"name": "M1", "controlType": "Text", "automationId": "", "ancestors": [
    "Window_Main", "MUPMapMainView", "MastPin", "MUPElementLabel"]}

new_def = {"targetMethod": "name,control_type,ui_path",
           "targetValue": "M1,Text,MTDClimatologySelectorControl > PART_ItemsScrollViewer > M1"}
old_def = {"targetMethod": "name,control_type", "targetValue": "M1,Text"}

print("new_def list_M1 =", score(m1_list, new_def))
print("new_def map_M1  =", score(m1_map, new_def))
print("old_def list_M1 =", score(m1_list, old_def))
print("old_def map_M1  =", score(m1_map, old_def))
