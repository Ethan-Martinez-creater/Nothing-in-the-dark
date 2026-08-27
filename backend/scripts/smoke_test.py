from __future__ import annotations

import sys
import time

import httpx


def main() -> int:
    base_url = "http://127.0.0.1:8000/api/v1"
    with httpx.Client(base_url=base_url, timeout=10) as client:
        health = client.get("/health")
        health.raise_for_status()

        capabilities = client.get("/system/capabilities")
        capabilities.raise_for_status()
        caps = capabilities.json()
        if caps.get("production_entry") != "messages":
            print(
                f"Unexpected production_entry={caps.get('production_entry')}",
                file=sys.stderr,
            )
            return 1

        case_response = client.post(
            "/cases",
            json={
                "topic": "Runtime smoke test",
                "description": "End-to-end verification created by scripts/smoke_test.py",
                "platforms": ["weibo", "bilibili"],
            },
        )
        case_response.raise_for_status()
        case = case_response.json()

        run_response = client.post(
            f"/cases/{case['id']}/messages",
            json={"content": "请对当前案例做一次快速分析。", "approve_crawl": True},
        )
        run_response.raise_for_status()
        run = run_response.json()
        if "agent" not in run:
            print("messages entry did not return an Agent Run", file=sys.stderr)
            return 1

        current = run
        for _ in range(80):
            current_response = client.get(f"/runs/{run['id']}")
            current_response.raise_for_status()
            current = current_response.json()
            if current["status"] in {
                "completed",
                "failed",
                "cancelled",
                "waiting_approval",
            }:
                break
            time.sleep(0.1)
        else:
            print("Smoke test timed out", file=sys.stderr)
            return 1

        # Entry smoke: the run must exist and be a coordinator Agent Run.
        # Without an LLM the run ends as failed/llm_not_configured — that
        # still proves we did not fall back to the legacy analysis graph.
        if current.get("agent") != "coordinator":
            print(
                f"Unexpected agent={current.get('agent')} status={current['status']}",
                file=sys.stderr,
            )
            return 1

        print(
            f"OK case={case['id']} run={run['id']} "
            f"status={current['status']} error={current.get('error_code')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
