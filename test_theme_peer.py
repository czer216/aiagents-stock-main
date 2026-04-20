#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试同题材补涨功能
"""

from longhubang_history import LonghubangHistoryService

def main():
    service = LonghubangHistoryService()

    # 测试几个不同的股票
    test_cases = [
        ("600410", "华胜天成"),
        ("002131", "利欧股份"),
        ("300058", "蓝色光标"),
    ]

    for code, name in test_cases:
        print("=" * 80)
        print(f"测试股票: {name}({code})")
        print("=" * 80)

        result = service.query_theme_peer_stocks(
            stock_code=code,
            min_cooccur_count=1,
            top_n=15,
        )

        if result["success"]:
            print(f"✓ 查询成功")
            print(f"  股票: {result['stock_name']}({result['stock_code']})")
            print(f"  参与题材: {', '.join(result['themes'][:5])}")
            print(f"  找到 {len(result['peer_stocks'])} 只同题材跟涨股票")
            print()

            if result["peer_stocks"]:
                print("前10只同题材跟涨股票（按历史平均涨幅排序）:")
                print(f"{'排名':<6}{'代码':<10}{'名称':<12}{'共现次数':<10}{'平均涨幅':<12}{'题材'}")
                print("-" * 80)

                for i, stock in enumerate(result["peer_stocks"][:10], 1):
                    themes_str = "、".join(stock["themes"][:3])
                    print(
                        f"{i:<6}{stock['code']:<10}{stock['name']:<12}"
                        f"{stock['cooccur_count']:<10}{stock['avg_pct_chg']:>+6.2f}%      "
                        f"{themes_str}"
                    )
        else:
            print(f"✗ 查询失败: {result['error']}")

        print()

if __name__ == "__main__":
    main()
