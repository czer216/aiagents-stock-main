#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试共现群组功能
"""

from modules.longhubang.longhubang_history import LonghubangHistoryService

def test_cooccur_groups():
    """测试共现群组生成和查询"""
    service = LonghubangHistoryService()

    print("=" * 60)
    print("测试1: 回填历史共现群组（最近10天）")
    print("=" * 60)
    result = service.backfill_cooccur_groups(days=10)
    print(f"回填结果: {result}")
    print()

    print("=" * 60)
    print("测试2: 查询共现历史")
    print("=" * 60)
    # 模拟候选股票
    candidate_stocks = [
        {"code": "000001", "name": "平安银行"},
        {"code": "600036", "name": "招商银行"},
    ]
    active_themes = ["人工智能", "算力"]

    cooccur_map = service._query_cooccur_history(
        candidate_stocks=candidate_stocks,
        active_themes=active_themes,
        before_date="20260419",
    )

    print(f"查询到 {len(cooccur_map)} 个股票的共现历史:")
    for code, data in cooccur_map.items():
        print(f"  {code}: 共现{data['frequency']}次, 胜率{data['win_rate']:.1%}, 平均收益{data['avg_return']:+.2f}%")
    print()

    print("=" * 60)
    print("测试3: 数据库验证")
    print("=" * 60)
    conn = service._get_connection()

    # 检查群组数量
    group_count = conn.execute("SELECT COUNT(*) FROM longhubang_cooccur_group_2026").fetchone()[0]
    print(f"群组总数: {group_count}")

    # 检查成员数量
    member_count = conn.execute("SELECT COUNT(*) FROM longhubang_cooccur_member_2026").fetchone()[0]
    print(f"成员总数: {member_count}")

    # 查看最近的群组
    recent_groups = conn.execute("""
        SELECT group_id, trade_date, theme, member_count, group_strength
        FROM longhubang_cooccur_group_2026
        ORDER BY trade_date DESC
        LIMIT 5
    """).fetchall()

    print("\n最近的5个群组:")
    for row in recent_groups:
        print(f"  {row[0]}: {row[1]} | {row[2]} | 成员{row[3]}只 | 强度{row[4]:.2f}")

    conn.close()
    print()
    print("测试完成!")

if __name__ == "__main__":
    test_cooccur_groups()
