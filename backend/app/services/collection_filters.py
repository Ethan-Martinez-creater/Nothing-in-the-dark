"""C6: Collection Definition exclusions / filters 的真实采集过滤。

- exclusions：在 normalized post 的可见文本字段（title/content/description/
  summary/text）上做 case-insensitive substring 排除；命中任一排除词的
  post 不进入后续 coverage/persistence，comment 跟随父记录一起被排除。
- filters：只支持当前能确定语义的 key（``generated_by`` 为内部生成来源
  标记，无过滤语义）；未知 key 明确报 ``collection_filter_unsupported``，
  禁止"保存成功、运行时忽略"。

不修改第三方 MediaCrawler 搜索 DSL，不绕过 SocialCrawlerPort。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ApplicationError

# filters 白名单：generated_by 是 generate() 写入的内部标记（llm/fallback），
# 不是采集过滤条件；当前没有其他可确定映射到 normalized 数据的 filter key。
SUPPORTED_COLLECTION_FILTER_KEYS = {"generated_by"}

_TEXT_FIELDS = ("title", "content", "description", "summary", "text")


def validate_collection_filters(filters: dict[str, Any] | None) -> None:
    """创建/修订与 crawl 运行时共用的 filter key 校验（fail closed）。"""
    for key in (filters or {}):
        if key not in SUPPORTED_COLLECTION_FILTER_KEYS:
            raise ApplicationError(
                f"unsupported collection filter key '{key}'",
                code="collection_filter_unsupported",
            )


def _post_text(post: dict[str, Any]) -> str:
    parts = []
    for field in _TEXT_FIELDS:
        value = post.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts).lower()


def apply_collection_exclusions(
    posts: list[dict[str, Any]],
    exclusions: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按 active definition 的 exclusions 过滤 normalized posts。

    comment 跟随父记录处理（comment 数据内嵌在 post 结构内，排除父记录
    即一并排除）。返回 (kept_posts, stats)。
    """
    terms = [str(term).strip().lower() for term in (exclusions or []) if str(term).strip()]
    if not terms:
        return posts, {"before": len(posts), "after": len(posts), "excluded": 0}
    kept: list[dict[str, Any]] = []
    excluded = 0
    for post in posts:
        text = _post_text(post)
        if any(term in text for term in terms):
            excluded += 1
            continue
        kept.append(post)
    return kept, {
        "before": len(posts),
        "after": len(kept),
        "excluded": excluded,
    }
