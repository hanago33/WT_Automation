# encoding: utf-8
"""WT_AUTOMATION_Agent 命令行接口。

支持从命令行直接使用 Agent 进行自然语言到步骤的转换。

用法：
    python -m WT_AUTOMATION_Agent.cli "点击风机类型按钮"
    python -m WT_AUTOMATION_Agent.cli --sequence "先点击新建，然后输入名称，最后确认"
    python -m WT_AUTOMATION_Agent.cli --list-schemas
    python -m WT_AUTOMATION_Agent.cli --gui
"""
from __future__ import annotations

import json
import os
import sys
import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WT_AUTOMATION_Agent - 自然语言 RPA 流程构建 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("instruction", nargs="?", default="", help="自然语言指令")
    parser.add_argument(
        "--sequence", "-s",
        action="store_true",
        help="作为多步序列处理（使用 add_sequence）",
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="输出到 JSON 文件",
    )
    parser.add_argument(
        "--list-schemas",
        action="store_true",
        help="列出所有支持的 action 及其参数",
    )
    parser.add_argument(
        "--list-skills",
        action="store_true",
        help="列出可用的内置 Skill",
    )
    parser.add_argument(
        "--project-desc",
        default="",
        help="项目描述文字",
    )
    parser.add_argument(
        "--control-file",
        default="",
        help="控件库 JSON 文件路径（flow_definition.json 格式）",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="对话模式（按文本返回，不生成步骤）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出详细日志",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动对话式 GUI 界面",
    )
    parser.add_argument(
        "--gui-port", "-p",
        type=int,
        default=None,
        help="GUI 监听端口（默认自动选择）",
    )
    parser.add_argument(
        "--gui-no-browser",
        action="store_true",
        help="不自动打开浏览器",
    )
    return parser


def list_schemas() -> None:
    """列出所有支持的 action。"""
    from WT_AUTOMATION_Agent.schemas import get_action_names, get_action_schema

    print("=" * 60)
    print("WT_AUTOMATION_Agent 支持的 Action 列表")
    print("=" * 60)
    for name in get_action_names():
        schema = get_action_schema(name)
        print(f"\n  {name}")
        print(f"    标签: {schema.get('label', '')}")
        print(f"    描述: {schema.get('description', '')}")
        reqs = []
        if schema.get("target_required"):
            reqs.append("需要目标控件")
        if schema.get("input_required"):
            reqs.append(f"需要输入({schema.get('input_key', '')})")
        print(f"    要求: {', '.join(reqs) or '无'}")


def list_skills() -> None:
    """列出内置 Skill。"""
    from WT_AUTOMATION_Agent.skill_bridge import get_builtin_skills

    skills = get_builtin_skills()
    print("=" * 60)
    print("WT_AUTOMATION_Agent 内置 Skill")
    print("=" * 60)
    for skill in skills:
        print(f"\n  {skill.name}")
        print(f"    描述: {skill.description}")
        print(f"    内容预览: {skill.content[:100]}...")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.INFO)

    if args.list_schemas:
        list_schemas()
        return

    if args.list_skills:
        list_skills()
        return

    if args.gui:
        from WT_AUTOMATION_Agent.gui import start_server
        start_server(port=args.gui_port, open_browser=not args.gui_no_browser)
        return

    if not args.instruction:
        parser.print_help()
        return

    # 构建 Agent
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    config = DslAgentConfig()
    if not config.is_ready():
        print(
            "错误: Agent 未配置。请设置环境变量:\n"
            "  WT_DSL_BASE_URL - API 地址\n"
            "  WT_DSL_API_KEY  - API Key\n"
            "  WT_DSL_MODEL    - 模型名（可选）",
            file=sys.stderr,
        )
        sys.exit(1)

    agent = DslAgent(config)

    # 构建上下文
    skill_text = load_all_skills_text()
    context = build_context_for_agent(
        flow_path=args.control_file or None,
        project_description=args.project_desc,
        skill_text=skill_text,
    )

    try:
        if args.chat:
            # 对话模式
            result = agent.chat(args.instruction, context)
            print(result)
        elif args.sequence:
            steps = agent.nl_to_sequence(args.instruction, context)
            output = _wrap_flow(steps)
            print(output)
        else:
            steps = agent.nl_to_step(args.instruction, context)
            output = _wrap_flow(steps)
            print(output)

        if args.output and not args.chat:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\n已保存到: {args.output}", file=sys.stderr)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def _wrap_flow(steps: object) -> str:
    """把 Agent 产物包装成 WT_AUT_recorded.py 可直接消费的 flow_definition。

    Agent 的 nl_to_sequence 返回步骤数组、nl_to_step 返回单个步骤字典；
    而执行器只识别顶层含 "steps" 字段的对象（非 dict 会被当成空流程）。
    这里统一包成 {"steps": [...]} 以保证"生成即可执行"。
    """
    if isinstance(steps, dict):
        steps_list = [steps]
    elif isinstance(steps, list):
        steps_list = steps
    else:
        steps_list = []
    return json.dumps({"steps": steps_list}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
