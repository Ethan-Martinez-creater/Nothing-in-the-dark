from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.reports import diff_reports, redact_sensitive, render_html_report

REPORT_V1 = {
    "title": "案例报告 v1",
    "executive_summary": "第一版摘要",
    "sections": [
        {"title": "观点概况", "content": "主流观点偏向中性。"},
        {"title": "传播分析", "content": "核心节点为 A 账号。"},
    ],
    "citation_links": [{"conclusion": "结论一", "evidence_ids": ["ev-1"]}],
    "disclaimer": "本报告基于当前社交证据。",
}

REPORT_V2 = {
    "title": "案例报告 v2",
    "executive_summary": "第二版摘要",
    "sections": [
        {"title": "观点概况", "content": "主流观点偏向中性。"},
        {"title": "传播分析", "content": "核心节点更新为 B 账号。"},
        {"title": "事实核查", "content": "新增核查结论。"},
    ],
    "citation_links": [
        {"conclusion": "结论一", "evidence_ids": ["ev-1"]},
        {"conclusion": "结论二", "evidence_ids": ["ev-2"]},
    ],
    "disclaimer": "本报告基于当前社交证据。",
}


# ---------- 服务层 ----------

def test_redact_sensitive_masks_phone_email_and_key() -> None:
    text = "联系 13812345678 或 test@example.com，密钥 sk-abcdef1234567890xyz"
    result = redact_sensitive(text)

    assert "13812345678" not in result
    assert "138******78" in result
    assert "test@example.com" not in result
    assert "sk-abcdef1234567890xyz" not in result


def test_render_html_report_escapes_and_includes_sections() -> None:
    rendered = render_html_report(
        {
            "title": '报告 <script>alert(1)</script>',
            "executive_summary": "摘要",
            "sections": [{"title": "章节", "content": "内容"}],
            "citation_links": [],
            "disclaimer": "声明",
        }
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<h1>" in rendered
    assert "章节" in rendered
    assert "声明" in rendered


def test_diff_reports_detects_added_removed_and_changed() -> None:
    diff = diff_reports(REPORT_V2, REPORT_V1)

    assert diff["title_changed"] is True
    assert diff["summary_changed"] is True
    assert diff["sections_added"] == ["事实核查"]
    assert diff["sections_removed"] == []
    assert diff["sections_changed"] == ["传播分析"]
    assert diff["citation_link_count"] == 2


# ---------- 端点 ----------

def _create_case_and_report(client: TestClient, kind: str = "report") -> str:
    case_id = client.post(
        "/api/v1/cases",
        json={"topic": "报告导出", "platforms": ["weibo"]},
    ).json()["id"]
    repo = client.app.state.container.repository

    async def _seed() -> None:
        await repo.create_artifact(
            case_id=case_id,
            kind=kind,
            title="报告",
            data=REPORT_V1,
        )
        await repo.create_artifact(
            case_id=case_id,
            kind=kind,
            title="报告",
            data=REPORT_V2,
        )

    import asyncio

    asyncio.run(_seed())
    items = client.get(f"/api/v1/cases/{case_id}/artifacts").json()
    return case_id, items[0]["id"], items[1]["id"]


def test_download_report_html_redacts_sensitive(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'download.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        _, latest_id, _ = _create_case_and_report(client)
        response = client.get(f"/api/v1/artifacts/{latest_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" in response.headers["content-disposition"]
    assert "<h1>" in response.text


def test_download_rejects_non_report_artifact(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'reject.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id, _, _ = _create_case_and_report(client, kind="fact_check")
        items = client.get(f"/api/v1/cases/{case_id}/artifacts").json()
        response = client.get(f"/api/v1/artifacts/{items[0]['id']}/download")

    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_artifact_kind"


def test_diff_endpoint_compares_two_versions(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'diff.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        _, latest_id, previous_id = _create_case_and_report(client)
        response = client.get(
            f"/api/v1/artifacts/{latest_id}/diff",
            params={"against": previous_id},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sections_added"] == ["事实核查"]
    assert body["sections_changed"] == ["传播分析"]
    assert body["title_changed"] is True


def test_regenerate_creates_new_run(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'regenerate.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        _, latest_id, _ = _create_case_and_report(client)
        response = client.post(f"/api/v1/artifacts/{latest_id}/regenerate")

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "coordinator"
    assert "重新生成" in body["objective"]
