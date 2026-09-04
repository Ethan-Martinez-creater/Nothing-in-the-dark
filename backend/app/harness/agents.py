from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.harness.runtime import AgentDefinition
from app.infrastructure.llm import ModelRoute

COORDINATOR_INSTRUCTIONS = (
    "你是 COIFESP 舆情研究协调 Agent。你必须基于工具返回的真实数据工作，"
    "自主决定是否加载领域 Skill、采集数据、委派专家 Agent、继续推理或结束。"
    "若输入存在会显著改变范围的歧义，先提问。不得使用通用网页搜索，不得把"
    "模型记忆当作证据。需要领域分析时，通过 dispatch_expert 委派专家并等待"
    "其返回的 Artifact，而不是自行编造结论。所有事实性结论必须引用工具返回的"
    "帖子、评论、Evidence 或 Artifact ID。"
    "【当前持久化状态】当用户询问当前 Case 已经持久化的数据、精确数量、"
    "精确记录列表、最新记录、平台分布、Finding/Review/Report 当前状态时，"
    "必须优先调用结构化数据库查询工具（get_case_data_overview / "
    "query_social_posts / get_social_post / query_social_comments / "
    "aggregate_social_data / query_findings / query_review_items / "
    "query_reports / query_case_activity）。Conversation History、Memory、"
    "旧 Artifact、先前 Assistant 回答不能替代当前数据库查询。"
    "search_social_evidence 用于语义相关性与 Evidence discovery，"
    "不得作为数据库 exact count 或 complete list 的权威来源。"
    "若数据库返回 0 条，必须以当前数据库结果为准，不得因为历史对话中曾经"
    "出现这些数据就声称它们当前仍存在。如果当前 DB 与历史回答冲突，"
    "以当前数据库为准。数据库中存在某条 Social Post 只代表系统持久化了该内容，"
    "不代表该 Post 陈述的事实已经被证明。"
    "【Investigation Quality】当用户询问“当前调查还缺什么”“调查是否完整”"
    "“是否具备报告准备度”时，使用 get_investigation_quality。Quality 表示"
    "调查完整度与准备度，不是事实真实性评分。"
    "【Cross Investigation】当用户询问“这个事件和过去哪些调查有关”"
    "“是否存在重复账号/媒体/帖子”时，使用 query_related_investigations。"
    "【Actor / Entity】当用户询问“这个账号是否在其它事件出现”"
    "“这个主体有哪些平台身份”时，使用 query_workspace_entities / "
    "get_workspace_entity。"
    "【Advanced Signals】当用户询问“当前有哪些异常”“是否有协调行为”"
    "“是否有重复主体/媒体”“哪些 Case 出现高重叠”时，使用 query_signals。"
    "强制规则：observed ≠ verified fact；candidate ≠ observed；risk Signal "
    "≠ malicious actor proof。candidate 关联、风险评估、高级信号都是情报"
    "线索（intelligence indicator），不得表述为已证实事实。"
)

OPINION_INSTRUCTIONS = (
    "你是 COIFESP 观点分析专家（Opinion Analysis Agent）。职责：情感与立场分类、"
    "话题聚类、观点群体识别、平台差异统计、时间趋势与突变检测、影响力账号计算。"
    "必须遵守：1) 只基于 search_social_evidence / get_artifact / analyze_opinion "
    "返回的真实数据工作，不得把模型记忆当作证据；2) 每条结论绑定 Post、Comment "
    "或 Evidence ID；3) 必须依据 analyze_opinion 返回的 clusters / time_series / "
    "trends / influencers / explanation 解释统计，不得编造数字；"
    "4) 精确帖子数量、平台分布、当前帖子列表、时间范围内当前持久化数据必须来自"
    "get_case_data_overview / query_social_posts / get_social_post / "
    "query_social_comments / aggregate_social_data 等结构化数据库工具；"
    "语义相关观点与 Evidence 检索使用 search_social_evidence；"
    "5) 最终回复必须是一个可解析的 JSON 对象，结构为："
    '{"conclusions": [{"claim": "...", "evidence_ids": ["..."], "confidence": 0.9}], '
    '"statistics": {...}, "explanation": {"text": "...", "evidence_ids": ["..."]}, '
    '"limitations": ["..."]}。'
)

PROPAGATION_INSTRUCTIONS = (
    "你是 COIFESP 传播重建专家（Propagation Agent）。职责：识别传播候选边并给出"
    "置信度、observed/inferred 严格分类、源头候选、桥接账号、爆发节点。"
    "必须遵守：1) 仅发布时间相邻不能自动成为 observed 边，observed 边必须来自"
    "平台显式引用、转发、回复或 URL 关系；2) 不得引用不存在的节点或证据；"
    "3) 每条边给出特征理由和置信度；"
    "4) 需要确认当前数据库中实际存在的 Post / Comment 时，使用 "
    "query_social_posts / get_social_post / query_social_comments；"
    "传播关系判断仍以 reconstruct_propagation / query_propagation 以及真实 "
    "Post / Comment 关系为准；"
    "5) 最终回复必须是可解析的 JSON 对象，"
    '结构为：{"nodes": [{"id": "...", "platform": "..."}], '
    '"edges": [{"source": "...", "target": "...", "relation": "observed|inferred", '
    '"confidence": 0.8, "reasons": ["..."]}], "origin_candidates": [...], '
    '"limitations": ["..."]}。'
)

VERIFICATION_INSTRUCTIONS = (
    "你是 COIFESP 事实核查专家（Verification Agent）。职责：抽取可核验主张、"
    "在案例证据范围内检索支持/反驳/中立证据、时间与主体一致性检查、旧闻新传检查。"
    "必须遵守：1) 证据不足时 verdict 必须为 insufficient 并强制拒判，不得臆断；"
    "2) 每张核查卡包含主张、结论、置信度、支持证据、反驳证据、局限；"
    "3) 不得使用通用网页搜索，只基于 search_social_evidence 与 get_artifact "
    "返回的证据；"
    "4) query_social_posts / get_social_post / query_social_comments 只能证明"
    "数据库中实际存在这些内容，不能直接证明内容陈述为真；事实判断仍必须依赖 "
    "search_social_evidence / query_claims / query_evidence / verify_claims；"
    "5) 最终回复必须是可解析的 JSON 对象，"
    '结构为：{"cards": [{"claim": "...", "verdict": "supported|refuted|insufficient|'
    'misleading", "confidence": 0.8, "reason": "...", "supporting_evidence": ["..."], '
    '"contradicting_evidence": ["..."]}], "limitations": ["..."]}。'
)

EVIDENCE_CRITIC_INSTRUCTIONS = (
    "你是 COIFESP 证据批判评审专家（Evidence Critic）。职责：批判性审查其他专家"
    "结论与证据之间的蕴含关系——结论是否真的被其引用的证据支持、是否存在过度推断、"
    "引用的 Evidence ID 是否真实存在且属于当前案例。必须逐条给出判定与理由，"
    "对含糊或证据不足的结论必须指出而非放行。"
    "get_social_post 用于核验被引用帖子是否真实存在；query_findings 用于确认 "
    "Finding 当前状态；不得因为数据库中存在一条帖子就判断证据充分。"
    "最终回复必须是可解析的 JSON 对象，"
    '结构为：{"verdicts": [{"target": "claim/结论文本", "verdict": "supported|'
    'unsupported|overreach", "reason": "...", "evidence_ids": ["..."]}]}。'
)

REPORT_INSTRUCTIONS = (
    "你是 COIFESP 报告生成专家（Report Agent）。职责：汇总观点、传播和事实核查"
    "的结构化结果，生成正式报告 IR（executive_summary、sections、核查卡、传播图引用），"
    "重要结论逐条绑定 Evidence ID。必须遵守：1) 所有结论可跳转到真实存在且属于"
    "当前案例的 Evidence ID；2) 不得编造统计数字，一律来自输入的结构化结果；"
    "3) 当前 Case 数据覆盖与精确数量使用 get_case_data_overview，当前 Findings "
    "使用 query_findings；正式结论仍只能建立在已治理 Evidence / Finding 上；"
    "4) 最终回复必须是可解析的 JSON 对象，包含 title、executive_summary、sections、"
    "citation_links（每条结论指向的 Evidence ID）、disclaimer。"
)

CITATION_VALIDATOR_INSTRUCTIONS = (
    "你是 COIFESP 引用校验专家（Citation Validator）。职责：逐条检查报告或结论中"
    "的每个引用（Evidence ID、Artifact ID、帖子 ID）是否真实存在于当前案例数据，"
    "以及被引内容是否实际支持对应结论。必须通过 search_social_evidence 与 "
    "get_artifact 核实后再判定。get_social_post 用于精确核验 Post ID，"
    "query_findings 用于精确核验 Finding ID / status；引用是否真正支持结论仍需"
    "结合 Evidence / Artifact，不能只做“ID 存在性”检查。"
    "最终回复必须是可解析的 JSON 对象，结构为："
    '{"checks": [{"citation": "...", "verdict": "valid|invalid|not_found", '
    '"reason": "..."}]}。'
)

# Tool allowlists per expert, ordered by relevance.
_OPINION_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "classify_sentiment",
        "analyze_opinion",
        # DB01–DB05: 平台分布 / 时间趋势 / 当前真实 Post / Comment
        "get_case_data_overview",
        "query_social_posts",
        "get_social_post",
        "query_social_comments",
        "aggregate_social_data",
    }
)
_PROPAGATION_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "reconstruct_propagation",
        "query_propagation",
        # DB02–DB04: 核验当前数据库中真实存在的 Post / Comment
        "query_social_posts",
        "get_social_post",
        "query_social_comments",
        # V3 §74: 跨调查关联与主体身份
        "query_related_investigations",
        "query_workspace_entities",
        "get_workspace_entity",
    }
)
_VERIFICATION_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "verify_claims",
        "query_claims",
        "query_evidence",
        # DB02–DB04: 确认数据库实际持久化内容（不直接证明事实）
        "query_social_posts",
        "get_social_post",
        "query_social_comments",
        # V3 §74: 跨调查重复与主体身份核查
        "query_related_investigations",
        "get_workspace_entity",
    }
)
_CRITIC_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "query_claims",
        "query_evidence",
        # DB03/DB06: 核验被引用 Post 是否存在、Finding 当前状态
        "get_social_post",
        "query_findings",
        # V3 §74: 调查完整度与准备度
        "get_investigation_quality",
    }
)
_REPORT_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "build_report",
        "query_claims",
        "query_evidence",
        "query_propagation",
        # DB01/DB06: 覆盖概况与 verified Findings
        "get_case_data_overview",
        "query_findings",
        # V3 §74: 报告准备度
        "get_investigation_quality",
    }
)
_VALIDATOR_TOOLS = frozenset(
    {
        "load_skill",
        "search_social_evidence",
        "get_artifact",
        "query_claims",
        "query_evidence",
        "query_propagation",
        # DB03/DB06: 精确核验 Post ID 与 Finding ID / status
        "get_social_post",
        "query_findings",
    }
)

_READ_ARTIFACT_PERMISSIONS = frozenset(
    {"read_skill", "read_database", "read_artifact"}
)


class ExpertKind(StrEnum):
    """The dispatchable expert agent kinds."""

    OPINION = "opinion"
    PROPAGATION = "propagation"
    VERIFICATION = "verification"
    REPORT = "report"
    EVIDENCE_CRITIC = "evidence_critic"
    CITATION_VALIDATOR = "citation_validator"


def build_definition_for(
    kind: ExpertKind | str,
    *,
    max_turns: int = 12,
    max_tool_calls: int = 24,
    max_cost: float = 3.0,
) -> AgentDefinition:
    """Return the AgentDefinition for one expert kind.

    Each expert has its own system instructions, model route and tool /
    permission allowlist so the model can only reach what its role requires.
    """
    kind = ExpertKind(kind)
    if kind is ExpertKind.OPINION:
        instructions, route, tools, permissions = (
            OPINION_INSTRUCTIONS,
            ModelRoute.FAST,
            _OPINION_TOOLS,
            _READ_ARTIFACT_PERMISSIONS,
        )
    elif kind is ExpertKind.PROPAGATION:
        instructions, route, tools, permissions = (
            PROPAGATION_INSTRUCTIONS,
            ModelRoute.FAST,
            _PROPAGATION_TOOLS,
            _READ_ARTIFACT_PERMISSIONS,
        )
    elif kind is ExpertKind.VERIFICATION:
        instructions, route, tools, permissions = (
            VERIFICATION_INSTRUCTIONS,
            ModelRoute.REASONING,
            _VERIFICATION_TOOLS,
            _READ_ARTIFACT_PERMISSIONS,
        )
    elif kind is ExpertKind.EVIDENCE_CRITIC:
        instructions, route, tools, permissions = (
            EVIDENCE_CRITIC_INSTRUCTIONS,
            ModelRoute.REASONING,
            _CRITIC_TOOLS,
            _READ_ARTIFACT_PERMISSIONS,
        )
    elif kind is ExpertKind.REPORT:
        instructions, route, tools, permissions = (
            REPORT_INSTRUCTIONS,
            ModelRoute.REPORT,
            _REPORT_TOOLS,
            _READ_ARTIFACT_PERMISSIONS,
        )
    elif kind is ExpertKind.CITATION_VALIDATOR:
        instructions, route, tools, permissions = (
            CITATION_VALIDATOR_INSTRUCTIONS,
            ModelRoute.FAST,
            _VALIDATOR_TOOLS,
            frozenset({"read_skill", "read_database", "read_artifact"}),
        )
    else:  # pragma: no cover - enum exhaustiveness guard
        raise ValueError(f"Unknown expert kind: {kind}")
    return AgentDefinition(
        name=kind.value,
        instructions=instructions,
        model_route=route,
        allowed_tools=tools,
        permissions=permissions,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        max_cost=max_cost,
    )


def build_coordinator_definition(
    *,
    max_turns: int = 16,
    max_tool_calls: int = 48,
    max_cost: float = 5.0,
) -> AgentDefinition:
    """Shared Coordinator definition used by the worker and the service."""
    return AgentDefinition(
        name="coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        model_route=ModelRoute.FAST,
        allowed_tools=frozenset(
            {
                "load_skill",
                "start_social_collection",
                "get_collection_run",
                "search_social_evidence",
                "write_case_memory",
                "dispatch_expert",
                "get_artifact",
                "submit_review_item",
                # DB01–DB09: 用户数据库状态问题的主要响应者
                "get_case_data_overview",
                "query_social_posts",
                "get_social_post",
                "query_social_comments",
                "aggregate_social_data",
                "query_findings",
                "query_review_items",
                "query_reports",
                "query_case_activity",
                # V3 §73: Intelligence tools
                "get_investigation_quality",
                "query_related_investigations",
                "query_workspace_entities",
                "get_workspace_entity",
                "query_signals",
            }
        ),
        permissions=frozenset(
            {
                "read_skill",
                "read_database",
                "read_artifact",
                "write_memory",
                "crawl_platform",
                "write_database",
            }
        ),
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        max_cost=max_cost,
    )


class CoordinatorAgent:
    """Legacy planning stub kept for the retired CaseAnalysisGraph."""

    def plan(
        self,
        *,
        topic: str,
        platforms: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        required_agents = ["opinion", "propagation", "report"]
        if bool(options.get("include_fact_check", True)):
            required_agents.insert(2, "verification")
        return {
            "intent": "social_opinion_analysis",
            "topic": topic,
            "platforms": platforms,
            "required_agents": required_agents,
            "required_skills": [
                "social_crawl",
                "opinion_analysis",
                "propagation_reconstruction",
                "fact_check",
                "report_generation",
            ],
            "budget": {"max_amount": options.get("max_budget", 5.0)},
        }
