#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试题材均匀化功能
"""

from longhubang_history import LonghubangHistoryService

def test_theme_diversification():
    """测试题材均匀化算法"""
    service = LonghubangHistoryService()

    # 模拟候选数据：3个题材，评分不均
    candidates = [
        {"code": "000001", "name": "AI股票1", "theme_tokens": "人工智能、算力", "peer_rebound_score": 95},
        {"code": "000002", "name": "AI股票2", "theme_tokens": "人工智能、芯片", "peer_rebound_score": 92},
        {"code": "000003", "name": "AI股票3", "theme_tokens": "人工智能", "peer_rebound_score": 90},
        {"code": "000004", "name": "AI股票4", "theme_tokens": "人工智能、大模型", "peer_rebound_score": 88},
        {"code": "000005", "name": "AI股票5", "theme_tokens": "人工智能", "peer_rebound_score": 85},
        {"code": "600001", "name": "新能源1", "theme_tokens": "新能源、光伏", "peer_rebound_score": 93},
        {"code": "600002", "name": "新能源2", "theme_tokens": "新能源、储能", "peer_rebound_score": 87},
        {"code": "600003", "name": "新能源3", "theme_tokens": "新能源", "peer_rebound_score": 82},
        {"code": "300001", "name": "医药1", "theme_tokens": "医药、创新药", "peer_rebound_score": 91},
        {"code": "300002", "name": "医药2", "theme_tokens": "医药、生物制药", "peer_rebound_score": 86},
        {"code": "300003", "name": "医药3", "theme_tokens": "医药", "peer_rebound_score": 80},
    ]

    print("=" * 60)
    print("原始候选（按评分排序）：")
    print("=" * 60)
    for i, stock in enumerate(candidates[:6], 1):
        theme = stock["theme_tokens"].split("、")[0]
        print(f"{i}. {stock['name']} ({stock['code']}) - {theme} - {stock['peer_rebound_score']}分")
    print()

    print("=" * 60)
    print("测试1: 题材均匀化（top_n=6, max_ratio=0.5）")
    print("=" * 60)
    result = service._diversify_candidates_by_theme(
        candidates=candidates,
        top_n=6,
        max_per_theme_ratio=0.5,
    )

    theme_count = {}
    for i, stock in enumerate(result, 1):
        theme = stock["theme_tokens"].split("、")[0]
        theme_count[theme] = theme_count.get(theme, 0) + 1
        print(f"{i}. {stock['name']} ({stock['code']}) - {theme} - {stock['peer_rebound_score']}分")

    print(f"\n题材分布: {theme_count}")
    print(f"预期: 每个题材最多3只（50%），实际符合: {all(v <= 3 for v in theme_count.values())}")
    print()

    print("=" * 60)
    print("测试2: 题材均匀化（top_n=9, max_ratio=0.33）")
    print("=" * 60)
    result2 = service._diversify_candidates_by_theme(
        candidates=candidates,
        top_n=9,
        max_per_theme_ratio=0.33,
    )

    theme_count2 = {}
    for i, stock in enumerate(result2, 1):
        theme = stock["theme_tokens"].split("、")[0]
        theme_count2[theme] = theme_count2.get(theme, 0) + 1
        print(f"{i}. {stock['name']} ({stock['code']}) - {theme} - {stock['peer_rebound_score']}分")

    print(f"\n题材分布: {theme_count2}")
    print(f"预期: 每个题材最多3只（33%），实际符合: {all(v <= 3 for v in theme_count2.values())}")
    print()

    print("=" * 60)
    print("测试3: 不启用均匀化（直接取前6）")
    print("=" * 60)
    result3 = candidates[:6]

    theme_count3 = {}
    for i, stock in enumerate(result3, 1):
        theme = stock["theme_tokens"].split("、")[0]
        theme_count3[theme] = theme_count3.get(theme, 0) + 1
        print(f"{i}. {stock['name']} ({stock['code']}) - {theme} - {stock['peer_rebound_score']}分")

    print(f"\n题材分布: {theme_count3}")
    print(f"可以看到AI题材占据了{theme_count3.get('人工智能', 0)}/6 = {theme_count3.get('人工智能', 0)/6*100:.0f}%")
    print()

    print("测试完成!")

if __name__ == "__main__":
    test_theme_diversification()
