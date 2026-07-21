# encoding: utf-8
"""WT_AUTOMATION_Agent 快速入门示例。

本文件展示 Agent 的常见使用场景。
运行前请设置环境变量：
    WT_DSL_BASE_URL - API 地址（如 https://api.openai.com/v1）
    WT_DSL_API_KEY  - API Key
    WT_DSL_MODEL    - 模型名（可选，默认 gpt-4o）

或直接修改下方示例中的 base_url/api_key。
"""

import json
import os
import sys


# ──────────────────────────────────────────────────────────────────
# 示例 1：基础用法 — 单个自然语言指令 → 步骤
# ──────────────────────────────────────────────────────────────────

def example_single_step():
    """将一句自然语言指令转换为一个自动化步骤。"""
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    # 1. 创建配置（可从环境变量自动加载）
    config = DslAgentConfig(
        base_url=os.environ.get("WT_DSL_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("WT_DSL_API_KEY", ""),
        model=os.environ.get("WT_DSL_MODEL", "gpt-4o"),
    )

    if not config.is_ready():
        print("❌ 请设置 WT_DSL_BASE_URL 和 WT_DSL_API_KEY 环境变量")
        return

    # 2. 创建 Agent
    agent = DslAgent(config)

    # 3. 构建上下文（含 Skill 知识）
    skill_text = load_all_skills_text()
    context = build_context_for_agent(
        project_description="Meteodyn WT 风资源仿真软件自动化",
        skill_text=skill_text,
    )

    # 4. 转换自然语言指令
    nl = "点击确认按钮"
    print(f"🔄 转换指令: {nl}")
    steps = agent.nl_to_step(nl, context)
    print(f"✅ 生成 {len(steps)} 个步骤:")
    print(json.dumps(steps, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────────────────────────
# 示例 2：多步流程转换
# ──────────────────────────────────────────────────────────────────

def example_multi_step():
    """将一段流程描述转换为多个步骤。"""
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    config = DslAgentConfig()
    if not config.is_ready():
        print("❌ 请设置 WT_DSL_BASE_URL 和 WT_DSL_API_KEY 环境变量")
        return

    agent = DslAgent(config)
    skill_text = load_all_skills_text()
    context = build_context_for_agent(
        project_description="Meteodyn WT 风资源仿真软件自动化",
        skill_text=skill_text,
    )

    nl = (
        "先点击'新建项目'按钮，"
        "然后在项目名称输入框中输入'我的项目'，"
        "最后点击'确认'按钮完成创建"
    )
    print(f"🔄 转换流程: {nl}")
    steps = agent.nl_to_sequence(nl, context)
    print(f"✅ 生成 {len(steps)} 个步骤:")
    print(json.dumps(steps, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────────────────────────
# 示例 3：使用控件库上下文
# ──────────────────────────────────────────────────────────────────

def example_with_controls():
    """指定控件库后进行精确转换。"""
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent, build_index_from_controls
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    config = DslAgentConfig()
    if not config.is_ready():
        print("❌ 请设置 WT_DSL_BASE_URL 和 WT_DSL_API_KEY 环境变量")
        return

    agent = DslAgent(config)

    # 手动构造控件库
    controls = {
        "btn_new_project": {"name": "新建项目", "className": "Button", "controlType": "Button"},
        "input_project_name": {"name": "项目名称输入框", "className": "Edit", "controlType": "Edit"},
        "btn_confirm": {"name": "确认", "className": "Button", "controlType": "Button"},
        "btn_cancel": {"name": "取消", "className": "Button", "controlType": "Button"},
    }
    control_index_text = build_index_from_controls(controls)

    skill_text = load_all_skills_text()
    context = build_context_for_agent(
        control_index_text=control_index_text,
        project_description="Meteodyn WT 项目管理界面",
        skill_text=skill_text,
    )

    nl = "点新建项目，输入'测试工程'，再点确认"
    print(f"🔄 使用控件库转换: {nl}")
    steps = agent.nl_to_sequence(nl, context)
    print(f"✅ 生成 {len(steps)} 个步骤（control_id 应来自控件库）:")
    print(json.dumps(steps, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────────────────────────
# 示例 4：对话模式 — 回答流程相关问题
# ──────────────────────────────────────────────────────────────────

def example_chat():
    """使用对话模式询问 LLM 关于流程的问题。"""
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent

    config = DslAgentConfig()
    if not config.is_ready():
        print("❌ 请设置 WT_DSL_BASE_URL 和 WT_DSL_API_KEY 环境变量")
        return

    agent = DslAgent(config)
    context = build_context_for_agent(
        project_description="Meteodyn WT 风资源仿真软件",
    )

    question = "在风资源评估中，导入气象数据后通常需要做什么操作？"
    print(f"💬 询问: {question}")
    answer = agent.chat(question, context)
    print(f"💬 回答:\n{answer}")


# ──────────────────────────────────────────────────────────────────
# 示例 5：CLI 模式 — 使用命令行接口
# ──────────────────────────────────────────────────────────────────

def example_cli():
    """展示 CLI 的等效命令行调用。"""
    print("=" * 60)
    print("CLI 使用示例：")
    print("=" * 60)
    print()
    print("  # 列出所有支持的 action：")
    print("  python -m WT_AUTOMATION_Agent.cli --list-schemas")
    print()
    print("  # 列出内置 Skill：")
    print("  python -m WT_AUTOMATION_Agent.cli --list-skills")
    print()
    print("  # 单步转换：")
    print('  python -m WT_AUTOMATION_Agent.cli "点击确认按钮"')
    print()
    print("  # 序列转换：")
    print('  python -m WT_AUTOMATION_Agent.cli -s "先点击新建，再输入名称，最后确认"')
    print()
    print("  # 输出到文件：")
    print('  python -m WT_AUTOMATION_Agent.cli -o steps.json "点击按钮并输入文本"')
    print()
    print("  # 对话模式：")
    print('  python -m WT_AUTOMATION_Agent.cli --chat "这个流程是做什么的？"')
    print()
    print("  # 加载控件文件：")
    print('  python -m WT_AUTOMATION_Agent.cli --control-file flow_definition.json "点击新建项目"')


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("WT_AUTOMATION_Agent 快速入门示例")
    print("=" * 60)
    print()

    if not os.environ.get("WT_DSL_BASE_URL") and not os.environ.get("WT_DSL_API_KEY"):
        print("⚠️  未设置 API 环境变量，将使用默认值。")
        print("   建议设置: WT_DSL_BASE_URL, WT_DSL_API_KEY, WT_DSL_MODEL")
        print()

    # 运行示例（默认跳过实际 API 调用，只展示方法调用方式）
    print("📋 示例文件结构：")
    print("  1. example_single_step()   - 单步转换")
    print("  2. example_multi_step()    - 多步流程转换")
    print("  3. example_with_controls() - 带控件库的转换")
    print("  4. example_chat()          - 对话模式")
    print("  5. example_cli()           - CLI 使用说明")
    print()

    # 默认只展示 CLI 示例（不调用 API）
    example_cli()

    # 取消注释以下行以实际运行各个示例：
    # example_single_step()
    # example_multi_step()
    # example_with_controls()
    # example_chat()
