from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def wait_for_run(
    client: httpx.Client,
    run_id: str,
    *,
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run["status"] in TERMINAL_STATUSES:
            return run
        time.sleep(0.5)
    raise TimeoutError(f"Run {run_id} did not finish within {timeout_seconds}s")


def event_summary(client: httpx.Client, run_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/runs/{run_id}/events")
    response.raise_for_status()
    return [
        {
            "event_type": event["event_type"],
            "tool": event.get("tool"),
            "status": event["status"],
            "usage": event["payload"].get("usage"),
        }
        for event in response.json()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        case_response = client.post(
            "/cases",
            json={
                "topic": "杭州新能源汽车主动召回测试案例",
                "description": "用于验证 Harness Agent 的受控真实模型调用。",
                "platforms": ["weibo"],
            },
        )
        case_response.raise_for_status()
        case = case_response.json()
        case_id = case["id"]

        memory_response = client.post(
            f"/cases/{case_id}/memories",
            json={
                "scope": "case",
                "kind": "correction",
                "content": "用户确认测试事件发生地是杭州，不是上海。",
                "source_type": "user_correction",
                "source_id": "smoke-test-user-correction",
                "importance": 1,
                "confidence": 1,
            },
        )
        memory_response.raise_for_status()

        document_response = client.post(
            f"/cases/{case_id}/documents",
            files={
                "file": (
                    "official-briefing.md",
                    (
                        "官方测试材料：2026年7月，示例公司宣布对杭州地区"
                        "某批次新能源汽车启动主动召回。本材料仅用于系统测试。"
                    ),
                    "text/markdown",
                )
            },
        )
        document_response.raise_for_status()

        first_response = client.post(
            f"/cases/{case_id}/messages",
            json={
                "content": (
                    "这是受控测试。请先加载 case-follow-up Skill，再调用 "
                    "search_social_evidence 搜索“杭州 主动召回”。禁止调用采集工具，"
                    "禁止写入新记忆。请只根据工具返回的 Evidence ID 回答事件地点和"
                    "是否存在主动召回材料；证据不足时必须明确说明。"
                ),
                "approve_crawl": False,
            },
        )
        first_response.raise_for_status()
        first_run = wait_for_run(client, first_response.json()["id"])

        second_response = client.post(
            f"/cases/{case_id}/messages",
            json={
                "content": (
                    "基于上一轮案例上下文，再次确认事件地点。不要调用采集工具，"
                    "并引用已有 Evidence ID。"
                ),
                "approve_crawl": False,
            },
        )
        second_response.raise_for_status()
        second_run = wait_for_run(client, second_response.json()["id"])

        turns_response = client.get(f"/cases/{case_id}/turns")
        turns_response.raise_for_status()
        output = {
            "case_id": case_id,
            "first_run": first_run,
            "first_events": event_summary(client, first_run["id"]),
            "second_run": second_run,
            "second_events": event_summary(client, second_run["id"]),
            "turns": [
                {"role": turn["role"], "content": turn["content"]}
                for turn in turns_response.json()
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
