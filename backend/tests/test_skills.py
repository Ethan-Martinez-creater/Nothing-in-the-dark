"""Skill manifest mechanism: frontmatter parsing, disk scanning, tool and
permission dependency validation, versioning and cost estimates."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.harness.skills import (
    SkillRegistry,
    parse_skill_manifest,
)
from app.harness.tool_factory import build_tool_registry
from app.infrastructure.crawler.demo import DemoCrawlerAdapter
from app.infrastructure.database import Database
from app.infrastructure.database.knowledge_repository import KnowledgeRepository
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.embeddings import EmbeddingWorkerClient

_SKILL_DIR = Path(__file__).resolve().parents[1] / "skills"

_VALID_SKILL = """---
name: demo-skill
version: 1.2.3
description: A demo skill
tools: [tool_a, tool_b]
permissions: [read_database]
inputs: [query]
outputs: [result]
cost_tokens: 5000
cancellation: abortable
---
# Demo Skill

Do the demo thing with evidence.
"""


def _write_skill(root: Path, text: str) -> Path:
    (root / "demo-skill").mkdir(parents=True, exist_ok=True)
    path = root / "demo-skill" / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------- frontmatter 解析 ----------

def test_parse_manifest_fields_and_body(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, _VALID_SKILL)
    manifest = parse_skill_manifest(path)

    assert manifest.name == "demo-skill"
    assert manifest.version == "1.2.3"
    assert manifest.description == "A demo skill"
    assert manifest.tools == ("tool_a", "tool_b")
    assert manifest.permissions == ("read_database",)
    assert [io.name for io in manifest.inputs] == ["query"]
    assert [io.name for io in manifest.outputs] == ["result"]
    assert manifest.cost is not None
    assert manifest.cost.estimated_tokens == 5000
    assert manifest.cancellation == "abortable"
    assert manifest.instruction_path == path

    # load() 返回正文（不含 frontmatter）
    registry = SkillRegistry()
    registry.register(manifest)
    instructions = registry.load("demo-skill")
    assert instructions.startswith("# Demo Skill")
    assert "with evidence" in instructions
    assert "tools:" not in instructions


def test_parse_manifest_missing_optional_fields(tmp_path: Path) -> None:
    text = _VALID_SKILL.replace("description: A demo skill\n", "")
    text = text.replace("cost_tokens: 5000\n", "")
    text = text.replace("cancellation: abortable\n", "")
    path = _write_skill(tmp_path, text)
    manifest = parse_skill_manifest(path)

    # description 缺失时从正文首个 # 标题提取
    assert manifest.description == "Demo Skill"
    assert manifest.cost is None
    assert manifest.cancellation == "restartable"  # 默认值


def test_parse_manifest_missing_required_field(tmp_path: Path) -> None:
    text = _VALID_SKILL.replace("tools: [tool_a, tool_b]\n", "")
    path = _write_skill(tmp_path, text)
    with pytest.raises(ApplicationError) as exc:
        parse_skill_manifest(path)
    assert "missing fields" in str(exc.value)


def test_parse_manifest_invalid_version(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, _VALID_SKILL.replace("1.2.3", "abc"))
    with pytest.raises(ApplicationError) as exc:
        parse_skill_manifest(path)
    assert exc.value.code == "invalid_skill_version"


def test_parse_manifest_unknown_cancellation(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path, _VALID_SKILL.replace("abortable", "nuclear")
    )
    with pytest.raises(ApplicationError) as exc:
        parse_skill_manifest(path)
    assert exc.value.code == "invalid_skill_cancellation"


def test_parse_manifest_no_frontmatter(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "# No Frontmatter\n\nplain instructions\n")
    with pytest.raises(ApplicationError) as exc:
        parse_skill_manifest(path)
    assert exc.value.code == "invalid_skill_manifest"


def test_parse_manifest_unterminated_frontmatter(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "---\nname: demo\nversion: 1.0.0\n")
    with pytest.raises(ApplicationError):
        parse_skill_manifest(path)


def test_parse_manifest_list_items_with_quotes(tmp_path: Path) -> None:
    text = _VALID_SKILL.replace("tool_a, tool_b", '"tool_a", \'tool_b\'')
    path = _write_skill(tmp_path, text)
    manifest = parse_skill_manifest(path)
    assert manifest.tools == ("tool_a", "tool_b")


def test_parse_manifest_non_integer_cost(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, _VALID_SKILL.replace("5000", "many"))
    with pytest.raises(ApplicationError) as exc:
        parse_skill_manifest(path)
    assert exc.value.code == "invalid_skill_cost"


# ---------- Registry ----------

def test_scan_loads_all_eight_skills() -> None:
    registry = SkillRegistry.scan(_SKILL_DIR)

    described = registry.describe()
    names = {entry["name"] for entry in described}
    assert names == {
        "social-crawl",
        "social-normalization",
        "opinion-research",
        "propagation-reconstruction",
        "claim-verification",
        "evidence-review",
        "report-generation",
        "case-follow-up",
    }
    # 每个 skill 都能加载指令正文（完成标准：八个 Skill 独立加载/校验）
    for entry in described:
        assert entry["loadable"] is True
        assert registry.load(str(entry["name"]))
        assert registry.estimate_cost(str(entry["name"])) > 0
        assert entry["cancellation"] in {"restartable", "abortable", "checkpointed"}
        assert entry["inputs"] and entry["outputs"]


def test_register_duplicate_rejected() -> None:
    registry = SkillRegistry.scan(_SKILL_DIR)
    with pytest.raises(ApplicationError) as exc:
        registry.register(registry.get("social-crawl"))
    assert exc.value.code == "duplicate_skill"


def test_unknown_skill_not_found() -> None:
    registry = SkillRegistry()
    with pytest.raises(ApplicationError) as exc:
        registry.load("nope")
    assert exc.value.code == "skill_not_found"


def test_validate_tools_reports_missing(tmp_path: Path) -> None:
    _write_skill(tmp_path, _VALID_SKILL)
    registry = SkillRegistry.scan(tmp_path)
    missing = registry.validate_tools({"tool_b"})
    assert missing == ["tool_a"]


def test_validate_permissions_reports_unknown(tmp_path: Path) -> None:
    _write_skill(tmp_path, _VALID_SKILL)
    registry = SkillRegistry.scan(tmp_path)
    # 白名单不含 manifest 声明的权限时报告未知权限
    unknown = registry.validate_permissions({"other_permission"})
    assert unknown == ["read_database"]


# ---------- 真实装配集成：manifest 与真实工具/权限集一致 ----------

def test_real_skills_pass_tool_and_permission_validation() -> None:
    """首批八个 Skill 的 manifest 必须能通过真实 ToolRegistry 的工具依赖
    校验与系统权限白名单（bootstrap 装配时会 fail fast）。"""
    registry = SkillRegistry.scan(_SKILL_DIR)
    database = Database("sqlite+aiosqlite:///:memory:")
    repository = ApplicationRepository(database)
    knowledge = KnowledgeRepository(database)
    social = SocialRepository(database)
    embeddings = EmbeddingWorkerClient("", dimensions=1024, timeout_seconds=1)
    tools = build_tool_registry(
        DemoCrawlerAdapter(),
        registry,
        knowledge,
        embeddings,
        social,
        repository,
    )

    assert registry.validate_tools(tools.names()) == []
    known_permissions = {
        permission
        for spec in (tools.get(name) for name in tools.names())
        for permission in spec.permissions
    }
    known_permissions.add("write_artifact")  # 系统级权限（写入 artifact）
    known_permissions.add("read_skill")  # load_skill 自身权限
    assert registry.validate_permissions(known_permissions) == []
