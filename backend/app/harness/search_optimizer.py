"""LLM-driven retrieval optimization.

采集与检索的 query 此前由模型内联直出、零加工。这里在工具 handler 内
增加 LLM 环节：
- ``generate_platform_keywords``：根据事件主题与平台特点为每个平台生成
  2-3 组检索关键词（平台用户语体、信息密度、事件侧重点不同）；
- ``rewrite_search_query``：把 RAG 检索 query 重写/扩写为更利于命中的
  形式（补充同义表达、限定词、事件实体）。

任何一步失败都回退到原始输入——优化是增强而非硬依赖。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.infrastructure.llm import LLMGateway, LLMMessage, ModelRoute

logger = logging.getLogger(__name__)

_PLATFORM_PROFILES: dict[str, str] = {
    "weibo": "微博：短文本、实时性强、情绪化表达多、热搜词驱动",
    "bilibili": "哔哩哔哩：中长视频+弹幕评论、科普/盘点/二次创作风格、标题党少",
    "tieba": "百度贴吧：帖子+回复串、吧内黑话、事件细节挖掘、多图多链接",
    "zhihu": "知乎：问答式长文、专业视角、时间线梳理、匿名爆料",
    "douyin": "抖音：短视频+评论区、标题/话题标签驱动、快节奏口播",
}

_KEYWORD_PROMPT = (
    "你是舆情检索词优化器。根据事件主题与各平台的内容特点，"
    "为每个平台生成 2-3 组检索关键词。\n"
    "要求：\n"
    "1. 每组关键词 2-5 个词，贴合该平台的用户语体"
    "（微博用热搜词风格、知乎用问题式表达、B站用视频标题风格等）；\n"
    "2. 覆盖事件的不同侧面（事实描述、人物/组织、争议点、时间线索）；\n"
    "3. 不要包含平台名本身。\n\n"
    "事件主题：{topic}\n"
    "平台画像：\n"
    "{profiles}\n\n"
    '输出严格 JSON 对象，key 为平台名，value 为 2-3 个关键词数组，例如：'
    '{{"weibo": [["事件名", "官方回应"], ["人物名", "争议点"]], '
    '"bilibili": [["事件名", "时间线"]]}}。不要输出 platform/keywords 包装层。'
)

_QUERY_PROMPT = """你是舆情证据检索词优化器。把用户的检索意图改写为更可能命中的检索词。
原始检索：{query}
事件背景（如有）：{topic}

要求：
1. 保留原意，补充同义表达、常见别名、事件相关实体/人名/组织名；
2. 输出 1 组 3-8 个检索词，用空格分隔；
3. 只输出检索词本身，不要解释。

输出："""


async def generate_platform_keywords(
    llm: LLMGateway | None,
    topic: str,
    platforms: list[str],
) -> dict[str, list[str]]:
    """为每个平台生成 2-3 组检索关键词；失败或未注入 LLM 时返回 {platform: [topic]}。"""
    fallback: dict[str, list[str]] = {
        platform: [topic] for platform in platforms
    }
    if llm is None or not platforms:
        return fallback
    profiles = "\n".join(
        f"- {_PLATFORM_PROFILES.get(p, '通用：混合文本形态')}" for p in platforms
    )
    try:
        response = await llm.complete(
            messages=[
                LLMMessage(role="system", content="你是检索词优化器，只输出 JSON。"),
                LLMMessage(role="user", content=_KEYWORD_PROMPT.format(
                    topic=topic, profiles=profiles
                )),
            ],
            route=ModelRoute.FAST,
            tools=[],
        )
        text = response.message.content or ""
        payload: Any = json.loads(text)
        if not isinstance(payload, dict):
            return fallback
        result: dict[str, list[str]] = {}
        for platform, groups in payload.items():
            if platform not in platforms:
                continue
            if isinstance(groups, list) and groups:
                cleaned: list[str] = []
                for group in groups:
                    if isinstance(group, list):
                        cleaned.append(" ".join(str(item) for item in group))
                    elif isinstance(group, str):
                        cleaned.append(group)
                result[platform] = cleaned[:3] or [topic]
            else:
                result[platform] = [topic]
        return result or fallback
    except Exception:
        logger.exception("keyword optimization failed; falling back to topic")
        return fallback


async def rewrite_search_query(
    llm: LLMGateway | None,
    query: str,
    topic: str | None = None,
) -> str:
    """重写/扩写检索 query；失败或未注入 LLM 时原样返回。"""
    if llm is None or not query.strip():
        return query
    try:
        response = await llm.complete(
            messages=[
                LLMMessage(role="system", content="你是检索词优化器。"),
                LLMMessage(role="user", content=_QUERY_PROMPT.format(
                    query=query, topic=topic or ""
                )),
            ],
            route=ModelRoute.FAST,
            tools=[],
        )
        rewritten = (response.message.content or "").strip()
        return rewritten if rewritten else query
    except Exception:
        logger.exception("query rewrite failed; using original query")
        return query
