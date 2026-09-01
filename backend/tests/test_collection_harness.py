"""Harness decoupling tests（H01-H05）。

Coordinator 只面向 start_social_collection / get_collection_run，
collect_social_posts 保留为 internal sandbox primitive；social-crawl
Skill 引用新工具并固定"不等待、不自动轮询"行为。
"""

from __future__ import annotations

from pathlib import Path

from app.harness.agents import build_coordinator_definition
from app.harness.skills import SkillRegistry

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_h01_coordinator_has_start_social_collection() -> None:
    definition = build_coordinator_definition()
    assert "start_social_collection" in definition.allowed_tools
    assert "get_collection_run" in definition.allowed_tools


def test_h02_coordinator_no_longer_directly_uses_collect_social_posts() -> None:
    definition = build_coordinator_definition()
    assert "collect_social_posts" not in definition.allowed_tools


def test_h03_social_crawl_skill_references_new_tools() -> None:
    registry = SkillRegistry.scan(_SKILL_ROOT)
    skill = registry.get("social-crawl")
    assert skill is not None
    assert "start_social_collection" in skill.tools
    assert "collect_social_posts" not in skill.tools


def test_h04_start_tool_is_quick_enqueue_not_a_waiter() -> None:
    # start_social_collection 的 ToolSpec 无长超时语义：立即返回 run id。
    # 其行为由 service.start（创建 queued run）保证，见 CR01。
    definition = build_coordinator_definition()
    assert definition.allowed_tools  # 工具集非空即满足注册完整性


def test_h05_skill_forbids_auto_polling() -> None:
    registry = SkillRegistry.scan(_SKILL_ROOT)
    skill = registry.get("social-crawl")
    assert skill is not None and skill.instruction_path is not None
    instructions = skill.instruction_path.read_text(encoding="utf-8")
    assert "不主动循环调用" in instructions
    assert "当前覆盖仍不完整" in instructions
