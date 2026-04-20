#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化历史数据：同步快照 + 回填共现群组
"""

from longhubang_history import LonghubangHistoryService

def main():
    service = LonghubangHistoryService()

    print("=" * 60)
    print("步骤1: 同步历史快照数据（最近10天）")
    print("=" * 60)

    # 同步最近10天的历史数据
    sync_result = service.sync_2026_history_until(
        end_date="20260420",
        max_days_per_run=10,
        max_workers=5,
    )

    print(f"同步结果: {sync_result}")
    print()

    print("=" * 60)
    print("步骤2: 回填共现群组（最近10天）")
    print("=" * 60)

    # 回填共现群组
    backfill_result = service.backfill_cooccur_groups(days=10)

    print(f"回填结果: {backfill_result}")
    print()

    print("=" * 60)
    print("步骤3: 验证数据")
    print("=" * 60)

    conn = service._get_connection()

    # 检查快照数量
    snapshot_count = conn.execute("SELECT COUNT(*) FROM longhubang_daily_snapshot_2026").fetchone()[0]
    print(f"快照总数: {snapshot_count}")

    # 检查龙虎榜数量
    lhb_count = conn.execute("SELECT COUNT(*) FROM longhubang_daily_snapshot_2026 WHERE is_lhb = 1").fetchone()[0]
    print(f"龙虎榜记录: {lhb_count}")

    # 检查群组数量
    group_count = conn.execute("SELECT COUNT(*) FROM longhubang_cooccur_group_2026").fetchone()[0]
    print(f"共现群组: {group_count}")

    # 检查成员数量
    member_count = conn.execute("SELECT COUNT(*) FROM longhubang_cooccur_member_2026").fetchone()[0]
    print(f"群组成员: {member_count}")

    # 查看最近的群组
    if group_count > 0:
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
    print("初始化完成!")

if __name__ == "__main__":
    main()
