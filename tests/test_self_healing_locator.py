# encoding: utf-8
"""#4 自愈式选择器单测：降级检测、学习覆盖持久化、会话上报。"""

import json
import os
import types

import pytest

import wt_flow_locator as L


class FakeWrapper:
    """模拟 pywinauto wrapper，数据取自传入 dict。"""

    def __init__(self, data):
        self._data = dict(data or {})
        ei = self._data.get("element_info") or {}
        self.element_info = types.SimpleNamespace(
            automation_id=ei.get("automation_id", ""),
            control_type=ei.get("control_type", ""),
            localized_control_type=ei.get("localized_control_type", ""),
            framework_id=ei.get("framework_id", ""),
            help_text=ei.get("help_text", ""),
            process_id=ei.get("process_id", ""),
        )

    def window_text(self):
        return self._data.get("name", "")

    def class_name(self):
        return self._data.get("className", "")


def _control_def(automation_id, name, control_type="Button", extra=None):
    cd = {
        "inspectData": {
            "automationId": automation_id,
            "name": name,
            "controlType": control_type,
        }
    }
    if extra:
        cd.update(extra)
    return cd


@pytest.fixture(autouse=True)
def _reset_self_heal(tmp_path, monkeypatch):
    L.SELF_HEAL_ENABLED = True
    L._self_heal_overrides = {}
    L._self_heal_session_hits = []
    L.SELF_HEAL_STORE_PATH = os.path.join(str(tmp_path), "self_heal_store.json")
    monkeypatch.setattr(L, "_LOG_STEP", lambda message: L._CAPTURED_LOGS.append(message))
    L._CAPTURED_LOGS = []
    yield
    L.SELF_HEAL_ENABLED = True
    L._self_heal_overrides = {}
    L._self_heal_session_hits = []


def test_detect_healed_locator_primary_match_is_priority_zero():
    cd = _control_def("btnOK", "确定")
    # 主定位器 automationId 仍生效
    wrapper = FakeWrapper({"name": "确定", "className": "Button",
                           "element_info": {"automation_id": "btnOK", "control_type": "Button"}})
    healed = L.detect_healed_locator(wrapper, cd)
    assert healed is not None
    assert healed["priority"] == 0
    assert healed["method"].startswith("automation_id")


def test_detect_healed_locator_degraded_match_is_priority_gt_zero():
    cd = _control_def("btnOK_old", "确定")  # 录制的 automationId 已失效
    # 实际控件 automationId 变了，但 name 不变 -> 降级自愈
    wrapper = FakeWrapper({"name": "确定", "className": "Button",
                           "element_info": {"automation_id": "btnOK_new", "control_type": "Button"}})
    healed = L.detect_healed_locator(wrapper, cd)
    assert healed is not None
    assert healed["priority"] > 0
    assert healed["method"].startswith("name")
    assert "确定" in healed["value"]


def test_apply_self_heal_override_makes_learned_locator_primary():
    cd = _control_def("btnOK_old", "确定")
    L.record_self_heal("step_1", "c1", "name", "确定")
    cloned = L._apply_self_heal_override("step_1", "c1", cd)
    assert cloned is not cd  # 不改动原对象
    assert cloned["targetMethod"] == "name"
    assert cloned["targetValue"] == "确定"
    # build_common_locator_candidates 把 targetMethod 排到 priority 0
    candidates = L.build_common_locator_candidates(cloned)
    assert candidates[0] == ("name", "确定")


def test_apply_self_heal_override_no_override_returns_original():
    cd = _control_def("btnOK", "确定")
    assert L._apply_self_heal_override("step_x", "cX", cd) is cd


def test_record_and_persist_override(tmp_path):
    store = os.path.join(str(tmp_path), "store.json")
    L.configure_self_heal(store_path=store)
    L.record_self_heal("step_1", "c1", "name", "确定", score=42)
    # 重新载入（模拟新进程）
    L._self_heal_overrides = {}
    L._self_heal_overrides = L._load_self_heal_store()
    ov = L.get_self_heal_override("step_1", "c1")
    assert ov == {"method": "name", "value": "确定", "score": 42}
    assert os.path.exists(store)


def test_disabled_self_heal_does_not_record(tmp_path):
    store = os.path.join(str(tmp_path), "store.json")
    L.configure_self_heal(enabled=False, store_path=store)
    L.record_self_heal("step_1", "c1", "name", "确定")
    assert L.get_self_heal_override("step_1", "c1") is None
    assert not os.path.exists(store)


def test_maybe_report_self_heal_records_on_degraded_match():
    cd = _control_def("btnOK_old", "确定")
    controls = [cd]
    wrapper = FakeWrapper({"name": "确定", "className": "Button",
                           "element_info": {"automation_id": "btnOK_new", "control_type": "Button"}})
    L._maybe_report_self_heal("step_1", "c1", wrapper, controls)
    ov = L.get_self_heal_override("step_1", "c1")
    assert ov is not None and ov["method"].startswith("name")
    assert len(L.get_self_heal_report()) == 1
    assert any("自愈" in msg for msg in L._CAPTURED_LOGS)


def test_maybe_report_self_heal_skips_primary_match():
    cd = _control_def("btnOK", "确定")
    controls = [cd]
    wrapper = FakeWrapper({"name": "确定", "className": "Button",
                           "element_info": {"automation_id": "btnOK", "control_type": "Button"}})
    L._maybe_report_self_heal("step_1", "c1", wrapper, controls)
    assert L.get_self_heal_override("step_1", "c1") is None
    assert len(L.get_self_heal_report()) == 0


def test_self_heal_summary_shape():
    L.record_self_heal("step_1", "c1", "name", "确定")
    summary = L.self_heal_summary()
    assert summary["enabled"] is True
    assert summary["learnedCount"] == 1
    assert len(summary["sessionHits"]) == 0  # 仅 record 不计入会话自愈事件
