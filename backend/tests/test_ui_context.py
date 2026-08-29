"""M2.2：结构化 UiContext——Run metadata 持久化 + ContextBuilder 独立 block。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.application.context_builder import ContextBuilder
from app.core.config import Settings
from app.main import create_app

UI_CONTEXT = {
    "workspace": "network",
    "selected_type": "propagation_edge",
    "selected_id": "edge-123",
    "selected_label": "A → B",
    "filters": {"relation": "inferred", "min_confidence": 0.6},
    "time_range": None,
}


def test_message_ui_context_persisted_in_run_metadata(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ui_ctx.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "UI 上下文案例", "platforms": ["weibo"]},
        )
        assert created.status_code == 201
        case_id = created.json()["id"]

        started = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "为什么这条边被判断为 inferred？", "ui_context": UI_CONTEXT},
        )
        assert started.status_code == 202, started.text
        # route 侧 exclude_none：显式 None 字段（time_range）不进入 metadata。
        expected = {k: v for k, v in UI_CONTEXT.items() if v is not None}
        assert started.json()["metadata_json"]["ui_context"] == expected

        # 旧 client 不传 ui_context 仍合法：metadata 中不出现 ui_context。
        legacy = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "不带界面上下文的消息"},
        )
        assert legacy.status_code == 202
        assert "ui_context" not in legacy.json()["metadata_json"]


def test_message_ui_context_oversized_rejected(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ui_ctx_big.db'}",
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/cases",
            json={"topic": "超大上下文案例", "platforms": ["weibo"]},
        )
        case_id = created.json()["id"]
        big = {
            "workspace": "evidence",
            "filters": {"blob": "x" * (17 * 1024)},
        }
        started = client.post(
            f"/api/v1/cases/{case_id}/messages",
            json={"content": "携带超界 ui_context", "ui_context": big},
        )
        assert started.status_code == 400
        assert started.json()["code"] == "ui_context_too_large"


def test_ui_context_block_contains_warning_and_selection() -> None:
    run = SimpleNamespace(metadata_json={"ui_context": UI_CONTEXT})
    block = ContextBuilder._ui_context_block(run)
    assert "不构成事实证据" in block
    assert "工作区：network" in block
    assert "propagation_edge / edge-123" in block
    assert "A → B" in block
    assert '"relation": "inferred"' in block
    assert "必须调用允许的工具查询" in block


def test_ui_context_block_empty_without_metadata() -> None:
    assert ContextBuilder._ui_context_block(SimpleNamespace(metadata_json={})) == ""
    assert ContextBuilder._ui_context_block(SimpleNamespace(metadata_json=None)) == ""
    assert ContextBuilder._ui_context_block(SimpleNamespace(metadata_json={"ui_context": {}})) == ""


def test_ui_context_block_does_not_resolve_objects() -> None:
    """selected_id 仅作为导航文本；不得触发任何按 ID 的对象加载。"""
    run = SimpleNamespace(
        metadata_json={
            "ui_context": {
                "workspace": "evidence",
                "selected_type": "evidence",
                "selected_id": "ev-from-other-case",
            }
        }
    )
    block = ContextBuilder._ui_context_block(run)
    # 纯字符串格式化：ID 以文本出现，但绝不包含任何事实内容字段。
    assert "ev-from-other-case" in block
    assert json.dumps(UI_CONTEXT["filters"], ensure_ascii=False) not in json.dumps(block)
