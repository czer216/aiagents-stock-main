from typing import Any, Dict


def build_final_decision_prompt(
    stock_info: Dict[str, Any],
    comprehensive_discussion: str,
    indicators: Dict[str, Any],
    anchor_text: str,
) -> str:
    return f"""
基于前期的综合分析讨论，现在需要做出最终的投资决策。

股票信息：
- 股票代码：{stock_info.get('symbol', 'N/A')}
- 股票名称：{stock_info.get('name', 'N/A')}
- 当前价格：{stock_info.get('current_price', 'N/A')}

综合分析讨论结果：
{comprehensive_discussion}

当前关键技术位：
- MA20：{indicators.get('ma20', 'N/A')}
- 布林带上轨：{indicators.get('bb_upper', 'N/A')}
- 布林带下轨：{indicators.get('bb_lower', 'N/A')}

价格锚点（必须优先使用）：
{anchor_text}

请给出最终投资决策，必须包含以下内容：
1. 投资评级：买入/持有/卖出
2. 目标价位（具体数字）
3. 操作建议（具体的买入/卖出策略）
4. 进场位置（具体价位区间）
5. 止盈位置（具体价位）
6. 止损位置（具体价位）
7. 持有周期建议
8. 风险提示
9. 仓位建议（轻仓/中等仓位/重仓）

硬性约束（必须遵守）：
1) `entry_range` 必须是纯数字区间格式：`xx.xx-yy.yy`。
2) `entry_range` 的中位数必须围绕“当前价格锚点”，偏离不得超过 10%。
3) 若有7日高低点锚点：`entry_range` 必须落在 `[近7日最低价*0.97, 近7日最高价*1.03]` 范围内。
4) `take_profit`、`stop_loss` 必须可解析，且满足：`stop_loss < entry_range下沿 < entry_range上沿 < take_profit`。
5) 若锚点缺失或冲突，先使用当前价格与MA20做兜底。

请以JSON格式输出决策结果。
"""
