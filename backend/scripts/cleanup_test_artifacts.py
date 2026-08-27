"""报告测试运行产生的临时产物。

默认只报告明确命名模式命中的测试目录和数据库，不执行批量删除。需要清理时，
使用 ``--file <相对路径>`` 一次删除一个已报告的明确文件；目录始终交由操作者
人工核验和处理。真实库与 ``data/`` 下的持久文件不会命中规则。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _patterns() -> list[tuple[Path, re.Pattern[str]]]:
    """(目录, 匹配测试产物名的正则)。"""
    return [
        (BACKEND_ROOT, re.compile(r"^\.pytest-tmp")),
        (BACKEND_ROOT / "data", re.compile(r"^(security_|module_gaps_|mem_gov_|media_pipeline_)")),
        (
            BACKEND_ROOT / "data",
            re.compile(r"(_api|_integration|_pending|_secret|_auth|_kill|_split)\.db$"),
        ),
        (BACKEND_ROOT, re.compile(r"^coifesp-(narr|media|r7|sb|whisper)-")),
        (BACKEND_ROOT, re.compile(r"^_gen_|^_probe|^_diag|^_refl|^_cons|^_tbl|^_loop")),
    ]


def collect() -> list[Path]:
    hits: list[Path] = []
    for root, pattern in _patterns():
        if not root.exists():
            continue
        for entry in root.iterdir():
            if pattern.match(entry.name):
                hits.append(entry)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="兼容参数；默认即只报告")
    parser.add_argument(
        "--file",
        help="只删除一个明确的、位于 backend 下且符合测试产物规则的文件",
    )
    args = parser.parse_args()
    hits = collect()
    if args.file:
        target = (BACKEND_ROOT / args.file).resolve()
        root = BACKEND_ROOT.resolve()
        if root not in target.parents:
            print("拒绝：目标不在 backend 内。")
            return 2
        if target not in [item.resolve() for item in hits]:
            print("拒绝：目标不符合已知测试产物规则。")
            return 2
        if not target.is_file():
            print("拒绝：只能一次删除一个明确文件；目录需人工逐项处理。")
            return 2
        try:
            target.unlink()
        except OSError as exc:
            print(f"删除失败: {target.relative_to(root)}: {exc}")
            return 1
        print("removed:", target.relative_to(root))
        return 0
    if not hits:
        print("没有发现测试残留。")
        return 0
    for hit in sorted(hits, key=lambda p: str(p)):
        kind = "directory-manual" if hit.is_dir() else "file"
        print(f"[report:{kind}]", hit.relative_to(BACKEND_ROOT))
    print(f"共 {len(hits)} 项；未执行批量删除。文件可用 --file 一次处理一个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
