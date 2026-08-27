from __future__ import annotations

import argparse
import json

import httpx
from smoke_llm_runtime import event_summary, wait_for_run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        response = client.post(
            f"/cases/{args.case_id}/messages",
            json={
                "content": (
                    "复测中文检索修复：调用 search_social_evidence 搜索"
                    "“杭州 主动召回”，禁止调用其他工具。请列出命中的 Evidence ID，"
                    "并严格依据证据回答地点及是否存在主动召回材料。"
                ),
                "approve_crawl": False,
            },
        )
        response.raise_for_status()
        run = wait_for_run(client, response.json()["id"], timeout_seconds=120)
        turns = client.get(f"/cases/{args.case_id}/turns")
        turns.raise_for_status()
        assistants = [
            turn["content"]
            for turn in turns.json()
            if turn["role"] == "assistant"
        ]
        print(
            json.dumps(
                {
                    "run": run,
                    "events": event_summary(client, run["id"]),
                    "answer": assistants[-1] if assistants else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
