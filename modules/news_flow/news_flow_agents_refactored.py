"""
新闻流量智能分析代理模块（重构版）
使用统一的 AI 客户端基础设施
"""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from infrastructure.ai.base_ai_client import DeepSeekAIClient
from prompts.news_flow_prompts import (
    build_sector_impact_prompt,
    build_stock_recommend_prompt,
    build_risk_assess_prompt,
    build_investment_advisor_prompt,
    build_sector_deep_prompt,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NewsFlowAgentsRefactored:
    """新闻流量智能分析代理（重构版）"""

    def __init__(self, model: str = None):
        """
        初始化代理

        Args:
            model: 使用的模型，默认从 .env 的 DEFAULT_MODEL_NAME 读取
        """
        import config
        self.model = model or config.DEFAULT_MODEL_NAME
        self.ai_client = DeepSeekAIClient(model=self.model)

    def is_available(self) -> bool:
        """检查AI是否可用"""
        return self.ai_client.is_available()

    def sector_impact_agent(
        self, hot_topics: List[Dict], stock_news: List[Dict], flow_data: Dict = None
    ) -> Dict:
        """
        板块影响分析代理

        分析热点可能影响的板块

        Returns:
            {
                'affected_sectors': List[Dict],
                'analysis': str,
                'success': bool,
            }
        """
        # 准备数据
        topics_text = "\n".join(
            [
                f"- {t['topic']} (热度:{t.get('heat', 0)}, 跨{t.get('cross_platform', 0)}平台)"
                for t in hot_topics[:20]
            ]
        )

        news_text = "\n".join(
            [
                f"- [{n.get('platform_name', '')}] {n.get('title', '')}"
                for n in stock_news[:15]
            ]
        )

        flow_info = ""
        if flow_data:
            flow_info = f"""
当前流量状态:
- 流量得分: {flow_data.get('total_score', 'N/A')}/1000
- 流量等级: {flow_data.get('level', 'N/A')}
- 社交媒体热度: {flow_data.get('social_score', 'N/A')}
- 财经平台热度: {flow_data.get('finance_score', 'N/A')}
"""

        prompt = build_sector_impact_prompt(
            topics_text=topics_text,
            news_text=news_text,
            flow_info=flow_info,
        )

        # 使用统一的 AI 客户端
        result = self.ai_client.call_json(
            prompt, fallback=lambda: self._fallback_sector_analysis(hot_topics, stock_news)
        )

        if result and not result.get("error"):
            return {
                "hot_themes": result.get("hot_themes", []),
                "affected_sectors": result.get("benefited_sectors", [])
                + result.get("damaged_sectors", []),
                "benefited_sectors": result.get("benefited_sectors", []),
                "damaged_sectors": result.get("damaged_sectors", []),
                "opportunity_assessment": result.get("opportunity_assessment", ""),
                "trading_suggestion": result.get("trading_suggestion", ""),
                "key_points": result.get("key_points", []),
                "success": True,
            }
        else:
            return self._fallback_sector_analysis(hot_topics, stock_news)

    def stock_recommend_agent(
        self, hot_sectors: List[Dict], flow_stage: str, sentiment_class: str
    ) -> Dict:
        """
        股票推荐代理

        基于热门板块和市场状态推荐股票
        """
        sectors_text = "\n".join(
            [
                f"- {s.get('name', '')}：{s.get('impact', '利好')}，置信度{s.get('confidence', 50)}%\n  原因：{s.get('reason', '')}\n  龙头特征：{s.get('leader_characteristics', 'N/A')}"
                for s in hot_sectors[:5]
            ]
        )

        prompt = build_stock_recommend_prompt(
            sectors_text=sectors_text,
            flow_stage=flow_stage,
            sentiment_class=sentiment_class,
        )

        result = self.ai_client.call_json(
            prompt, fallback=lambda: self._fallback_stock_recommend(hot_sectors)
        )

        if result and not result.get("error"):
            return {
                "recommended_stocks": result.get("recommended_stocks", []),
                "strategy": result.get("strategy", ""),
                "timing_advice": result.get("timing_advice", ""),
                "risk_control": result.get("risk_control", ""),
                "success": True,
            }
        else:
            return self._fallback_stock_recommend(hot_sectors)

    def risk_assess_agent(
        self, hot_topics: List[Dict], flow_data: Dict, sentiment_data: Dict
    ) -> Dict:
        """风险评估代理"""
        topics_text = "\n".join(
            [f"- {t['topic']} (热度:{t.get('heat', 0)})" for t in hot_topics[:15]]
        )

        prompt = build_risk_assess_prompt(
            topics_text=topics_text,
            flow_data=flow_data,
            sentiment_data=sentiment_data,
        )

        result = self.ai_client.call_json(
            prompt, fallback=lambda: self._fallback_risk_assess()
        )

        if result and not result.get("error"):
            return {
                "risk_level": result.get("risk_level", "中"),
                "risk_factors": result.get("risk_factors", []),
                "warning_signals": result.get("warning_signals", []),
                "suggestions": result.get("suggestions", []),
                "success": True,
            }
        else:
            return self._fallback_risk_assess()

    def investment_advisor_agent(
        self,
        sector_analysis: Dict,
        stock_recommendations: Dict,
        risk_assessment: Dict,
    ) -> Dict:
        """投资建议代理"""
        prompt = build_investment_advisor_prompt(
            sector_analysis=sector_analysis,
            stock_recommendations=stock_recommendations,
            risk_assessment=risk_assessment,
        )

        result = self.ai_client.call_json(
            prompt, fallback=lambda: self._fallback_investment_advice()
        )

        if result and not result.get("error"):
            return {
                "overall_strategy": result.get("overall_strategy", ""),
                "position_advice": result.get("position_advice", ""),
                "entry_timing": result.get("entry_timing", ""),
                "exit_strategy": result.get("exit_strategy", ""),
                "key_reminders": result.get("key_reminders", []),
                "success": True,
            }
        else:
            return self._fallback_investment_advice()

    # 降级方案
    def _fallback_sector_analysis(
        self, hot_topics: List[Dict], stock_news: List[Dict]
    ) -> Dict:
        """板块分析降级方案"""
        logger.warning("使用板块分析降级方案")
        return {
            "hot_themes": [t.get("topic", "") for t in hot_topics[:5]],
            "affected_sectors": [],
            "benefited_sectors": [],
            "damaged_sectors": [],
            "opportunity_assessment": "AI 服务暂时不可用，请稍后重试",
            "trading_suggestion": "建议观望",
            "key_points": [],
            "success": False,
        }

    def _fallback_stock_recommend(self, hot_sectors: List[Dict]) -> Dict:
        """股票推荐降级方案"""
        logger.warning("使用股票推荐降级方案")
        return {
            "recommended_stocks": [],
            "strategy": "AI 服务暂时不可用，请稍后重试",
            "timing_advice": "建议观望",
            "risk_control": "严格控制仓位",
            "success": False,
        }

    def _fallback_risk_assess(self) -> Dict:
        """风险评估降级方案"""
        logger.warning("使用风险评估降级方案")
        return {
            "risk_level": "中",
            "risk_factors": ["AI 服务暂时不可用"],
            "warning_signals": [],
            "suggestions": ["建议谨慎操作"],
            "success": False,
        }

    def _fallback_investment_advice(self) -> Dict:
        """投资建议降级方案"""
        logger.warning("使用投资建议降级方案")
        return {
            "overall_strategy": "AI 服务暂时不可用，请稍后重试",
            "position_advice": "建议轻仓或观望",
            "entry_timing": "等待明确信号",
            "exit_strategy": "设置止损",
            "key_reminders": ["市场有风险，投资需谨慎"],
            "success": False,
        }
