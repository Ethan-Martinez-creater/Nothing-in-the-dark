from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_memory_document_and_case_scoped_retrieval(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case = client.post(
            "/api/v1/cases",
            json={"topic": "新能源汽车争议", "platforms": ["weibo"]},
        ).json()
        case_id = case["id"]

        memory = client.post(
            f"/api/v1/cases/{case_id}/memories",
            json={
                "kind": "correction",
                "content": "用户确认事件发生地是杭州，不是上海。",
                "source_type": "user_correction",
                "source_id": "turn-1",
                "importance": 1,
            },
        )
        document = client.post(
            f"/api/v1/cases/{case_id}/documents",
            files={
                "file": (
                    "briefing.md",
                    "官方材料显示该批次车辆已启动主动召回。",
                    "text/markdown",
                )
            },
        )
        memory_hits = client.post(
            f"/api/v1/cases/{case_id}/memory/search",
            json={"query": "杭州", "limit": 5},
        )
        evidence_hits = client.post(
            f"/api/v1/cases/{case_id}/evidence/search",
            json={"query": "官方材料 主动召回", "limit": 5},
        )

    assert memory.status_code == 201
    assert memory.json()["source_type"] == "user_correction"
    assert document.status_code == 201
    assert document.json()["status"] == "ready"
    assert memory_hits.status_code == 200
    assert memory_hits.json()[0]["evidence_id"].startswith("memory:")
    assert evidence_hits.status_code == 200
    assert evidence_hits.json()[0]["evidence_id"].startswith("document_chunk:")
