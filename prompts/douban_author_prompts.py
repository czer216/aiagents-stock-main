from typing import List, Dict
import json


def build_pattern_extraction_prompt(author_name: str, posts: List[Dict], lookback_days: int = 60) -> str:
    lines = []
    for idx, p in enumerate(posts[:30], 1):
        title = str(p.get("post_title", "") or "")
        published = str(p.get("post_published_at", "") or "")
        content = str(p.get("clean_content", "") or "")[:1800]
        lines.append(f"[{idx}] 时间: {published}\\n标题: {title}\\n内容: {content}")

    corpus = "\\n\\n".join(lines) if lines else "无历史帖子"

    return f"""
你是A股短线交易模式研究员。请基于以下作者在最近{lookback_days}天的帖子，提炼交易规则库。

作者: {author_name}

帖子样本:
{corpus}

硬性要求：
1) 严禁输出任何具体股票代码、股票名称、个股举例；
2) 只输出可复用的交易规则；
3) 证据必须来自提供的帖子原文；
4) 如果样本不足，降低置信度并在summary说明。

请仅输出JSON，不要输出其他内容，格式如下：
{{
  "trading_style": "一句话概括风格",
  "rule_set": [
    {{"rule_id": "R1", "rule": "规则描述", "when_to_use": "适用场景", "when_not_to_use": "不适用场景"}}
  ],
  "sector_preferences": ["偏好板块1", "偏好板块2"],
  "timing_rules": ["择时规则1", "择时规则2"],
  "entry_exit_heuristics": ["进出场规则1", "进出场规则2"],
  "risk_controls": ["风控规则1", "风控规则2"],
  "confidence": 0-100,
  "evidence": [
    {{"title": "帖子标题", "date": "日期", "point": "支持该模式的原文要点"}}
  ],
  "summary": "100字以内总结"
}}
""".strip()


def build_pattern_update_prompt(author_name: str, previous_pattern_json: Dict, new_posts: List[Dict], lookback_days: int = 60) -> str:
    lines = []
    for idx, p in enumerate((new_posts or [])[:20], 1):
        title = str(p.get("post_title", "") or "")
        published = str(p.get("post_published_at", "") or "")
        content = str(p.get("clean_content", "") or "")[:1800]
        lines.append(f"[{idx}] 时间: {published}\\n标题: {title}\\n内容: {content}")

    new_corpus = "\\n\\n".join(lines) if lines else "无新增帖子"
    prev_block = json.dumps(previous_pattern_json or {}, ensure_ascii=False)

    return f"""
你是A股短线交易模式研究员。请基于“上一版模式 + 新增帖子”做增量修订。

作者: {author_name}
最近增量窗口: {lookback_days}天

上一版模式(JSON):
{prev_block}

新增帖子样本:
{new_corpus}

硬性要求：
1) 输出结构必须与上一版模式一致，可在字段内增删规则；
2) 仅在新增帖子证据支持时更新规则，否则保持原有规则稳定；
3) 严禁输出任何具体股票代码、股票名称、个股举例；
4) evidence 仅引用新增帖子原文；
5) 如果新增信息不足，维持原结论并在summary说明。

请仅输出JSON，不要输出其他内容，格式如下：
{{
  "trading_style": "一句话概括风格",
  "rule_set": [
    {{"rule_id": "R1", "rule": "规则描述", "when_to_use": "适用场景", "when_not_to_use": "不适用场景"}}
  ],
  "sector_preferences": ["偏好板块1", "偏好板块2"],
  "timing_rules": ["择时规则1", "择时规则2"],
  "entry_exit_heuristics": ["进出场规则1", "进出场规则2"],
  "risk_controls": ["风控规则1", "风控规则2"],
  "confidence": 0-100,
  "evidence": [
    {{"title": "帖子标题", "date": "日期", "point": "支持该模式的原文要点"}}
  ],
  "summary": "100字以内总结"
}}
""".strip()


def build_candidate_generation_prompt(
    author_name: str,
    pattern_json: Dict,
    historical_success_patterns: List[Dict] = None,
    historical_failure_lessons: List[Dict] = None,
    author_feedback_metrics: Dict = None,
    allowed_stock_pool: List[Dict] = None,
    candidate_limit: int = 3,
    analysis_date: str = "",
    target_trade_date: str = "",
    market_context: Dict = None,
    intraday_mode: bool = False,
) -> str:
    success_block = json.dumps(historical_success_patterns or [], ensure_ascii=False)
    failure_block = json.dumps(historical_failure_lessons or [], ensure_ascii=False)
    feedback_block = json.dumps(author_feedback_metrics or {}, ensure_ascii=False)
    pool_block = json.dumps((allowed_stock_pool or [])[:120], ensure_ascii=False)
    market_context_block = json.dumps(market_context or {}, ensure_ascii=False)
    stale_data_rule = "7) 若候选的最新K线日期早于分析锚点日，默认视为数据不新鲜，优先回避或降低置信度并说明；" if not intraday_mode else "7) 当前为盘中实时场景：若rt_source=tdx且rt_update_time有效，不得使用“数据不新鲜”作为理由；若实时涨幅已较大（如>=7%），避免给出低吸建议。"

    return f"""
你是A股交易执行顾问。请基于作者交易模式，从候选池中输出“下一交易日执行计划”。

作者: {author_name}
分析锚点日(收盘后): {analysis_date or '未提供'}
目标交易日: {target_trade_date or '未提供'}

作者模式(JSON):
{pattern_json}

历史成功样本(JSON):
{success_block}

历史失败教训(JSON):
{failure_block}

作者后验反馈统计(JSON):
{feedback_block}

大盘环境快照(JSON):
{market_context_block}

龙虎榜候选池(每只股票附近10个交易日K线, JSON):
{pool_block}

约束：
1) 只能从候选池中选择股票代码；
2) 不做形态打分，只结合给定10日K线和作者模式给出交易想法；
3) 若无合适标的，返回空 candidates 并在 summary 说明原因；
4) 推荐理由必须引用某条作者规则(rule_id或规则摘要)；
5) 最多输出 {int(candidate_limit or 3)} 只股票候选，按确定性从高到低排序；
6) 所有建议必须面向“目标交易日”开盘后的计划，不能把分析锚点日前已触发且已失效的信号当作新机会；
{stale_data_rule}
8) 对于同一只股票：若输入候选池的theme非空，输出theme必须原样复用，不得改写为无关题材；仅当输入theme为空或缺失时，才允许你补全推断theme；
9) 每个candidate必须始终输出theme字段（可为空字符串，但字段不能缺失）；
10) 需要结合“大盘环境快照”调整候选优先级与仓位建议：情绪偏弱时优先防守和低仓位，情绪偏强时可提高进攻性，但仍需遵守作者规则。

请仅输出JSON，不要输出其他内容，格式如下：
{{
  "candidates": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "action": "操作建议(买入观察/低吸/持有/回避)",
      "reason": "推荐理由(不超过80字)",
      "theme": "所属主线/题材",
      "trigger_logic": "触发逻辑",
      "entry_idea": "入场思路",
      "exit_idea": "止盈/止损思路",
      "risk": "主要风险",
      "confidence": 0-100,
      "why_consistent_with_author": "与作者风格一致原因",
      "which_past_lesson_applied": "引用的历史教训或成功经验",
      "matched_rule": "匹配的规则ID或摘要",
      "expected_hold_horizon": "预期持有周期(T1/T3/T5)"
    }}
  ],
  "positioning_advice": "仓位建议",
  "invalidators": ["失效条件1", "失效条件2"],
  "summary": "当日执行摘要"
}}
""".strip()
