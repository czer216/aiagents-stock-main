from typing import Any, Dict, List


def build_youzi_behavior_prompt(summary: Dict[str, Any], youzi_info: str, youzi_signal_context: str, longhubang_data: str) -> str:
    return f"""
你是一名资深的游资研究专家，拥有10年以上的龙虎榜数据分析经验，深谙各路游资的操作风格和盈利模式。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个
总买入金额: {summary.get('total_buy_amount', 0):,.2f} 元
总卖出金额: {summary.get('total_sell_amount', 0):,.2f} 元
净流入金额: {summary.get('total_net_inflow', 0):,.2f} 元

{youzi_info}
{youzi_signal_context}

{longhubang_data[:8000]}

请基于以上龙虎榜数据，进行深入的游资行为分析：

1. **活跃游资识别与画像** ⭐ 核心
   - 识别当前最活跃的5-8个游资席位
   - 分析每个游资的操作风格（激进型/稳健型/超短型/波段型）
   - 评估游资的胜率和成功案例
   - 识别知名"牛散"和"游资大佬"

2. **游资操作特征分析**
   - 分析游资的买入特征（追高/低吸/打板/潜伏）
   - 分析游资的卖出特征（一日游/持有周期/止盈止损）
   - 识别游资的联合操作和接力特征
   - 判断游资是否存在抱团现象

3. **游资目标股票分析**
   - 分析游资重点关注的股票（前10只）
   - 识别游资集体看好的股票（多席位介入）
   - 分析游资选股的共性特征（题材/概念/技术形态）
   - 评估游资介入股票的后续爆发力

4. **游资进出节奏**
   - 判断游资整体是进攻还是防守状态
   - 分析游资对热点的跟随速度
   - 识别游资撤退的信号和板块
   - 评估游资的持续作战能力

5. **游资与题材的匹配**
   - 分析游资偏好的题材和概念
   - 识别游资正在炒作的热点
   - 判断题材的炒作周期位置
   - 预判下一个游资可能关注的题材

6. **风险与机会提示**
   - 识别游资可能设置的"陷阱"股票
   - 提示游资一致性过高的风险（容易崩盘）
   - 发现游资刚开始介入的潜力股
   - 评估跟随游资的风险收益比

7. **投资策略建议**
   - 推荐3-5只游资看好的潜力股票
   - 提示2-3只游资可能出货的风险股票
   - 给出跟随游资的操作建议
   - 提供仓位和止损建议

请给出专业、实战性强的游资行为分析报告。
"""


def build_stock_potential_prompt(summary: Dict[str, Any], stock_info: str, stock_signal_context: str, theme_whitelist_context: str, stock_theme_binding_context: str, longhubang_data: str) -> str:
    return f"""
你是一名资深的个股研究专家和短线交易高手，精通技术分析和资金分析，擅长从龙虎榜中挖掘短期爆发股。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个

{stock_info}
{stock_signal_context}
{theme_whitelist_context}
{stock_theme_binding_context}

{longhubang_data[:8000]}

请基于以上龙虎榜数据，进行深入的个股潜力分析：

1. **次日大概率上涨股票挖掘** ⭐⭐⭐ 最核心
   - 识别5-8只次日大概率上涨的股票
   - 严禁推荐“当日跌停池”股票
   - 详细分析每只股票的上涨逻辑（资金面、技术面、题材面）
   - 评估每只股票的上涨空间和确定性（高/中/低）
   - 给出买入时机与风控思路（不写具体价格）

2. **资金流向强度分析**
3. **技术形态评估**
4. **题材与概念分析**
5. **游资持仓分析**
6. **上榜类型分析**
7. **风险股票识别**
8. **操作策略建议**

请给出专业、实战、具有可操作性的个股潜力分析报告。务必重点分析次日大概率上涨的股票！
"""


def build_theme_tracker_prompt(summary: Dict[str, Any], concept_info: str, theme_signal_context: str, theme_whitelist_context: str, stock_theme_binding_context: str, longhubang_data: str) -> str:
    return f"""
你是一名资深的题材研究专家，拥有敏锐的市场嗅觉，擅长从龙虎榜数据中捕捉题材热点和板块轮动机会。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只

{concept_info}
{theme_signal_context}
{theme_whitelist_context}
{stock_theme_binding_context}

{longhubang_data[:8000]}

请基于以上龙虎榜数据，进行深入的题材追踪分析：

1. **热点题材识别** ⭐ 核心
2. **题材炒作周期分析**
3. **题材龙头与梯队**
4. **游资对题材的态度**
5. **题材轮动特征**
6. **题材与市场环境匹配度**
7. **题材风险评估**
8. **投资策略建议**

请给出专业、前瞻性强的题材追踪分析报告。
"""


def build_risk_control_prompt(summary: Dict[str, Any], risk_signal_context: str, longhubang_data: str) -> str:
    return f"""
你是一名资深的风险控制专家和反向思维大师，拥有20年的市场风险管理经验，擅长识别龙虎榜中的风险信号和资金陷阱。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个
总买入金额: {summary.get('total_buy_amount', 0):,.2f} 元
总卖出金额: {summary.get('total_sell_amount', 0):,.2f} 元
净流入金额: {summary.get('total_net_inflow', 0):,.2f} 元

{risk_signal_context}

{longhubang_data[:8000]}

请基于以上龙虎榜数据，进行全面的风险分析：

1. **高风险股票识别** ⭐ 核心
2. **游资出货信号识别**
3. **资金陷阱识别**
4. **题材风险评估**
5. **技术面风险提示**
6. **情绪风险评估**
7. **系统性风险提示**
8. **风险管理建议**

请给出专业、严谨、保守的风险控制报告，宁可错过，不可做错。
"""


def build_chief_strategist_prompt(
    chief_signal_context: str,
    theme_whitelist_context: str,
    stock_theme_binding_context: str,
    candidate_quota_context: str,
    analyses_text: str,
    quota_enabled: bool,
    quota_count: int,
    quota_ratio: float,
    max_limit_like: int,
) -> str:
    return f"""
你是一名资深的首席投资策略师，拥有CFA、FRM等专业资格，具有25年的市场实战经验和卓越的综合分析能力。

你的团队包含4位专业分析师，他们已经从不同维度完成了龙虎榜数据分析：
1. 游资行为分析师 - 分析游资操作特征和意图
2. 个股潜力分析师 - 挖掘次日大概率上涨的股票
3. 题材追踪分析师 - 识别热点题材和轮动机会
4. 风险控制专家 - 识别高风险股票和市场陷阱

以下是各位分析师的详细分析报告：

{chief_signal_context}
{theme_whitelist_context}
{stock_theme_binding_context}
{candidate_quota_context}

【推荐分层配额约束（最终表格必须满足）】
- 启用状态：{"开启" if quota_enabled else "关闭"}
- 推荐总数：{quota_count}
- 近涨停标签占比上限：{round(quota_ratio * 100, 2)}%（最多 {max_limit_like} 只）
- 说明：当配额开启时，“次日重点推荐股票表”必须严格遵守该比例与总数。

{analyses_text[:15000]}

请作为首席策略师，综合以上所有分析，给出最终的投资策略报告。
"""


def build_repair_advice_prompt(items: List[str], chief_analysis: str) -> str:
    return f"""
你是短线交易策略顾问。请仅对给定股票补全“详细推荐理由+交易建议字段”。

【待补全股票】
{chr(10).join(items)}

【首席报告摘要（可参考）】
{str(chief_analysis or '')[:3000]}

【输出硬约束】
1) 仅输出一个Markdown表格，不要输出任何解释文字。
2) 表头固定：股票代码 | 推荐理由（详细） | 确定性评级 | 买入时机建议 | 交易触发条件 | 持有周期建议 | 止损思路 | 失效条件
3) 每只股票必须一行，覆盖全部给定股票代码。
4) 禁止使用“待定/暂无/N/A/观察”等占位词。
5) 推荐理由必须是完整句，至少包含：资金面依据 + 题材/逻辑依据 + 次日观察重点（可合并成一段）。
6) 建议必须可执行，且不写具体价格数字。
7) 禁止直接复述输入里的模板片段，必须改写成自然语言总结。
8) 推荐理由尽量先总结“为什么值得关注”，再补“次日看什么信号”。
"""


def build_repair_table_prompt(chief_analysis: str, candidate_quota_context: str, candidate_kline_context: str, recommendation_count: int, recommendation_limit_up_ratio: float) -> str:
    rec_count = max(3, min(int(recommendation_count or 8), 12))
    ratio = max(0.0, min(float(recommendation_limit_up_ratio or 0.4), 1.0))
    max_limit_like = int(rec_count * ratio + 1e-9)
    min_non_limit_like = max(0, rec_count - max_limit_like)
    return f"""
你是首席投资策略师的“推荐表修正助手”。
你的任务：在不改变整体报告观点的前提下，重选“次日重点推荐股票”表，确保满足分层配额。

【原首席报告（节选）】
{str(chief_analysis or '')[:3500]}

【候选池与字段说明（系统口径）】
{candidate_quota_context}
{candidate_kline_context}

【硬性约束】
1) 仅可从候选池明细中的股票代码中选择，不得新增候选池外股票。
2) 推荐总数必须为 {rec_count} 只。
3) 近涨停标签（is_limit_like_for_quota=是）最多 {max_limit_like} 只。
4) 非近涨停（is_limit_like_for_quota=否）至少 {min_non_limit_like} 只。
5) 优先保留原报告核心逻辑，但配额不满足时必须优先满足配额。
6) 不要出现“待定/暂无/N/A/观察”等占位词。
7) 可参考9日日线趋势字段做强弱筛选，但其权重低于资金与题材主信号。

【输出格式（必须严格）】
- 仅输出一个 Markdown 表格，不要任何额外文字。
- 表头固定为：
| 序号 | 股票名称 | 股票代码 | 推荐理由（含题材线索） | 确定性评级 | 买入时机建议 | 交易触发条件 | 持有周期建议 |
"""
