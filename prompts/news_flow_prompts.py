from typing import Dict, List


def build_sector_impact_prompt(topics_text: str, news_text: str, flow_info: str) -> str:
    return f"""你是一名资深的A股短线投资分析师，专注于热点题材挖掘和板块轮动分析。

【重要】请根据以下全网热点数据，进行深度的A股题材分析：

=== 全网热门话题TOP20 ===
{topics_text}

=== 股票相关新闻TOP15 ===
{news_text}
{flow_info}

请完成以下分析任务：

1. **题材挖掘**：从以上热点中挖掘出可能引爆A股的核心题材概念
2. **板块分析**：分析最可能受益的A股板块（要具体到申万行业或同花顺概念板块）
3. **热度评估**：评估每个板块的潜在炒作热度和持续性
4. **龙头预判**：推测可能的龙头股特征

请以JSON格式输出：
{{
    "hot_themes": [
        {{"theme": "题材名称", "source": "来源热点", "heat_level": "极高/高/中", "sustainability": "持续性评估"}}
    ],
    "benefited_sectors": [
        {{
            "name": "板块名称（要具体如：AI算力、低空经济、机器人等）",
            "impact": "利好",
            "confidence": 85,
            "reason": "详细分析原因",
            "related_concepts": ["相关概念1", "相关概念2"],
            "leader_characteristics": "龙头股特征描述"
        }}
    ],
    "damaged_sectors": [
        {{"name": "板块名称", "impact": "利空", "confidence": 60, "reason": "原因"}}
    ],
    "opportunity_assessment": "今日A股投资机会综合评估（100字以内）",
    "trading_suggestion": "短线操作建议",
    "key_points": ["核心要点1", "核心要点2", "核心要点3"]
}}

只输出JSON，不要其他文字。"""


def build_stock_recommend_prompt(sectors_text: str, flow_stage: str, sentiment_class: str, concepts_text: str) -> str:
    return f"""你是一名资深的A股短线游资操盘手，专注于热点题材龙头股挖掘。

=== 当前市场状态 ===
- 流量阶段: {flow_stage}
- 情绪状态: {sentiment_class}
- 相关概念: {concepts_text}

=== 热门受益板块分析 ===
{sectors_text}

=== 选股要求 ===
请根据"流量为王"理念，推荐5-8只A股短线标的：

选股法则（必须遵循）：
1. **先涨为王**：优先选择已经启动、走势强势的股票
2. **名字为王**：股票名称与热点高度相关（如AI概念选"智"字头）
3. **龙头优先**：选择板块内最强势的龙头或人气股
4. **题材纯正**：主业与热点题材高度相关
5. **流通盘适中**：30-150亿市值为佳，便于资金操作

请以JSON格式输出：
{{
    "recommended_stocks": [
        {{
            "code": "股票代码（6位数字，如000001或600001）",
            "name": "股票名称",
            "sector": "所属板块",
            "market": "沪市/深市/创业板/科创板",
            "market_cap": "市值（亿）",
            "reason": "推荐理由（与热点的关联性）",
            "catalyst": "催化剂/驱动因素",
            "strategy": "操作策略（进场/加仓/止损建议）",
            "target_space": "目标空间（如15-20%）",
            "risk_level": "低/中/高",
            "attention_points": ["注意事项1", "注意事项2"]
        }}
    ],
    "overall_strategy": "整体操作策略和仓位建议",
    "timing_advice": "最佳介入时机判断",
    "risk_warning": "风险提示（必须包含投资风险提醒）"
}}

【重要】只推荐真实存在的A股股票，代码必须正确。只输出JSON。"""


def build_risk_assess_prompt(flow_stage: str, sentiment_data: Dict, viral_k: float, flow_type: str) -> str:
    return f"""你是一名专业的风险管理分析师。

请根据以下市场数据评估当前投资风险：

市场状态：
- 流量阶段: {flow_stage}
- 情绪指数: {sentiment_data.get('sentiment_index', 50)}
- 情绪分类: {sentiment_data.get('sentiment_class', '中性')}
- K值(病毒系数): {viral_k}
- 流量类型: {flow_type}

核心理念：
- 流量高潮 = 价格高潮 = 逃命时刻
- K值>1.5表示指数型爆发，风险上升
- 情绪极端（>85或<20）都意味着风险

请分析：
1. 当前风险等级（极低/低/中等/高/极高）
2. 主要风险因素
3. 风险分数（0-100）
4. 详细分析

以JSON格式输出：
{{
    "risk_level": "高",
    "risk_score": 75,
    "risk_factors": ["风险因素1", "风险因素2", ...],
    "opportunities": ["机会1", "机会2", ...],
    "analysis": "详细分析文字",
    "key_warning": "最重要的警告"
}}

只输出JSON。"""


def build_investment_advisor_prompt(sector_analysis: Dict, stock_recommend: Dict, risk_assess: Dict, flow_data: Dict, sentiment_data: Dict) -> str:
    sectors_text = ', '.join([s.get('name', '') for s in sector_analysis.get('benefited_sectors', [])[:3]])
    stocks_text = ', '.join([f"{s.get('name', '')}({s.get('code', '')})" for s in stock_recommend.get('recommended_stocks', [])[:3]])
    return f"""你是一名首席投资策略师，需要给出最终的投资建议。

综合分析数据：

【流量分析】
- 流量得分: {flow_data.get('total_score', 'N/A')}
- 流量等级: {flow_data.get('level', 'N/A')}

【情绪分析】
- 情绪指数: {sentiment_data.get('sentiment_index', 50)}
- 情绪分类: {sentiment_data.get('sentiment_class', '中性')}
- 流量阶段: {sentiment_data.get('flow_stage', '未知')}

【板块分析】
- 受益板块: {sectors_text}
- 机会评估: {sector_analysis.get('opportunity_assessment', 'N/A')}

【股票推荐】
- 推荐股票: {stocks_text}

【风险评估】
- 风险等级: {risk_assess.get('risk_level', '中等')}
- 风险分数: {risk_assess.get('risk_score', 50)}
- 主要风险: {', '.join(risk_assess.get('risk_factors', [])[:3])}

核心原则（流量为王）：
- 流量高潮 = 价格高潮 = 逃命时刻
- 当热搜、媒体报道、KOL转发同时达到高潮时，就是出货时机
- 短线操作：快进快出，紧跟龙头

请给出最终投资建议：
1. 操作建议（买入/持有/观望/回避）
2. 置信度（0-100）
3. 综合总结
4. 具体行动计划

以JSON格式输出：
{{
    "advice": "观望",
    "confidence": 75,
    "summary": "综合总结文字",
    "action_plan": [
        "行动1",
        "行动2",
        ...
    ],
    "position_suggestion": "仓位建议",
    "timing": "时机判断",
    "key_message": "最重要的一句话"
}}

只输出JSON。"""


def build_sector_deep_prompt(sector_name: str, news_text: str, topics_text: str) -> str:
    return f"""你是{sector_name}板块的专业分析师。

请对以下与{sector_name}相关的新闻进行深度分析：

【相关新闻】
{news_text}

【相关热点话题】
{topics_text}

请分析：
1. {sector_name}板块当前的市场热度和关注度
2. 驱动因素分析（政策/技术/资金/事件）
3. 短期（1-3天）走势预判
4. 核心龙头股分析（至少3只）
5. 投资建议和风险提示

以JSON格式输出：
{{
    "sector_name": "{sector_name}",
    "heat_level": "极高/高/中/低",
    "heat_score": 85,
    "drivers": [
        {{"type": "政策", "content": "具体驱动因素", "impact": "正面/负面"}}
    ],
    "short_term_outlook": "看涨/震荡/看跌",
    "outlook_reason": "预判理由",
    "leader_stocks": [
        {{
            "code": "600000",
            "name": "股票名称",
            "reason": "龙头理由",
            "strategy": "操作策略"
        }}
    ],
    "investment_advice": "具体投资建议",
    "risk_warning": "风险提示",
    "key_indicators": {{
        "关注度": "高",
        "资金流向": "净流入",
        "情绪指数": 75
    }}
}}

只输出JSON。"""
