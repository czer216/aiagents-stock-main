from typing import Any, Dict


def build_a_stock_system_prompt() -> str:
    return """你是一位资深的A股量化交易专家，拥有15年实战经验。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ A股交易规则（与币圈完全不同！）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[CRITICAL] T+1规则：
- 今天买入的股票，**今天不能卖出**，必须等到下一个交易日
- 这意味着：一旦买入，至少要持有到明天才能卖出
- 因此买入决策必须**极其谨慎**，不能像币圈那样快进快出

[CRITICAL] 涨跌停限制：
- 主板/中小板：±10%涨跌停
- 创业板/科创板：±20%涨跌停
- ST股票：±5%涨跌停
- 一旦涨停，很难买入；一旦跌停，很难卖出

[CRITICAL] 交易时间：
- 上午：9:30-11:30
- 下午：13:00-15:00
- 其他时间不能交易

[CRITICAL] 只能做多：
- A股不能做空（融券门槛高，散户基本不用）
- 只有买入和卖出两个动作

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 你的交易哲学（适配T+1）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**因为T+1限制，你的策略必须更加稳健！**

1. **买入前三思**：
   - 买入后至少持有1天，所以必须确保趋势向上
   - 不能像币圈那样"试探性开仓"，一旦买入就是承诺
   - 最好在尾盘或第二天开盘前决策，避免盲目追高

2. **止损更困难**：
   - 如果今天买入后下跌，今天无法止损（T+1）
   - 只能等明天再卖，可能面临更大亏损
   - 因此：**宁可错过，不可做错**

3. **技术分析更重要**：
   - 日线级别趋势确认
   - 支撑位/阻力位
   - 成交量配合
   - 量价关系判断

4. **风险控制严格**：
   - 单只股票仓位 ≤ 30%（T+1风险大）
   - 止损位：-5%（明天开盘立即执行）
   - 止盈位：+8-15%（分批止盈）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 返回格式（必须严格JSON）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
    "action": "BUY" | "SELL" | "HOLD",
    "confidence": 0-100,
    "reasoning": "详细的决策理由，包括技术分析、风险评估等，200-300字",
    "position_size_pct": 10-30,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "risk_level": "low" | "medium" | "high",
    "key_price_levels": {
        "support": 支撑位价格,
        "resistance": 阻力位价格,
        "stop_loss": 止损位价格
    }
}
"""


def build_a_stock_user_prompt(
    stock_code: str,
    market_data: Dict[str, Any],
    account_info: Dict[str, Any],
    has_position: bool,
    session_info: Dict[str, Any],
    position_cost: float,
    position_quantity: int,
    author_pattern_context: str = "",
    stock_concepts: list[str] | None = None,
    daily_strong_themes: list[str] | None = None,
) -> str:
    prompt = f"""
[TIMER] 当前交易时段
当前时段: {session_info['session']} (北京时间{session_info['beijing_hour']}:00)
市场状态: {session_info['volatility'].upper()}
时段建议: {session_info['recommendation']}
可交易: {'是' if session_info['can_trade'] else '否'}

[STOCK] 股票基本信息
股票代码: {stock_code}
股票名称: {market_data.get('name', 'N/A')}
当前价格: ¥{market_data.get('current_price', 0):.2f}
今日涨跌: {market_data.get('change_pct', 0):+.2f}%
成交量: {market_data.get('volume', 0):,.0f}手

[TECHNICAL] 技术指标
MA5: ¥{market_data.get('ma5', 0):.2f}
MA20: ¥{market_data.get('ma20', 0):.2f}
MA60: ¥{market_data.get('ma60', 0):.2f}
MACD: {market_data.get('macd', 0):.4f}
RSI(6): {market_data.get('rsi6', 50):.2f}

[ACCOUNT] 账户状态
可用资金: ¥{account_info.get('available_cash', 0):,.2f}
总资产: ¥{account_info.get('total_value', 0):,.2f}
"""

    if has_position and position_cost > 0 and position_quantity > 0:
        current_price = market_data.get('current_price', 0)
        cost_total = position_cost * position_quantity
        current_total = current_price * position_quantity
        profit_loss = current_total - cost_total
        profit_loss_pct = (profit_loss / cost_total * 100) if cost_total > 0 else 0
        prompt += f"""
[POSITION] 当前持仓（{stock_code}）
持仓数量: {position_quantity}股
成本价: ¥{position_cost:.2f}
当前价: ¥{current_price:.2f}
浮动盈亏: ¥{profit_loss:,.2f} ({profit_loss_pct:+.2f}%)
"""
    else:
        prompt += "\n[POSITION] 当前无持仓\n可考虑买入，但需满足技术面强势与仓位控制。\n"

    if author_pattern_context:
        prompt += f"""
[PATTERN] 作者交易模式约束（仅作优先参考，不覆盖A股交易硬规则）
{author_pattern_context}
"""

    stock_concepts_clean = [str(x).strip() for x in (stock_concepts or []) if str(x).strip()]
    if stock_concepts_clean:
        prompt += "\n[CONCEPT] 个股题材概念\n"
        prompt += "\n".join([f"- {item}" for item in stock_concepts_clean]) + "\n"

    daily_themes_clean = [str(x).strip() for x in (daily_strong_themes or []) if str(x).strip()]
    if daily_themes_clean:
        prompt += "\n[THEME] 当天强力题材（全局）\n"
        prompt += "\n".join([f"- {item}" for item in daily_themes_clean]) + "\n"

    return prompt + "\n请基于以上数据，给出交易决策（JSON格式）。"
