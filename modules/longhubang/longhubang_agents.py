"""
智瞰龙虎AI分析师集群
专注于龙虎榜数据的多维度分析
"""

from infrastructure.ai.deepseek_client import DeepSeekClient
from typing import Dict, Any, List, Optional
from collections import Counter
import time
import logging
import re
import config
from prompts.longhubang.analysts import (
    build_youzi_behavior_prompt,
    build_stock_potential_prompt,
    build_theme_tracker_prompt,
    build_risk_control_prompt,
    build_chief_strategist_prompt,
    build_repair_advice_prompt,
    build_repair_table_prompt,
)


class LonghubangAgents:
    """龙虎榜AI分析师集合"""
    
    def __init__(self, model=None):
        self.model = model or config.DEFAULT_MODEL_NAME
        self.logger = logging.getLogger(__name__)
        self.deepseek_client = DeepSeekClient(model=self.model)
        self.logger.info(f"[智瞰龙虎] AI分析师系统初始化 (模型: {self.model})")

    def _run_agent_call(self, agent_name: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        """统一封装 AI 调用日志，便于定位是否真正发起模型请求。"""
        started_at = time.time()
        user_chars = 0
        for msg in reversed(messages or []):
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_chars = len(str(msg.get("content", "") or ""))
                break
        self.logger.info(
            f"[AI请求] {agent_name} 开始调用模型 | model={self.model} | "
            f"user_prompt_chars={user_chars} | max_tokens={max_tokens}"
        )
        analysis = self.deepseek_client.call_api(messages, max_tokens=max_tokens)
        elapsed = round(time.time() - started_at, 2)
        self.logger.info(
            f"[AI请求] {agent_name} 调用结束 | elapsed={elapsed}s | output_chars={len(analysis or '')}"
        )
        return analysis

    def _normalize_code(self, code: Any) -> str:
        text = str(code or "").strip()
        if "." in text:
            text = text.split(".", 1)[0]
        return text if len(text) == 6 and text.isdigit() else ""

    def _ok(self, data: Optional[Dict[str, Any]]) -> bool:
        return isinstance(data, dict) and data.get("data_success")

    def _split_theme_tokens(self, text: str) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        parts = re.split(r"[+＋,，;；/|、\s]+", raw)
        noise = {
            "涨停", "跌停", "概念", "题材", "板块", "原因", "N/A", "None", "nan", "-",
            "融资融券", "沪股通", "深股通", "陆股通", "港股通",
        }
        out: List[str] = []
        seen = set()
        for part in parts:
            token = str(part or "").strip()
            if not token or token in noise or token.isdigit() or len(token) < 2:
                continue
            if token.endswith("板块"):
                continue
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    def _build_theme_whitelist_context(self, summary: Dict[str, Any]) -> str:
        """
        构建题材白名单：
        - 主体使用统一字段 theme_top
        - 同时补充 lu_desc_top 拆词（仅当后端已判定为真实来源时才会存在）
        """
        tokens: List[str] = []
        seen = set()
        for row in (summary.get("top_stock_lu_desc", []) or []):
            for item in (row.get("theme_top", []) or []):
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    tokens.append(text)
            for raw in (row.get("lu_desc_top", []) or []):
                for text in self._split_theme_tokens(raw):
                    if text and text not in seen:
                        seen.add(text)
                        tokens.append(text)
        if not tokens:
            return (
                "\n【题材词白名单】\n"
                "- 当前无可用白名单词；允许使用AI知识补充题材，但必须显式标注“AI知识标签：xxx”。"
            )
        top_tokens = tokens[:80]
        return (
            "\n【题材词白名单（仅可使用以下词）】\n"
            + "、".join(top_tokens)
            + "\n【硬性约束】\n"
            + "- 所有题材/概念表述必须来自上述白名单，不允许同义词扩展、行业推断或自造词。\n"
            + "- 若某股票匹配不到白名单词，可使用AI知识补充，但必须显式写“AI知识标签：xxx”。"
        )

    def _build_stock_theme_binding_context(self, summary: Dict[str, Any]) -> str:
        """
        个股题材绑定约束：
        - 每只股票仅可使用该股票自身的 theme_top + lu_desc_top 拆词结果
        - 禁止跨股票借用题材词
        """
        rows = []
        for item in (summary.get("top_stock_lu_desc", []) or [])[:30]:
            code = self._normalize_code(item.get("code", ""))
            name = str(item.get("name", "") or "").strip()
            if not code:
                continue
            own_tokens: List[str] = []
            seen = set()
            for tok in (item.get("theme_top", []) or []):
                text = str(tok or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    own_tokens.append(text)
            for raw in (item.get("lu_desc_top", []) or []):
                for text in self._split_theme_tokens(raw):
                    if text and text not in seen:
                        seen.add(text)
                        own_tokens.append(text)
            rows.append(
                f"- {name}({code}): {'、'.join(own_tokens[:6]) if own_tokens else '题材线索不足'}"
            )
        if not rows:
            rows = ["- 暂无个股题材绑定数据，允许使用AI知识标签（需显式标注“AI知识标签：xxx”）。"]
        return (
            "\n【个股题材绑定清单（逐股强约束）】\n"
            + "\n".join(rows)
            + "\n【逐股硬性规则】\n"
            + "- 当你分析/推荐某只股票时，只能使用该股票在上表同一行中的题材词。\n"
            + "- 严禁使用其他股票行的题材词，严禁跨股票迁移概念。\n"
            + "- 若该股票行是“题材线索不足”，允许使用AI知识补充，但必须写“AI知识标签：xxx”。"
        )

    def _build_brief_signal_context(
        self,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = ["【龙虎榜补充热度信号（Tushare摘要）】"]

        if self._ok(concept_rotation_data):
            strongest = concept_rotation_data.get("strongest_today", {}) or {}
            rotation_summary = concept_rotation_data.get("rotation_summary", {}) or {}
            daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
            top3 = []
            if daily_rankings:
                top3 = daily_rankings[0].get("top_concepts", [])[:3]
            top3_str = "、".join(
                [f"{x.get('concept', '')}(hot:{x.get('hot_num_total', 0)})" for x in top3 if x.get("concept")]
            ) or "暂无"
            lines.append(
                f"- 概念轮动：最强概念 {strongest.get('concept', 'N/A')}"
                f"(关联股{strongest.get('stock_count', strongest.get('limit_up_count', 0))},hot:{strongest.get('hot_num_total', 0)})，"
                f"资金动向 {rotation_summary.get('capital_flow_signal', 'N/A')}，今日TOP3 {top3_str}"
            )

        if self._ok(limit_step_data):
            step_summary = limit_step_data.get("market_heat_summary", {}) or {}
            strongest_step_concepts = limit_step_data.get("strongest_step_concepts", [])[:3]
            strongest_step_concepts_text = "、".join(
                [f"{x.get('concept', '')}({x.get('score', 0)})" for x in strongest_step_concepts if x.get("concept")]
            ) or "暂无"
            lines.append(
                f"- 连板晋级：热度 {step_summary.get('heat_level', 'N/A')}，"
                f"平均连板高度 {step_summary.get('avg_max_step', 0)}，"
                f"平均晋级个数 {step_summary.get('avg_advance_count', step_summary.get('avg_promotion_count', 0))}，"
                f"最新晋级 {step_summary.get('latest_advance_count', 0)}，"
                f"梯队最强概念 {strongest_step_concepts_text}"
            )

        if self._ok(ths_hot_data):
            hot_summary = ths_hot_data.get("hot_summary", {}) or {}
            top_today = hot_summary.get("top_today", [])[:5]
            top_hot = "、".join(
                [
                    f"{x.get('name', x.get('code', ''))}({x.get('code', '')})-TOP{x.get('rank', '-')}"
                    for x in top_today
                ]
            ) or "暂无"
            lines.append(
                f"- 同花顺A股热榜：覆盖天数 {hot_summary.get('days', 0)}，"
                f"跟踪股票 {hot_summary.get('tracked_stocks', 0)} 只，"
                f"今日热榜 {top_hot}"
            )

        if self._ok(kpl_list_data):
            kpl_summary = kpl_list_data.get("kpl_summary", {}) or {}
            top_themes = kpl_summary.get("top_themes_today", [])[:5]
            top_theme_str = "、".join(
                [f"{x.get('theme', '')}({x.get('count', 0)})" for x in top_themes if x.get("theme")]
            ) or "暂无"
            lines.append(
                f"- 开盘啦热度：覆盖天数 {kpl_summary.get('days', 0)}，"
                f"跟踪股票 {kpl_summary.get('tracked_stocks', 0)} 只，"
                f"最热题材 {kpl_summary.get('hottest_theme', 'N/A')}，"
                f"今日题材TOP {top_theme_str}"
            )

        if self._ok(p1_signal_data):
            lhb_summary = p1_signal_data.get("lhb_summary", {}) or {}
            money_summary = p1_signal_data.get("moneyflow_summary", {}) or {}
            limit_summary = p1_signal_data.get("limit_quality_summary", {}) or {}
            youzi_summary = p1_signal_data.get("youzi_profile_summary", {}) or {}
            concept_flow_top = (p1_signal_data.get("concept_flow_top", []) or [])[:3]
            youzi_top = (p1_signal_data.get("youzi_profile_top", []) or [])[:3]
            concept_flow_text = "、".join(
                [f"{x.get('concept', '')}({x.get('flow_score', 0)})" for x in concept_flow_top if x.get("concept")]
            ) or "暂无"
            youzi_top_text = "、".join(
                [
                    f"{x.get('hm_name', '')}"
                    f"{'/' + str(x.get('style', '')) if x.get('style') else ''}"
                    f"(score:{x.get('profile_score', 0)})"
                    for x in youzi_top
                    if x.get("hm_name")
                ]
            ) or "暂无"
            lines.append(
                f"- P1增强信号：机构净买入正向 {lhb_summary.get('inst_positive_count', 0)}/"
                f"{lhb_summary.get('tracked_stocks', 0)}，"
                f"主力净流入正向占比 {money_summary.get('positive_ratio', 0)}，"
                f"高质量封板 {limit_summary.get('high_quality_count', 0)} 只，"
                f"游资净买入正向 {youzi_summary.get('youzi_positive_count', 0)}/"
                f"{youzi_summary.get('tracked_stocks', 0)}，"
                f"概念资金TOP {concept_flow_text}，"
                f"游资画像TOP {youzi_top_text}"
            )

        if isinstance(kline_9d_data, dict) and kline_9d_data.get("data_success"):
            trend_summary = kline_9d_data.get("trend_summary", {}) or {}
            stage_dist = trend_summary.get("stage_distribution", {}) or {}
            stage_text = "、".join([f"{k}:{v}" for k, v in stage_dist.items() if v]) or "暂无"
            top_strength = (kline_9d_data.get("top_strength", []) or [])[:5]
            top_text = "、".join(
                [
                    f"{x.get('name', x.get('code', ''))}({x.get('code', '')})/"
                    f"{x.get('trend_stage', '-')}/"
                    f"{x.get('change_7d_pct', 0)}%"
                    for x in top_strength
                    if x.get("code")
                ]
            ) or "暂无"
            lines.append(
                f"- 9日K线趋势：覆盖 {trend_summary.get('tracked_stocks', 0)} 只，"
                f"分布 {stage_text}，"
                f"强势TOP {top_text}"
            )
            weekly_summary = (kline_9d_data.get("weekly_summary", {}) or {})
            if weekly_summary:
                weekly_dist = weekly_summary.get("stage_distribution", {}) or {}
                weekly_text = "、".join([f"{k}:{v}" for k, v in weekly_dist.items() if v]) or "暂无"
                lines.append(
                    f"- 周线辅助（低权重）：覆盖 {weekly_summary.get('tracked_stocks', 0)} 只，"
                    f"分布 {weekly_text}（仅作弱参考）"
                )
            scale = max(0.0, min(float(trend_overlay_scale or 1.0), 10.0))
            if scale <= 0.0:
                weight_tip = "已关闭（仅忽略）"
            elif scale <= 1.0:
                weight_tip = "弱参考（仅辅助）"
            elif scale <= 3.0:
                weight_tip = "中等参考（辅助判断）"
            else:
                weight_tip = "较强参考（仍不主导）"
            lines.append(
                f"- 9日K线参考权重：{round(scale, 2)}/10，{weight_tip}。"
                f"请勿仅依据K线否定资金与题材主信号。"
            )

        if isinstance(market_style_context, dict) and market_style_context:
            heat_score = float(market_style_context.get("market_heat_score", 50.0) or 50.0)
            market_temperature = str(market_style_context.get("market_temperature", "warm") or "warm")
            style_regime = str(market_style_context.get("style_regime", "balanced") or "balanced")
            style_conf = float(market_style_context.get("style_confidence", 0.0) or 0.0)
            lines.append(
                f"- 市场温度：{market_temperature}（热度分 {round(heat_score, 2)}），"
                f"风格开关：{style_regime}（置信度 {round(style_conf, 2)}，仅作辅助）"
            )

        if fixed_candidate_stocks:
            pos_counter = Counter(
                str(x.get("lhb_position_tag", "") or "unknown") for x in fixed_candidate_stocks
            )
            cap_counter = Counter(
                str(x.get("lhb_capital_behavior_tag", "") or "mixed") for x in fixed_candidate_stocks
            )
            sector_counter = Counter(
                str(x.get("lhb_sector_tier", "") or "rotation") for x in fixed_candidate_stocks
            )
            pos_text = "、".join([f"{k}:{v}" for k, v in pos_counter.most_common(4)]) or "暂无"
            cap_text = "、".join([f"{k}:{v}" for k, v in cap_counter.most_common(4)]) or "暂无"
            sector_text = "、".join([f"{k}:{v}" for k, v in sector_counter.most_common(4)]) or "暂无"
            lines.append(f"- 候选位置分布：{pos_text}")
            lines.append(f"- 候选资金行为分布：{cap_text}")
            lines.append(f"- 候选板块分层分布：{sector_text}")

        if len(lines) == 1:
            lines.append("- 暂无可用的补充热度信号（可能为Token权限或接口数据为空）")

        if fixed_candidate_stocks:
            lines.append("\n【固定候选池（统一上下文）】")
            lines.append("- 以下候选池为系统固定列表，所有分析师请在该范围内优先分析与推荐。")
            lines.append("- 格式：序号. 股票名称(代码) | 净流入 | pct_chg | 当日涨停 | 连板状态 | 连板数 | 筹码胜率 | 筹码微调 | 近涨停标签 | 封板质量 | 数据质量(A/B/C) | 题材词")
            for idx, stock in enumerate((fixed_candidate_stocks or [])[:40], 1):
                code = self._normalize_code(stock.get("code", ""))
                if not code:
                    continue
                name = str(stock.get("name", "") or "")
                try:
                    net_inflow = float(stock.get("net_inflow", 0.0) or 0.0)
                except Exception:
                    net_inflow = 0.0
                pct_raw = stock.get("pct_chg", None)
                try:
                    pct_text = "-" if pct_raw is None else f"{round(float(pct_raw), 2)}%"
                except Exception:
                    pct_text = "-"
                is_limit_up = "是" if bool(stock.get("is_today_limit_up", False)) else "否"
                limit_up_status = str(stock.get("limit_up_status", "") or "").strip() or "-"
                try:
                    limit_up_streak = int(float(stock.get("limit_up_streak", 0) or 0))
                except Exception:
                    limit_up_streak = 0
                is_limit_like = "是" if bool(stock.get("is_limit_like_for_quota", False)) else "否"
                try:
                    cyq_winner_rate = round(float(stock.get("cyq_winner_rate", 0.0) or 0.0), 4)
                except Exception:
                    cyq_winner_rate = 0.0
                try:
                    cyq_adjust = round(float(stock.get("cyq_adjust", 0.0) or 0.0), 3)
                except Exception:
                    cyq_adjust = 0.0
                try:
                    limit_quality = round(float(stock.get("limit_quality_score", 0.0) or 0.0), 3)
                except Exception:
                    limit_quality = 0.0
                quality_grade = str(stock.get("data_quality_grade", "") or "-")
                raw_themes = stock.get("theme_tokens", [])
                if isinstance(raw_themes, str):
                    themes = raw_themes.strip() or "暂无"
                else:
                    themes = "、".join([str(x) for x in (raw_themes or [])[:3] if str(x).strip()]) or "暂无"
                lines.append(
                    f"{idx}. {name}({code}) | 净流入 {net_inflow:,.2f} | "
                    f"pct_chg {pct_text} | 当日涨停 {is_limit_up} | 连板状态 {limit_up_status} | 连板数 {limit_up_streak} | "
                    f"筹码胜率 {cyq_winner_rate:.4f} | 筹码微调 {cyq_adjust:.3f} | 近涨停标签 {is_limit_like} | "
                    f"封板质量 {limit_quality} | 数据质量 {quality_grade} | 题材词 {themes}"
                )
            lines.append(f"- 固定候选池总数：{len(fixed_candidate_stocks)}")

        return "\n".join(lines)

    def _build_theme_signal_context(
        self,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = [
            self._build_brief_signal_context(
                concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
            )
        ]

        if self._ok(concept_rotation_data):
            lines.append("\n【近5日概念轮动明细】")
            for day in (concept_rotation_data.get("daily_rankings", []) or [])[:5]:
                top3 = day.get("top_concepts", [])[:3]
                top3_str = "、".join(
                    [f"{x.get('concept', '')}(hot:{x.get('hot_num_total', 0)})" for x in top3 if x.get("concept")]
                )
                lines.append(
                    f"- {day.get('trade_date', '')}: 最强 {day.get('strongest_concept', 'N/A')}"
                    f"(关联股{day.get('strongest_count', 0)},hot:{day.get('strongest_hot_num', 0)}), "
                    f"TOP3 {top3_str or '暂无'}"
                )

        if self._ok(limit_step_data):
            lines.append("\n【近5日连板晋级明细】")
            for day in (limit_step_data.get("daily_step_stats", []) or [])[:5]:
                lines.append(
                    f"- {day.get('trade_date', '')}: 最高连板 {day.get('max_step', 0)}，"
                    f"晋级个数 {day.get('advance_count', 0)}，"
                    f"晋级池 {day.get('promotion_count', 0)}，"
                    f"高标数 {day.get('hot_stock_count', 0)}"
                )

        if self._ok(ths_hot_data):
            lines.append("\n【近3日同花顺A股热榜TOP10】")
            for day in (ths_hot_data.get("daily_hot_stats", []) or [])[:3]:
                top10 = day.get("top_hot", [])[:10]
                top_str = "、".join(
                    [f"{x.get('name', '')}({x.get('code', '')})-TOP{x.get('rank', '-')}" for x in top10]
                )
                lines.append(f"- {day.get('trade_date', '')}: {top_str or '暂无'}")

        if self._ok(kpl_list_data):
            lines.append("\n【近3日开盘啦题材热度TOP10】")
            for day in (kpl_list_data.get("daily_kpl_stats", []) or [])[:3]:
                top_theme = day.get("top_themes", [])[:10]
                top_str = "、".join(
                    [f"{x.get('theme', '')}({x.get('count', 0)})" for x in top_theme if x.get("theme")]
                )
                lines.append(f"- {day.get('trade_date', '')}: {top_str or '暂无'}")

        if self._ok(p1_signal_data):
            lines.append("\n【P1资金确认（概念/行业）】")
            concept_top = (p1_signal_data.get("concept_flow_top", []) or [])[:10]
            industry_top = (p1_signal_data.get("industry_flow_top", []) or [])[:10]
            youzi_top = (p1_signal_data.get("youzi_profile_top", []) or [])[:10]
            concept_text = "、".join([f"{x.get('concept', '')}({x.get('flow_score', 0)})" for x in concept_top if x.get("concept")])
            industry_text = "、".join([f"{x.get('industry', '')}({x.get('flow_score', 0)})" for x in industry_top if x.get("industry")])
            youzi_text = "、".join(
                [
                    f"{x.get('hm_name', '')}"
                    f"{'/' + str(x.get('style', '')) if x.get('style') else ''}"
                    f"({x.get('profile_score', 0)})"
                    for x in youzi_top
                    if x.get("hm_name")
                ]
            )
            lines.append(f"- 概念净流入TOP: {concept_text or '暂无'}")
            lines.append(f"- 行业净流入TOP: {industry_text or '暂无'}")
            lines.append(f"- 活跃游资画像TOP: {youzi_text or '暂无'}")

        return "\n".join(lines)

    def _build_stock_signal_context(
        self,
        summary: Dict[str, Any],
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = [
            self._build_brief_signal_context(
                concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
            )
        ]
        lines.append("\n【个股级热度信号（用于筛选次日关注股）】")

        step_map = (
            (limit_step_data.get("stock_step_heat_map", {}) or {})
            if self._ok(limit_step_data)
            else {}
        )
        hot_map = (
            (ths_hot_data.get("stock_hot_map", {}) or {})
            if self._ok(ths_hot_data)
            else {}
        )
        kpl_map = (
            (kpl_list_data.get("stock_kpl_map", {}) or {})
            if self._ok(kpl_list_data)
            else {}
        )
        p1_map = (
            (p1_signal_data.get("stock_signal_map", {}) or {})
            if self._ok(p1_signal_data)
            else {}
        )
        concept_strength_map = (
            (concept_rotation_data.get("concept_strength_map", {}) or {})
            if self._ok(concept_rotation_data)
            else {}
        )
        if concept_strength_map:
            top_concepts = list(concept_strength_map.items())[:5]
            lines.append(
                "- 主线强度TOP5："
                + "、".join([f"{name}({score})" for name, score in top_concepts])
            )

        top_stocks = summary.get("top_stocks", []) or []
        candidates = []
        for item in top_stocks[:20]:
            code = self._normalize_code(item.get("code", ""))
            if not code:
                continue
            step = step_map.get(code, {})
            hot = hot_map.get(code, {})
            kpl = kpl_map.get(code, {})
            p1 = p1_map.get(code, {})
            if not step and not hot and not kpl and not p1:
                continue
            combined = (
                float(step.get("score", 0.0) or 0.0)
                + float(hot.get("score", 0.0) or 0.0)
                + float(kpl.get("score", 0.0) or 0.0)
                + float(p1.get("score", 0.0) or 0.0)
            )
            candidates.append(
                {
                    "name": item.get("name", ""),
                    "code": code,
                    "combined": round(combined, 2),
                    "step_score": step.get("score", 0.0),
                    "max_step": step.get("max_step", 0),
                    "advance_days": step.get("advance_days", 0),
                    "hot_score": hot.get("score", 0.0),
                    "best_rank": hot.get("best_rank", "-"),
                    "hot_days": hot.get("appear_days", 0),
                    "kpl_score": kpl.get("score", 0.0),
                    "kpl_best_rank": kpl.get("best_rank", "-"),
                    "kpl_themes": (kpl.get("themes", []) or [])[:3],
                    "p1_score": p1.get("score", 0.0),
                    "inst_net_amt": p1.get("inst_net_amt", 0.0),
                    "main_net_amt_avg": p1.get("main_net_amt_avg", 0.0),
                    "seal_quality": p1.get("limit_quality_score", 0.0),
                    "youzi_signal": p1.get("youzi_signal_score", 0.0),
                    "top_hm_name": p1.get("top_hm_name", ""),
                    "top_hm_style": p1.get("top_hm_style", ""),
                }
            )

        candidates.sort(key=lambda x: x["combined"], reverse=True)
        if not candidates:
            lines.append("- 龙虎榜前20股票中暂无可用的连板/热榜映射数据")
        else:
            for idx, item in enumerate(candidates[:12], 1):
                best_rank_text = f"TOP{item['best_rank']}" if str(item["best_rank"]) not in ("-", "0", "None") else "-"
                kpl_rank_text = f"TOP{item['kpl_best_rank']}" if str(item["kpl_best_rank"]) not in ("-", "0", "None") else "-"
                lines.append(
                    f"{idx}. {item['name']}({item['code']}) | 信号总分 {item['combined']} "
                    f"(连板{item['step_score']}, 热榜{item['hot_score']}, KPL{item['kpl_score']}, P1{item['p1_score']}) | "
                    f"连板高度 {item['max_step']} | 晋级天数 {item['advance_days']} | "
                    f"热榜最佳 {best_rank_text} | 在榜天数 {item['hot_days']} | "
                    f"KPL最佳 {kpl_rank_text} | KPL题材 {'、'.join(item['kpl_themes']) or '暂无'} | "
                    f"机构净额 {item['inst_net_amt']} | 主力净流均值 {item['main_net_amt_avg']} | "
                    f"封板质量 {item['seal_quality']} | "
                    f"游资信号 {item['youzi_signal']} | "
                    f"游资画像 {(item['top_hm_name'] + '/' + item['top_hm_style']).strip('/') or '暂无'}"
                )

        return "\n".join(lines)

    def _build_risk_signal_context(
        self,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = [
            self._build_brief_signal_context(
                concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
            )
        ]
        risk_flags: List[str] = []

        if self._ok(limit_step_data):
            step_summary = limit_step_data.get("market_heat_summary", {}) or {}
            avg_max = float(step_summary.get("avg_max_step", 0) or 0)
            avg_adv = float(step_summary.get("avg_advance_count", step_summary.get("avg_promotion_count", 0)) or 0)
            latest_adv = float(step_summary.get("latest_advance_count", 0) or 0)
            if avg_max >= 5:
                risk_flags.append("高位连板拥挤（高标情绪过热）")
            if avg_adv > 0 and latest_adv < avg_adv * 0.7:
                risk_flags.append("最新晋级个数显著回落（高位分歧/退潮风险）")

        if self._ok(concept_rotation_data):
            rotation_summary = concept_rotation_data.get("rotation_summary", {}) or {}
            flow = str(rotation_summary.get("capital_flow_signal", ""))
            if "高频轮动" in flow:
                risk_flags.append("概念轮动速度快（主线持续性偏弱）")

        if self._ok(ths_hot_data):
            hot_map = ths_hot_data.get("stock_hot_map", {}) or {}
            crowded = sum(
                1
                for v in hot_map.values()
                if (int(v.get("best_rank", 9999) or 9999) <= 30 and int(v.get("appear_days", 0) or 0) >= 2)
            )
            if crowded >= 15:
                risk_flags.append("热榜高位拥挤度较高（一致性交易风险）")

        if self._ok(kpl_list_data):
            kpl_map = kpl_list_data.get("stock_kpl_map", {}) or {}
            kpl_crowded = sum(
                1
                for v in kpl_map.values()
                if (int(v.get("best_rank", 9999) or 9999) <= 20 and int(v.get("appear_days", 0) or 0) >= 2)
            )
            if kpl_crowded >= 12:
                risk_flags.append("开盘啦榜单高位拥挤（题材一致性过高）")

        if self._ok(p1_signal_data):
            p1_map = p1_signal_data.get("stock_signal_map", {}) or {}
            weak_seal = sum(1 for v in p1_map.values() if float(v.get("limit_quality_score", 0.0) or 0.0) < 0.45)
            money_out = sum(1 for v in p1_map.values() if float(v.get("main_net_amt_sum", 0.0) or 0.0) < 0)
            youzi_out = sum(1 for v in p1_map.values() if float(v.get("youzi_net_amt_sum", 0.0) or 0.0) < 0)
            if weak_seal >= 8:
                risk_flags.append("封板质量偏弱股票偏多（炸板/回封失败风险）")
            if money_out >= 8:
                risk_flags.append("主力净流出股票偏多（次日承接风险）")
            if youzi_out >= 8:
                risk_flags.append("游资净卖出股票偏多（短线兑现压力上升）")

        if isinstance(kline_9d_data, dict) and kline_9d_data.get("data_success"):
            trend_summary = kline_9d_data.get("trend_summary", {}) or {}
            high_shake = int(trend_summary.get("high_shake_count", 0) or 0)
            decline = int(trend_summary.get("decline_count", 0) or 0)
            tracked = int(trend_summary.get("tracked_stocks", 0) or 0)
            if tracked > 0 and (high_shake + decline) >= max(5, int(tracked * 0.35)):
                risk_flags.append("9日K线显示高位震荡/退潮占比偏高（次日分歧风险）")

        lines.append("\n【风险优先信号】")
        if risk_flags:
            for idx, flag in enumerate(risk_flags, 1):
                lines.append(f"{idx}. {flag}")
        else:
            lines.append("- 暂无明显的拥挤/退潮风险信号，仍需结合盘中确认")

        return "\n".join(lines)

    def _build_chief_signal_context(
        self,
        summary: Dict[str, Any],
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> str:
        lines = [
            self._build_brief_signal_context(
                concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale
            )
        ]

        step_map = (
            (limit_step_data.get("stock_step_heat_map", {}) or {})
            if self._ok(limit_step_data)
            else {}
        )
        hot_map = (
            (ths_hot_data.get("stock_hot_map", {}) or {})
            if self._ok(ths_hot_data)
            else {}
        )
        kpl_map = (
            (kpl_list_data.get("stock_kpl_map", {}) or {})
            if self._ok(kpl_list_data)
            else {}
        )
        p1_map = (
            (p1_signal_data.get("stock_signal_map", {}) or {})
            if self._ok(p1_signal_data)
            else {}
        )
        lu_desc_map = {}
        for item in (summary.get("top_stock_lu_desc", []) or []):
            code = self._normalize_code(item.get("code", ""))
            if code:
                lu_desc_map[code] = item
        signal_top = []
        for item in (summary.get("top_stocks", []) or [])[:20]:
            code = self._normalize_code(item.get("code", ""))
            if not code:
                continue
            step_score = float((step_map.get(code, {}) or {}).get("score", 0.0) or 0.0)
            hot_score = float((hot_map.get(code, {}) or {}).get("score", 0.0) or 0.0)
            kpl_score = float((kpl_map.get(code, {}) or {}).get("score", 0.0) or 0.0)
            p1_score = float((p1_map.get(code, {}) or {}).get("score", 0.0) or 0.0)
            total = step_score + hot_score + kpl_score + p1_score
            if total <= 0:
                continue
            signal_top.append(
                {
                    "code": code,
                    "name": item.get("name", ""),
                    "signal_total": round(total, 2),
                    "step_score": round(step_score, 2),
                    "hot_score": round(hot_score, 2),
                    "kpl_score": round(kpl_score, 2),
                    "p1_score": round(p1_score, 2),
                    "lu_desc_top": (lu_desc_map.get(code, {}) or {}).get("lu_desc_top", [])[:2],
                    "theme_top": (lu_desc_map.get(code, {}) or {}).get("theme_top", [])[:3],
                }
            )
        signal_top.sort(key=lambda x: x["signal_total"], reverse=True)
        lines.append("\n【高热度候选（连板+热榜）TOP8】")
        if signal_top:
            for idx, item in enumerate(signal_top[:8], 1):
                lines.append(
                    f"{idx}. {item['name']}({item['code']}): 信号总分{item['signal_total']} "
                    f"(连板{item['step_score']} + 热榜{item['hot_score']} + KPL{item['kpl_score']} + P1{item['p1_score']}) | "
                    f"题材线索 {'；'.join(item['lu_desc_top']) or '暂无'} | "
                    f"题材词 {'、'.join(item['theme_top']) or '暂无'}"
                )
        else:
            lines.append("- 暂无可用的高热度候选映射数据")

        return "\n".join(lines)
    
    def youzi_behavior_analyst(
        self,
        longhubang_data: str,
        summary: Dict,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        游资行为分析师 - 分析游资操作特征和意图
        
        职责：
        - 识别活跃游资及其操作风格
        - 分析游资席位的进出特征
        - 研判游资对个股的态度
        """
        self.logger.info("🎯 游资行为分析师正在分析...")
        time.sleep(1)
        
        # 构建游资统计信息
        youzi_info = ""
        if summary.get('top_youzi'):
            youzi_info = "\n【活跃游资统计】\n"
            for idx, (name, amount) in enumerate(list(summary['top_youzi'].items())[:15], 1):
                youzi_info += f"{idx}. {name}: 净流入 {amount:,.2f} 元\n"
        youzi_signal_context = self._build_brief_signal_context(
            concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
        )
        
        prompt = build_youzi_behavior_prompt(summary, youzi_info, youzi_signal_context, longhubang_data)
        
        messages = [
            {"role": "system", "content": "你是一名资深的游资研究专家，擅长从龙虎榜数据中洞察游资意图和操作手法。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self._run_agent_call("游资行为分析师", messages, max_tokens=4000)
        
        self.logger.info("  ✓ 游资行为分析师分析完成")
        
        return {
            "agent_name": "游资行为分析师",
            "agent_role": "分析游资操作特征、意图和目标股票",
            "analysis": analysis,
            "focus_areas": ["游资画像", "操作风格", "目标股票", "进出节奏", "题材偏好"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def stock_potential_analyst(
        self,
        longhubang_data: str,
        summary: Dict,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        个股潜力分析师 - 从龙虎榜数据挖掘潜力股
        
        职责：
        - 分析上榜股票的资金动向
        - 评估股票的上涨潜力
        - 识别次日大概率上涨的股票
        """
        self.logger.info("📈 个股潜力分析师正在分析...")
        time.sleep(1)
        
        # 构建股票统计信息
        stock_info = ""
        if summary.get('top_stocks'):
            stock_info = "\n【热门股票统计】\n"
            for idx, stock in enumerate(summary['top_stocks'][:20], 1):
                stock_info += f"{idx}. {stock['name']}({stock['code']}): 净流入 {stock['net_inflow']:,.2f} 元\n"
        stock_signal_context = self._build_stock_signal_context(
            summary, concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
        )
        theme_whitelist_context = self._build_theme_whitelist_context(summary or {})
        stock_theme_binding_context = self._build_stock_theme_binding_context(summary or {})
        
        prompt = build_stock_potential_prompt(
            summary,
            stock_info,
            stock_signal_context,
            theme_whitelist_context,
            stock_theme_binding_context,
            longhubang_data,
        )
        
        messages = [
            {"role": "system", "content": "你是一名资深的个股研究专家和短线交易高手，擅长从龙虎榜中挖掘短期爆发股。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self._run_agent_call("个股潜力分析师", messages, max_tokens=4000)
        
        self.logger.info("  ✓ 个股潜力分析师分析完成")
        
        return {
            "agent_name": "个股潜力分析师",
            "agent_role": "挖掘次日大概率上涨的潜力股票",
            "analysis": analysis,
            "focus_areas": ["潜力股挖掘", "资金流向", "技术形态", "题材概念", "操作策略"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def theme_tracker_analyst(
        self,
        longhubang_data: str,
        summary: Dict,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        题材追踪分析师 - 分析龙虎榜中的热点题材
        
        职责：
        - 识别当前热点题材和概念
        - 分析题材的炒作周期
        - 预判题材的持续性
        """
        self.logger.info("🔥 题材追踪分析师正在分析...")
        time.sleep(1)
        
        # 构建概念统计信息
        concept_info = ""
        if summary.get('hot_concepts'):
            concept_info = "\n【热门概念统计】\n"
            for idx, (concept, count) in enumerate(list(summary['hot_concepts'].items())[:20], 1):
                concept_info += f"{idx}. {concept}: 出现 {count} 次\n"
        theme_signal_context = self._build_theme_signal_context(
            concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
        )
        theme_whitelist_context = self._build_theme_whitelist_context(summary or {})
        stock_theme_binding_context = self._build_stock_theme_binding_context(summary or {})
        
        prompt = build_theme_tracker_prompt(
            summary,
            concept_info,
            theme_signal_context,
            theme_whitelist_context,
            stock_theme_binding_context,
            longhubang_data,
        )
        
        messages = [
            {"role": "system", "content": "你是一名资深的题材研究专家，擅长从龙虎榜数据中捕捉题材热点和投资机会。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self._run_agent_call("题材追踪分析师", messages, max_tokens=4000)
        
        self.logger.info("  ✓ 题材追踪分析师分析完成")
        
        return {
            "agent_name": "题材追踪分析师",
            "agent_role": "识别热点题材，分析炒作周期，预判轮动方向",
            "analysis": analysis,
            "focus_areas": ["热点题材", "炒作周期", "龙头梯队", "题材轮动", "风险评估"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def risk_control_specialist(
        self,
        longhubang_data: str,
        summary: Dict,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        fixed_candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        风险控制专家 - 识别龙虎榜中的风险信号
        
        职责：
        - 识别高风险股票和陷阱
        - 分析游资出货信号
        - 提供风险管理建议
        """
        self.logger.info("⚠️ 风险控制专家正在分析...")
        time.sleep(1)
        risk_signal_context = self._build_risk_signal_context(
            concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, fixed_candidate_stocks, market_style_context, sector_tier_map
        )
        
        prompt = build_risk_control_prompt(summary, risk_signal_context, longhubang_data)
        
        messages = [
            {"role": "system", "content": "你是一名资深的风险控制专家，擅长识别龙虎榜中的风险信号和资金陷阱。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self._run_agent_call("风险控制专家", messages, max_tokens=4000)
        
        self.logger.info("  ✓ 风险控制专家分析完成")
        
        return {
            "agent_name": "风险控制专家",
            "agent_role": "识别高风险股票、游资出货信号和市场陷阱",
            "analysis": analysis,
            "focus_areas": ["高风险股票", "出货信号", "资金陷阱", "题材风险", "风险管理"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def chief_strategist(
        self,
        all_analyses: List[Dict],
        summary: Optional[Dict[str, Any]] = None,
        concept_rotation_data: Optional[Dict[str, Any]] = None,
        limit_step_data: Optional[Dict[str, Any]] = None,
        ths_hot_data: Optional[Dict[str, Any]] = None,
        kpl_list_data: Optional[Dict[str, Any]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        trend_overlay_scale: float = 1.0,
        recommendation_count: int = 8,
        recommendation_limit_up_ratio: float = 0.4,
        enable_recommendation_quota: bool = True,
        candidate_quota_context: str = "",
        market_style_context: Optional[Dict[str, Any]] = None,
        sector_tier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        首席策略师 - 综合所有分析师的意见，给出最终投资建议
        
        职责：
        - 整合多维度分析结果
        - 给出最终推荐股票清单
        - 提供具体操作策略
        """
        self.logger.info("👔 首席策略师正在综合分析...")
        time.sleep(1)
        
        # 整合所有分析师的分析结果
        analyses_text = ""
        for analysis in all_analyses:
            analyses_text += f"\n{'='*60}\n"
            analyses_text += f"【{analysis['agent_name']}】分析报告\n"
            analyses_text += f"职责: {analysis['agent_role']}\n"
            analyses_text += f"{'='*60}\n"
            analyses_text += analysis['analysis'] + "\n"
        chief_signal_context = self._build_chief_signal_context(
            summary or {}, concept_rotation_data, limit_step_data, ths_hot_data, kpl_list_data, p1_signal_data, kline_9d_data, trend_overlay_scale, market_style_context, sector_tier_map
        )
        theme_whitelist_context = self._build_theme_whitelist_context(summary or {})
        stock_theme_binding_context = self._build_stock_theme_binding_context(summary or {})
        quota_enabled = bool(enable_recommendation_quota)
        quota_count = max(3, min(int(recommendation_count or 8), 12))
        quota_ratio = max(0.0, min(float(recommendation_limit_up_ratio or 0.4), 1.0))
        max_limit_like = int(quota_count * quota_ratio + 1e-9)
        
        prompt = build_chief_strategist_prompt(
            chief_signal_context,
            theme_whitelist_context,
            stock_theme_binding_context,
            candidate_quota_context,
            analyses_text,
            quota_enabled,
            quota_count,
            quota_ratio,
            max_limit_like,
        )
        
        messages = [
            {"role": "system", "content": "你是一名资深的首席投资策略师，擅长综合多维度分析，给出最优投资决策。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self._run_agent_call("首席策略师", messages, max_tokens=5000)
        
        self.logger.info("  ✓ 首席策略师分析完成")
        
        return {
            "agent_name": "首席策略师",
            "agent_role": "综合多维度分析，给出最终投资建议和推荐股票清单",
            "analysis": analysis,
            "focus_areas": ["综合研判", "推荐股票", "风险警示", "热点题材", "操作策略"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    def repair_recommendation_advice(
        self,
        recommended_stocks: List[Dict[str, Any]],
        chief_analysis: str = "",
    ) -> str:
        """
        二次补全推荐理由与建议字段（补全详细理由+确定性/买点/触发条件/持有周期）。
        返回Markdown表文本，供引擎解析回填。
        """
        items = []
        for x in (recommended_stocks or [])[:12]:
            code = self._normalize_code(x.get("code", ""))
            if not code:
                continue
            items.append(
                f"- {x.get('name', '')}({code}) | 理由: {x.get('reason', '')} | 当前: "
                f"确定性={x.get('confidence', '')}, 买入={x.get('buy_price', '')}, "
                f"触发={x.get('target_price', '')}, 持有={x.get('hold_period', '')}"
            )
        if not items:
            return ""

        prompt = build_repair_advice_prompt(items, chief_analysis)
        messages = [
            {"role": "system", "content": "你是资深短线交易策略顾问，擅长给出可执行建议。"},
            {"role": "user", "content": prompt},
        ]
        return self._run_agent_call("建议字段补全器", messages, max_tokens=1800)

    def repair_chief_recommendation_table(
        self,
        chief_analysis: str,
        candidate_quota_context: str,
        candidate_kline_context: str = "",
        recommendation_count: int = 8,
        recommendation_limit_up_ratio: float = 0.4,
    ) -> str:
        """
        当首席推荐表不满足分层配额时，触发一次AI重选，仅输出“次日重点推荐股票”Markdown表格。
        """
        rec_count = max(3, min(int(recommendation_count or 8), 12))
        ratio = max(0.0, min(float(recommendation_limit_up_ratio or 0.4), 1.0))
        max_limit_like = int(rec_count * ratio + 1e-9)
        min_non_limit_like = max(0, rec_count - max_limit_like)

        prompt = build_repair_table_prompt(
            chief_analysis,
            candidate_quota_context,
            candidate_kline_context,
            recommendation_count,
            recommendation_limit_up_ratio,
        )
        messages = [
            {"role": "system", "content": "你是资深首席投资策略师，擅长在约束条件下给出可执行推荐表。"},
            {"role": "user", "content": prompt},
        ]
        return self._run_agent_call("首席推荐表修正器", messages, max_tokens=2200)


# 测试函数
if __name__ == "__main__":
    print("=" * 60)
    print("测试智瞰龙虎AI分析师系统")
    print("=" * 60)
    
    # 创建模拟数据
    test_summary = {
        'total_records': 150,
        'total_stocks': 50,
        'total_youzi': 30,
        'total_buy_amount': 500000000,
        'total_sell_amount': 200000000,
        'total_net_inflow': 300000000,
        'top_youzi': {
            '92科比': 14455321,
            '赵老哥': 12000000,
            '章盟主': 10000000
        },
        'top_stocks': [
            {'code': '001337', 'name': '四川黄金', 'net_inflow': 14455321}
        ],
        'hot_concepts': {
            '黄金概念': 10,
            '新能源': 8,
            'ChatGPT': 7
        }
    }
    
    test_data = """
【详细交易记录 TOP50】
92科比 | 四川黄金(001337) | 买入:14,470,401 卖出:15,080 净流入:14,455,321 | 日期:2023-03-22
"""
    
    agents = LonghubangAgents()
    
    # 测试游资行为分析师
    print("\n测试游资行为分析师...")
    result = agents.youzi_behavior_analyst(test_data, test_summary)
    print(f"分析师: {result['agent_name']}")
    print(f"分析内容长度: {len(result['analysis'])} 字符")
