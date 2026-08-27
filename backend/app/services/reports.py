"""Report Artifact 导出与比较服务。

- HTML 渲染：把 Report Agent 产出的结构化 JSON（title / executive_summary /
  sections / citation_links / disclaimer）渲染为可下载的独立 HTML；
- 敏感信息检查：导出前对文本打码（手机号、身份证、邮箱、API Key）；
- 版本差异比较：同一版本族内两份报告的章节级结构化 diff。
"""

from __future__ import annotations

import html
import re
from typing import Any

# ---------- 敏感信息打码 ----------

_PHONE = re.compile(r"1[3-9]\d{9}")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_SENSITIVE_PATTERNS = (_PHONE, _ID_CARD, _EMAIL, _API_KEY)


def _mask(value: str, keep_head: int = 3, keep_tail: int = 2) -> str:
    if len(value) <= keep_head + keep_tail:
        return "*" * len(value)
    return value[:keep_head] + "*" * (len(value) - keep_head - keep_tail) + value[-keep_tail:]


def redact_sensitive(text: str) -> str:
    """对手机号、身份证、邮箱和 API Key 做中间打码（保留首尾便于人工核对）。"""
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(lambda m: _mask(m.group()), text)
    return text


# ---------- HTML 渲染 ----------

def render_html_report(data: dict[str, Any]) -> str:
    """把报告 Artifact 数据渲染为独立 HTML（先打码再转义，防注入）。"""
    title = html.escape(redact_sensitive(str(data.get("title") or "舆情分析报告")))
    summary = html.escape(
        redact_sensitive(str(data.get("executive_summary") or ""))
    )
    disclaimer = html.escape(redact_sensitive(str(data.get("disclaimer") or "")))

    sections_html: list[str] = []
    for section in data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_title = html.escape(
            redact_sensitive(str(section.get("title") or ""))
        )
        content = html.escape(
            redact_sensitive(str(section.get("content") or ""))
        )
        sections_html.append(
            f'<section class="section"><h2>{section_title}</h2>'
            f'<p>{content}</p></section>'
        )

    citations_html: list[str] = []
    for link in data.get("citation_links") or []:
        if isinstance(link, str):
            conclusion = html.escape(redact_sensitive(link))
            ids = html.escape(link)
        elif isinstance(link, dict):
            conclusion = html.escape(
                redact_sensitive(str(link.get("conclusion") or ""))
            )
            evidence_ids = link.get("evidence_ids") or []
            ids = ", ".join(html.escape(str(item)) for item in evidence_ids)
        else:
            continue
        citations_html.append(
            f'<li><strong>{conclusion}</strong>'
            f'<span class="evidence">Evidence: {ids}</span></li>'
        )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        "<style>"
        "body{font-family:'Microsoft YaHei',sans-serif;max-width:860px;"
        "margin:32px auto;padding:0 20px;color:#222;line-height:1.7}"
        "h1{border-bottom:2px solid #2f6fb3;padding-bottom:8px}"
        "h2{color:#2f6fb3;margin-top:28px}"
        ".summary{background:#f4f7fb;border-left:4px solid #2f6fb3;"
        "padding:12px 16px;border-radius:4px}"
        ".evidence{display:block;font-size:13px;color:#666;margin-top:4px}"
        ".disclaimer{margin-top:36px;padding:10px;border-top:1px solid #ddd;"
        "font-size:12px;color:#888}"
        "</style>\n</head>\n<body>\n"
        f"<h1>{title}</h1>\n"
        f'<section class="summary"><h2>执行摘要</h2><p>{summary}</p></section>\n'
        f'<section class="sections">{chr(10).join(sections_html)}</section>\n'
        f'<section class="citations"><h2>引用链接</h2><ul>'
        f"{chr(10).join(citations_html)}</ul></section>\n"
        f'<p class="disclaimer">{disclaimer}</p>\n'
        "</body>\n</html>\n"
    )


# ---------- 版本差异比较 ----------

def diff_reports(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """两份报告数据的章节级结构化 diff（按章节标题匹配）。"""
    def section_map(data: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for section in data.get("sections") or []:
            if isinstance(section, dict) and section.get("title") is not None:
                result[str(section["title"])] = str(section.get("content") or "")
        return result

    cur = section_map(current)
    prev = section_map(previous)
    added = sorted(title for title in cur if title not in prev)
    removed = sorted(title for title in prev if title not in cur)
    changed = sorted(
        title for title in cur if title in prev and prev[title] != cur[title]
    )
    return {
        "title_changed": current.get("title") != previous.get("title"),
        "summary_changed": (
            current.get("executive_summary")
            != previous.get("executive_summary")
        ),
        "sections_added": added,
        "sections_removed": removed,
        "sections_changed": changed,
        "citation_link_count": len(current.get("citation_links") or []),
    }
