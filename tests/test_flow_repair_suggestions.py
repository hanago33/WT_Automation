# encoding: utf-8
"""LLM 语义修复建议解析测试：proposed_patch 字段白名单与返回结构。"""
import json
import unittest
from unittest import mock

from WT_AUTOMATION_Agent import agent as agent_module
from WT_AUTOMATION_Agent.agent import DslAgent, DslAgentConfig


class FlowRepairSuggestionsTests(unittest.TestCase):
    def test_proposed_patch_is_parsed_and_filtered(self):
        flow = {
            "steps": [{
                "id": "s1",
                "name": "点击 保存",
                "actionConfig": {"action": "click", "controlId": "c1"},
            }]
        }
        report = {
            "pending_confirm": [{
                "step_index": 1,
                "step_name": "点击 保存",
                "category": "control",
                "message": "控件语义不明",
                "suggestion": "补充标签后人工确认",
            }]
        }
        llm_payload = [
            {
                "step_index": 1,
                "issue": "控件与动作语义不符",
                "suggestion": "将步骤名改为保存",
                "proposed_patch": {
                    "name": "保存",
                    "actionConfig": {"action": "click", "controlId": "c1"},
                    "evil_key": "should_be_dropped",
                },
            }
        ]
        fake_response = {
            "choices": [{"message": {"content": json.dumps(llm_payload, ensure_ascii=False)}}]
        }
        agent = DslAgent(DslAgentConfig(base_url="http://fake.local", api_key="test"))

        with mock.patch.object(agent_module, "_call_llm", return_value=fake_response):
            items = agent.repair_flow_suggestions(flow, report)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["step_index"], 1)
        self.assertEqual(items[0]["proposed_patch"]["name"], "保存")
        self.assertEqual(items[0]["proposed_patch"]["actionConfig"]["controlId"], "c1")
        self.assertNotIn("evil_key", items[0]["proposed_patch"])


if __name__ == "__main__":
    unittest.main()
