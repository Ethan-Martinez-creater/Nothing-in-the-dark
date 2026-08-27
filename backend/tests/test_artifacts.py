from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _seed_artifacts(client: TestClient, case_id: str) -> list[str]:
    repo = client.app.state.container.repository

    async def _seed() -> None:
        await repo.create_artifact(
            case_id=case_id,
            kind="report",
            title="报告 v1",
            data={"title": "v1"},
        )
        await repo.create_artifact(
            case_id=case_id,
            kind="report",
            title="报告 v2",
            data={"title": "v2"},
        )

    import asyncio

    asyncio.run(_seed())
    return [item["id"] for item in client.get(
        f"/api/v1/cases/{case_id}/artifacts"
    ).json()]


def test_get_artifact_detail(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "Artifact 详情", "platforms": ["weibo"]},
        ).json()["id"]
        ids = _seed_artifacts(client, case_id)
        latest = client.get(f"/api/v1/artifacts/{ids[0]}")
        missing = client.get("/api/v1/artifacts/does-not-exist")

    assert latest.status_code == 200
    assert latest.json()["title"] == "报告 v2"
    assert latest.json()["version"] == 2
    assert latest.json()["kind"] == "report"
    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"


def test_list_artifact_versions(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "Artifact 版本", "platforms": ["weibo"]},
        ).json()["id"]
        ids = _seed_artifacts(client, case_id)
        versions = client.get(f"/api/v1/artifacts/{ids[1]}/versions")

    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1, 2]
    assert [item["title"] for item in versions.json()] == ["报告 v1", "报告 v2"]


def test_artifact_versions_are_scoped_to_case_and_kind(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "Artifact 作用域", "platforms": ["weibo"]},
        ).json()["id"]
        repo = client.app.state.container.repository

        async def _seed() -> None:
            # 不同 kind 与不同案例的 Artifact 不应进入同一版本族
            await repo.create_artifact(
                case_id=case_id,
                kind="report",
                title="报告 v1",
                data={},
            )
            await repo.create_artifact(
                case_id=case_id,
                kind="fact_check",
                title="核查卡",
                data={},
            )

        import asyncio

        asyncio.run(_seed())
        items = client.get(f"/api/v1/cases/{case_id}/artifacts").json()
        report_id = next(item["id"] for item in items if item["kind"] == "report")
        versions = client.get(f"/api/v1/artifacts/{report_id}/versions")

    assert versions.status_code == 200
    assert [item["kind"] for item in versions.json()] == ["report"]
