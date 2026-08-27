"""M2: artifact follow-up — message with artifact ref and context injection.

``CreateMessageRequest.artifact_id`` 经 ``AgentRunService.start`` 进入 run
metadata（``artifact_ref``），GraphWorker 把目标 Artifact 数据注入 system
context（跨 case 引用忽略）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.bootstrap import ApplicationContainer
from app.core.config import Settings
from app.main import create_app
from app.schemas.cases import CreateCaseRequest


async def _container(tmp_path: Path, name: str) -> ApplicationContainer:
    container = ApplicationContainer(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / name}",
            demo_mode=True,
        )
    )
    await container.database.create_schema()
    return container


async def _case_and_artifact(
    container: ApplicationContainer,
) -> tuple[str, str]:
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="追问测试", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id,
        turn_id=None,
        objective="产出目标",
        metadata={},
    )
    artifact = await repository.create_artifact(
        case_id=case.id,
        run_id=run.id,
        kind="opinion_analysis",
        title="观点分析",
        data={"clusters": [{"theme": "主题A", "ratio": 0.6}]},
    )
    return case.id, artifact.id


async def test_start_records_artifact_ref_in_metadata(tmp_path: Path) -> None:
    container = await _container(tmp_path, "followup.db")
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="追问测试", platforms=["weibo"])
    )
    run = await container.agent_service.start(
        case_id=case.id,
        content="请解释这个 Artifact",
        approve_crawl=False,
        artifact_id="artifact-1",
    )
    assert run.metadata_json["artifact_ref"] == {"artifact_id": "artifact-1"}
    # 不带 artifact_id 时 metadata 保持原样。
    plain = await container.agent_service.start(
        case_id=case.id,
        content="普通追问",
        approve_crawl=False,
    )
    assert "artifact_ref" not in plain.metadata_json


async def test_followup_context_injects_artifact_data(tmp_path: Path) -> None:
    container = await _container(tmp_path, "followup_inject.db")
    case_id, artifact_id = await _case_and_artifact(container)
    context = await container.worker._artifact_followup_context(  # noqa: SLF001
        {"artifact_ref": {"artifact_id": artifact_id}},
        case_id,
    )
    assert "追问目标 Artifact（opinion_analysis v1）" in context
    assert "主题A" in context


async def test_followup_context_ignores_other_case_artifact(
    tmp_path: Path,
) -> None:
    container = await _container(tmp_path, "followup_other.db")
    repository = container.repository
    other = await repository.create_case(
        CreateCaseRequest(topic="另一个案例", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=other.id, turn_id=None, objective="目标", metadata={}
    )
    artifact = await repository.create_artifact(
        case_id=other.id,
        run_id=run.id,
        kind="opinion_analysis",
        title="别的产出",
        data={"clusters": [{"theme": "机密"}]},
    )
    context = await container.worker._artifact_followup_context(  # noqa: SLF001
        {"artifact_ref": {"artifact_id": artifact.id}},
        "case-of-this-run",
    )
    assert context == ""
    # 引用不存在的 artifact 同样返回空。
    assert await container.worker._artifact_followup_context(  # noqa: SLF001
        {"artifact_ref": {"artifact_id": "missing"}},
        other.id,
    ) == ""


async def test_followup_context_truncates_huge_artifacts(tmp_path: Path) -> None:
    container = await _container(tmp_path, "followup_big.db")
    repository = container.repository
    case = await repository.create_case(
        CreateCaseRequest(topic="追问测试", platforms=["weibo"])
    )
    run = await repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="目标", metadata={}
    )
    artifact = await repository.create_artifact(
        case_id=case.id,
        run_id=run.id,
        kind="fact_check",
        title="超长核查",
        data={"cards": [{"content": "长" * 5_000}]},
    )
    context = await container.worker._artifact_followup_context(  # noqa: SLF001
        {"artifact_ref": {"artifact_id": artifact.id}},
        case.id,
    )
    assert "（已截断）" in context
    assert len(context) < 4_200


def test_api_message_with_artifact_ref(tmp_path: Path) -> None:
    db_path = tmp_path / "followup_api.db"
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{db_path}", demo_mode=True)
    )
    with TestClient(app) as client:
        case_id = client.post(
            "/api/v1/cases",
            json={"topic": "追问", "platforms": ["weibo"]},
        ).json()["id"]
        response = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "解释这个结论", "artifact_id": "artifact-1"},
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["metadata_json"]["artifact_ref"] == {
            "artifact_id": "artifact-1"
        }
