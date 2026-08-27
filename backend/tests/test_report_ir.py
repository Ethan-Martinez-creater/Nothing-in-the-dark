"""P0-1.1g/h: formal Report IR with per-conclusion evidence IDs."""

from __future__ import annotations

from app.schemas.reports import ReportIR
from app.services.analysis import analyze_opinion, build_report, verify_claims
from app.services.propagation_algorithm import build_propagation_graph
from app.services.reports import render_html_report


async def test_build_report_emits_report_ir_schema() -> None:
    posts = [
        {
            "id": "post-1",
            "platform": "weibo",
            "author": "记者",
            "content": "官方回应称事故伤亡数据失实，正在调查",
            "published_at": "2026-08-01T09:00:00+00:00",
            "is_demo": False,
        }
    ]
    opinion = analyze_opinion(posts)
    propagation = build_propagation_graph(posts)
    fact_check = await verify_claims(posts, "事故调查")
    report = build_report("事故调查", opinion, propagation, fact_check)
    parsed = ReportIR.model_validate(report)
    assert parsed.schema_version == "1.0.0"
    assert parsed.sections
    assert all(section.evidence_ids for section in parsed.sections)
    assert parsed.citation_links
    assert all(link.evidence_ids for link in parsed.citation_links)


async def test_report_citations_are_structured_and_renderable() -> None:
    posts = [
        {
            "id": "post-1",
            "platform": "weibo",
            "author": "记者",
            "content": "官方回应称事故伤亡数据失实，正在调查",
            "published_at": "2026-08-01T09:00:00+00:00",
        }
    ]
    report = build_report(
        "事故调查",
        analyze_opinion(posts),
        build_propagation_graph(posts),
        await verify_claims(posts, "事故调查"),
    )
    html = render_html_report(report)
    assert "Evidence:" in html
    first = report["citation_links"][0]
    assert isinstance(first, dict)
    assert first["evidence_ids"]
