"""Alignment blocking benchmark (ALIGN-P0-02).

用固定 seed 合成数据测量 blocking 分桶后的候选比较对数，证明不再是
O(n^2) 全连接。这是离线基准，不依赖真实社交数据。
"""

from __future__ import annotations

import time
from collections import defaultdict


def build_synthetic_assets(n: int, seed: int = 42) -> list[dict]:
    """合成媒体资产：分散的 sha256 / phash 前缀。"""
    assets = []
    for i in range(n):
        # 分组：让一部分共享 phash 前缀（模拟跨平台变体）。
        group = i % 100
        assets.append(
            {
                "id": f"a{i}",
                "media_type": "image" if i % 2 == 0 else "video",
                "sha256": None if i % 3 else f"sha-{group}",
                "phash": f"{group:04x}ffff",
            }
        )
    return assets


def blocking_comparison_count(assets: list[dict]) -> int:
    """按 sha256 桶 + phash 前缀桶分桶，统计桶内比较对数。"""
    by_sha: dict[str, list[dict]] = defaultdict(list)
    by_phash: dict[str, list[dict]] = defaultdict(list)
    for asset in assets:
        if asset["sha256"]:
            by_sha[asset["sha256"]].append(asset)
        else:
            prefix = (asset["phash"] or "")[:4]
            by_phash[f"{asset['media_type']}:{prefix}"].append(asset)

    pairs = 0
    for group in list(by_sha.values()) + list(by_phash.values()):
        size = len(group)
        pairs += size * (size - 1) // 2
    return pairs


def main() -> None:
    n = 10_000
    assets = build_synthetic_assets(n)
    full_pairs = n * (n - 1) // 2

    start = time.perf_counter()
    blocked = blocking_comparison_count(assets)
    elapsed = time.perf_counter() - start

    print(f"assets = {n}")
    print(f"full O(n^2) pairs = {full_pairs}")
    print(f"blocked pairs      = {blocked}")
    print(f"reduction ratio    = {full_pairs / max(blocked, 1):.0f}x")
    print(f"blocking time      = {elapsed:.4f}s")
    assert blocked < full_pairs // 100, "blocking did not reduce comparisons"


if __name__ == "__main__":
    main()
