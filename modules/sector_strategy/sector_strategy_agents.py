"""
智策AI智能体分析集群
包含四个专业分析师智能体
"""

from infrastructure.ai.deepseek_client import DeepSeekClient
from typing import Dict, Any
import time
import config
from prompts.sector_strategy_prompts import (
    build_macro_strategist_prompt,
    build_sector_diagnostician_prompt,
    build_fund_flow_analyst_prompt,
    build_market_sentiment_decoder_prompt,
)


class SectorStrategyAgents:
    """板块策略AI智能体集合"""
    
    def __init__(self, model=None):
        self.model = model or config.DEFAULT_MODEL_NAME
        self.deepseek_client = DeepSeekClient(model=self.model)
        print(f"[智策] AI智能体系统初始化 (模型: {self.model})")
    
    def macro_strategist_agent(self, market_data: Dict, news_data: list) -> Dict[str, Any]:
        """
        宏观策略师 - 分析宏观经济和新闻对板块的影响
        
        职责：
        - 分析国际国内新闻和宏观经济数据
        - 判断对整体市场和不同板块的潜在影响
        - 识别政策导向和宏观趋势
        """
        print("🌐 宏观策略师正在分析...")
        time.sleep(1)
        
        # 构建新闻摘要
        news_summary = ""
        if news_data:
            news_summary = "\n【重要财经新闻】\n"
            for idx, news in enumerate(news_data[:30], 1):
                news_summary += f"{idx}. [{news.get('publish_time', '')}] {news.get('title', '')}\n"
                if news.get('content'):
                    news_summary += f"   摘要: {news['content'][:200]}...\n"
        
        # 构建市场概况
        market_summary = ""
        if market_data:
            market_summary = f"""
【市场概况】
大盘指数:
"""
            if market_data.get("sh_index"):
                sh = market_data["sh_index"]
                market_summary += f"  上证指数: {sh['close']} ({sh['change_pct']:+.2f}%)\n"
            if market_data.get("sz_index"):
                sz = market_data["sz_index"]
                market_summary += f"  深证成指: {sz['close']} ({sz['change_pct']:+.2f}%)\n"
            if market_data.get("cyb_index"):
                cyb = market_data["cyb_index"]
                market_summary += f"  创业板指: {cyb['close']} ({cyb['change_pct']:+.2f}%)\n"
            
            if market_data.get("total_stocks"):
                market_summary += f"""
市场涨跌统计:
  上涨: {market_data['up_count']} ({market_data['up_ratio']:.1f}%)
  下跌: {market_data['down_count']}
  涨停: {market_data['limit_up']} | 跌停: {market_data['limit_down']}
"""
        
        prompt = build_macro_strategist_prompt(market_summary, news_summary)
        
        messages = [
            {"role": "system", "content": "你是一名资深的宏观策略分析师，擅长从宏观经济、政策和新闻事件中把握市场脉搏。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self.deepseek_client.call_api(messages, max_tokens=4000)
        
        print("  ✓ 宏观策略师分析完成")
        
        return {
            "agent_name": "宏观策略师",
            "agent_role": "分析宏观经济、政策导向、新闻事件对市场和板块的影响",
            "analysis": analysis,
            "focus_areas": ["宏观经济", "政策解读", "新闻事件", "市场情绪", "行业轮动"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def sector_diagnostician_agent(self, sectors_data: Dict, concepts_data: Dict, market_data: Dict) -> Dict[str, Any]:
        """
        板块诊断师 - 分析板块的走势、估值和基本面
        
        职责：
        - 深入分析特定板块的历史走势
        - 评估板块的估值水平
        - 分析板块的成长性和基本面因素
        """
        print("📊 板块诊断师正在分析...")
        time.sleep(1)
        
        # 构建行业板块数据
        sector_summary = ""
        if sectors_data:
            sorted_sectors = sorted(sectors_data.items(), key=lambda x: x[1]["change_pct"], reverse=True)
            
            sector_summary = f"""
【行业板块表现】(共 {len(sectors_data)} 个板块)

涨幅榜 TOP15:
"""
            for idx, (name, info) in enumerate(sorted_sectors[:15], 1):
                sector_summary += f"{idx}. {name}: {info['change_pct']:+.2f}% | 换手率: {info['turnover']:.2f}% | 领涨股: {info['top_stock']} ({info['top_stock_change']:+.2f}%) | 涨跌家数: {info['up_count']}/{info['down_count']}\n"
            
            sector_summary += f"""
跌幅榜 TOP10:
"""
            for idx, (name, info) in enumerate(sorted_sectors[-10:], 1):
                sector_summary += f"{idx}. {name}: {info['change_pct']:+.2f}% | 换手率: {info['turnover']:.2f}% | 领跌股: {info['top_stock']} ({info['top_stock_change']:+.2f}%) | 涨跌家数: {info['up_count']}/{info['down_count']}\n"
        
        # 构建概念板块数据
        concept_summary = ""
        if concepts_data:
            sorted_concepts = sorted(concepts_data.items(), key=lambda x: x[1]["change_pct"], reverse=True)
            
            concept_summary = f"""
【概念板块表现】(共 {len(concepts_data)} 个板块)

热门概念 TOP15:
"""
            for idx, (name, info) in enumerate(sorted_concepts[:15], 1):
                concept_summary += f"{idx}. {name}: {info['change_pct']:+.2f}% | 换手率: {info['turnover']:.2f}% | 领涨股: {info['top_stock']} ({info['top_stock_change']:+.2f}%)\n"
        
        prompt = build_sector_diagnostician_prompt(
            self._format_market_overview(market_data),
            sector_summary,
            concept_summary,
        )
        
        messages = [
            {"role": "system", "content": "你是一名资深的板块分析师，擅长板块趋势判断和投资价值评估。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self.deepseek_client.call_api(messages, max_tokens=4000)
        
        print("  ✓ 板块诊断师分析完成")
        
        return {
            "agent_name": "板块诊断师",
            "agent_role": "深入分析板块走势、估值水平、基本面因素和成长性",
            "analysis": analysis,
            "focus_areas": ["板块走势", "估值分析", "基本面", "技术形态", "板块轮动"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def fund_flow_analyst_agent(self, fund_flow_data: Dict, north_flow_data: Dict, sectors_data: Dict) -> Dict[str, Any]:
        """
        资金流向分析师 - 分析板块资金流向和主力行为
        
        职责：
        - 实时跟踪主力资金在板块间的流动
        - 分析北向资金的板块偏好
        - 判断资金进攻或撤离的方向
        """
        print("💰 资金流向分析师正在分析...")
        time.sleep(1)
        
        # 构建资金流向数据
        fund_flow_summary = ""
        if fund_flow_data and fund_flow_data.get("today"):
            flow_list = fund_flow_data["today"]
            
            # 净流入前15
            sorted_inflow = sorted(flow_list, key=lambda x: x["main_net_inflow"], reverse=True)
            fund_flow_summary = f"""
【板块资金流向】(更新时间: {fund_flow_data.get('update_time', 'N/A')})

主力资金净流入 TOP15:
"""
            for idx, item in enumerate(sorted_inflow[:15], 1):
                fund_flow_summary += f"{idx}. {item['sector']}: {item['main_net_inflow']:.2f}万 ({item['main_net_inflow_pct']:+.2f}%) | 涨跌: {item['change_pct']:+.2f}% | 超大单: {item['super_large_net_inflow']:.2f}万\n"
            
            # 净流出前10
            sorted_outflow = sorted(flow_list, key=lambda x: x["main_net_inflow"])
            fund_flow_summary += f"""
主力资金净流出 TOP10:
"""
            for idx, item in enumerate(sorted_outflow[:10], 1):
                fund_flow_summary += f"{idx}. {item['sector']}: {item['main_net_inflow']:.2f}万 ({item['main_net_inflow_pct']:+.2f}%) | 涨跌: {item['change_pct']:+.2f}%\n"
        
        # 构建北向资金数据
        north_summary = ""
        if north_flow_data:
            north_summary = f"""
【北向资金】
日期: {north_flow_data.get('date', 'N/A')}
今日北向资金净流入: {north_flow_data.get('north_net_inflow', 0):.2f} 万元
  沪股通净流入: {north_flow_data.get('hgt_net_inflow', 0):.2f} 万元
  深股通净流入: {north_flow_data.get('sgt_net_inflow', 0):.2f} 万元
"""
            if north_flow_data.get('history'):
                north_summary += "\n近10日北向资金流向:\n"
                for item in north_flow_data['history'][:10]:
                    north_summary += f"  {item['date']}: {item['net_inflow']:.2f}万\n"
        
        prompt = build_fund_flow_analyst_prompt(fund_flow_summary, north_summary)
        
        messages = [
            {"role": "system", "content": "你是一名资深的资金流向分析师，擅长从资金数据中洞察主力意图和市场趋势。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self.deepseek_client.call_api(messages, max_tokens=4000)
        
        print("  ✓ 资金流向分析师分析完成")
        
        return {
            "agent_name": "资金流向分析师",
            "agent_role": "跟踪板块资金流向，分析主力行为和资金轮动",
            "analysis": analysis,
            "focus_areas": ["资金流向", "主力行为", "北向资金", "板块轮动", "量价配合"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def market_sentiment_decoder_agent(self, market_data: Dict, sectors_data: Dict, concepts_data: Dict) -> Dict[str, Any]:
        """
        市场情绪解码员 - 从多维度解读市场情绪
        
        职责：
        - 量化市场情绪指标
        - 识别过度乐观或恐慌信号
        - 评估板块热度和市场关注度
        """
        print("📈 市场情绪解码员正在分析...")
        time.sleep(1)
        
        # 构建市场情绪指标
        sentiment_summary = ""
        if market_data:
            sentiment_summary = f"""
【市场情绪指标】

涨跌统计:
  总股票数: {market_data.get('total_stocks', 0)}
  上涨股票: {market_data.get('up_count', 0)} ({market_data.get('up_ratio', 0):.1f}%)
  下跌股票: {market_data.get('down_count', 0)}
  涨停数: {market_data.get('limit_up', 0)}
  跌停数: {market_data.get('limit_down', 0)}

大盘表现:
"""
            if market_data.get("sh_index"):
                sh = market_data["sh_index"]
                sentiment_summary += f"  上证指数: {sh['close']} ({sh['change_pct']:+.2f}%)\n"
            if market_data.get("sz_index"):
                sz = market_data["sz_index"]
                sentiment_summary += f"  深证成指: {sz['close']} ({sz['change_pct']:+.2f}%)\n"
            if market_data.get("cyb_index"):
                cyb = market_data["cyb_index"]
                sentiment_summary += f"  创业板指: {cyb['close']} ({cyb['change_pct']:+.2f}%)\n"
        
        # 板块热度分析
        hot_sectors = ""
        if sectors_data:
            sorted_sectors = sorted(sectors_data.items(), key=lambda x: abs(x[1]["change_pct"]), reverse=True)
            hot_sectors = f"""
【板块热度排行】(按涨跌幅绝对值排序)

最活跃板块 TOP10:
"""
            for idx, (name, info) in enumerate(sorted_sectors[:10], 1):
                hot_sectors += f"{idx}. {name}: {info['change_pct']:+.2f}% | 换手率: {info['turnover']:.2f}% | 涨跌家数: {info['up_count']}/{info['down_count']}\n"
        
        # 概念热度
        hot_concepts = ""
        if concepts_data:
            sorted_concepts = sorted(concepts_data.items(), key=lambda x: abs(x[1]["change_pct"]), reverse=True)
            hot_concepts = f"""
【概念热度排行】

最热概念 TOP10:
"""
            for idx, (name, info) in enumerate(sorted_concepts[:10], 1):
                hot_concepts += f"{idx}. {name}: {info['change_pct']:+.2f}% | 换手率: {info['turnover']:.2f}%\n"
        
        prompt = build_market_sentiment_decoder_prompt(sentiment_summary, hot_sectors, hot_concepts)
        
        messages = [
            {"role": "system", "content": "你是一名资深的市场情绪分析师，擅长从市场数据中解读投资者情绪和市场心理。"},
            {"role": "user", "content": prompt}
        ]
        
        analysis = self.deepseek_client.call_api(messages, max_tokens=4000)
        
        print("  ✓ 市场情绪解码员分析完成")
        
        return {
            "agent_name": "市场情绪解码员",
            "agent_role": "量化市场情绪，识别恐慌贪婪信号，评估板块热度",
            "analysis": analysis,
            "focus_areas": ["市场情绪", "赚钱效应", "热点识别", "恐慌贪婪", "活跃度"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def _format_market_overview(self, market_data):
        """格式化市场概况"""
        if not market_data:
            return "暂无市场数据"
        
        text = ""
        if market_data.get("sh_index"):
            sh = market_data["sh_index"]
            text += f"上证指数: {sh['close']} ({sh['change_pct']:+.2f}%)\n"
        if market_data.get("sz_index"):
            sz = market_data["sz_index"]
            text += f"深证成指: {sz['close']} ({sz['change_pct']:+.2f}%)\n"
        if market_data.get("total_stocks"):
            text += f"涨跌统计: 上涨{market_data['up_count']}只({market_data['up_ratio']:.1f}%)，下跌{market_data['down_count']}只\n"
        
        return text


# 测试函数
if __name__ == "__main__":
    print("=" * 60)
    print("测试智策AI智能体系统")
    print("=" * 60)
    
    # 创建模拟数据
    test_market_data = {
        "sh_index": {"close": 3200, "change_pct": 0.5},
        "sz_index": {"close": 10500, "change_pct": 0.8},
        "total_stocks": 5000,
        "up_count": 3000,
        "up_ratio": 60.0,
        "down_count": 2000
    }
    
    test_news = [
        {"title": "央行宣布降准0.5个百分点", "content": "为支持实体经济发展...", "publish_time": "2024-01-15 10:00"}
    ]
    
    agents = SectorStrategyAgents()
    
    # 测试宏观策略师
    print("\n测试宏观策略师...")
    result = agents.macro_strategist_agent(test_market_data, test_news)
    print(f"分析师: {result['agent_name']}")
    print(f"分析内容长度: {len(result['analysis'])} 字符")

