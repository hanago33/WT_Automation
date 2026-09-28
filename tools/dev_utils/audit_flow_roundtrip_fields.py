import argparse
import json
import os
import sys


KEY_FIELDS = (
    "windowTitle",
    "actionConfig.parentWindow.title",
    "actionConfig.fallbackTemplate",
    "actionConfig.continueWhen",
)


def _load_json(file_path):
    with open(file_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _get_nested_value(payload, dotted_path):
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _normalize_value(value):
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _build_step_map(payload):
    step_map = {}
    for step in payload.get("steps", []):
        step_id = str(step.get("id", "")).strip()
        if step_id:
            step_map[step_id] = step
    return step_map


def compare_roundtrip_fields(baseline_path, candidate_path):
    baseline_payload = _load_json(baseline_path)
    candidate_payload = _load_json(candidate_path)
    baseline_steps = _build_step_map(baseline_payload)
    candidate_steps = _build_step_map(candidate_payload)

    issues = []
    for step_id, baseline_step in baseline_steps.items():
        candidate_step = candidate_steps.get(step_id)
        if candidate_step is None:
            issues.append(
                {
                    "stepId": step_id,
                    "field": "<step>",
                    "issue": "missing_step",
                    "baseline": baseline_step.get("name", ""),
                    "candidate": "",
                }
            )
            continue
        for field_path in KEY_FIELDS:
            baseline_value = _get_nested_value(baseline_step, field_path)
            candidate_value = _get_nested_value(candidate_step, field_path)
            if _normalize_value(baseline_value) != _normalize_value(candidate_value):
                issues.append(
                    {
                        "stepId": step_id,
                        "field": field_path,
                        "issue": "value_changed",
                        "baseline": baseline_value,
                        "candidate": candidate_value,
                    }
                )
    return issues


def main():
    parser = argparse.ArgumentParser(
        description="对比流程 JSON 的关键回灌字段，检查 Excel 往返后是否丢失 windowTitle、parentWindow.title、fallbackTemplate、continueWhen。"
    )
    parser.add_argument("--baseline", required=True, help="稳定基线 JSON 路径")
    parser.add_argument("--candidate", required=True, help="回灌后的候选 JSON 路径")
    args = parser.parse_args()

    baseline_path = os.path.abspath(args.baseline)
    candidate_path = os.path.abspath(args.candidate)
    issues = compare_roundtrip_fields(baseline_path, candidate_path)

    if not issues:
        print("OK: 未发现关键回灌字段差异")
        return 0

    print("FOUND_DIFFS: 检测到关键回灌字段差异")
    for issue in issues:
        print(
            json.dumps(
                issue,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
