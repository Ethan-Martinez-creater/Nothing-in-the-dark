"""Sandbox worker entrypoint (15).

子进程入口：`python -m app.harness.sandbox_worker --tool <name> --payload <json>`。
父进程通过参数数组启动（不经 shell）；结果以单行 JSON 写 stdout；stdout
有大小上限由执行器在父进程侧强制。工作目录是执行器准备的独立临时目录，
环境变量由执行器白名单注入。
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="coifesp sandbox worker")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--payload", default="{}")
    parser.add_argument("--payload-file", default=None)
    args = parser.parse_args()

    payload: dict[str, object] = {}
    try:
        if args.payload_file:
            with open(args.payload_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(args.payload)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": "sandbox_payload_invalid", "message": str(exc)},
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1

    from app.harness.external_tools import run_external

    result = run_external(args.tool, dict(payload))
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
