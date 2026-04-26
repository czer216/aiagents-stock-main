"""
智瞰龙虎AI智能评分模块
对龙虎榜上榜股票进行综合评分排名
"""

import re
import pandas as pd
from typing import Dict, List, Any


class LonghubangScoring:
    """龙虎榜股票智能评分系统"""
    
    def __init__(self):
        """初始化评分系统"""
        # 顶级游资名单（根据市场知名度和历史战绩）
        self.top_youzi = [
            '赵老哥', '章盟主', '92科比', '瑞鹤仙', '小鳄鱼',
            '养家心法', '欢乐海岸', '古北路', '成都系', '佛山系',
            '方新侠', '乔帮主', '淮海路', '东方财富',
            '国信深圳', '华泰深圳', '中信杭州', '招商深圳'
        ]
        
        # 知名游资（次一级）
        self.famous_youzi = [
            '深股通', '沪股通', '北向资金',
            '中金公司', '中信证券', '国泰君安', '海通证券',
            '广发证券', '华泰证券', '招商证券'
        ]
        
        # 机构关键词
        self.institution_keywords = [
            '机构专用', '机构', '基金', '保险', '社保',
            'QFII', 'RQFII', '券商', '信托'
        ]
        
        print("[智瞰龙虎] 评分系统初始化完成")
    
    def calculate_stock_score(
        self,
        stock_data: List[Dict],
        concept_rotation_data: Dict[str, Any] = None,
        limit_step_data: Dict[str, Any] = None,
        ths_hot_data: Dict[str, Any] = None,
        p1_signal_data: Dict[str, Any] = None,
        market_style_context: Dict[str, Any] = None,
        sector_tier_map: Dict[str, str] = None,
    ) -> float:
        """
        计算单个股票的综合评分
        
        Args:
            stock_data: 该股票的所有龙虎榜记录
            
        Returns:
            综合评分 (0-100分)
        """
        if not stock_data:
            return 0.0
        
        # 1. 买入资金含金量评分 (0-22分)
        capital_quality_score = self._calculate_capital_quality(stock_data, p1_signal_data)
        
        # 2. 净买入额评分 (0-18分)
        net_inflow_score = self._calculate_net_inflow_score(stock_data, p1_signal_data)
        
        # 3. 卖出压力评分 (0-14分)
        sell_pressure_score = self._calculate_sell_pressure_score(stock_data, p1_signal_data)
        
        # 4. 机构共振评分 (0-15分)
        institution_score = self._calculate_institution_score(stock_data, p1_signal_data)
        
        # 5. 其他加分项 (0-4分)
        bonus_score = self._calculate_bonus_score(stock_data, p1_signal_data)

        # 6. 板块轮动评分 (0-15分)
        rotation_score = self._calculate_board_rotation_score(
            stock_data, concept_rotation_data, limit_step_data
        )

        # 7. 连板晋级评分 (0-2分)
        step_score = self._calculate_limit_step_score(stock_data, limit_step_data)

        # 8. 同花顺热榜评分 (0-10分)
        ths_hot_score = self._calculate_ths_hot_score(stock_data, ths_hot_data)
        
        # 辅助标签低权重微调（不改变主因子主导）
        p1_detail = self._get_p1_detail(stock_data, p1_signal_data)
        stock_concepts = self._get_enriched_stock_concepts(
            stock_data,
            concept_rotation_data=concept_rotation_data,
            limit_step_data=limit_step_data,
        )
        position_tag = self._derive_stock_position_tag(p1_detail)
        capital_behavior_tag = self._derive_capital_behavior_tag(p1_detail)
        sector_tier = self._resolve_sector_tier(stock_concepts, sector_tier_map)
        style_regime = str((market_style_context or {}).get("style_regime", "balanced") or "balanced")
        helper_adjust = self._calc_helper_adjust(
            position_tag=position_tag,
            capital_behavior_tag=capital_behavior_tag,
            sector_tier=sector_tier,
            style_regime=style_regime,
        )

        # 综合评分
        total_score = (
            capital_quality_score +
            net_inflow_score +
            sell_pressure_score +
            institution_score +
            bonus_score +
            rotation_score +
            step_score +
            ths_hot_score +
            helper_adjust
        )
        total_score = max(0.0, min(100.0, total_score))

        return round(total_score, 1)
    
    def _calculate_capital_quality(
        self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None
    ) -> float:
        """
        计算买入资金含金量评分 (0-22分)
        顶级游资加分多，普通游资加分少
        """
        score = 0.0
        old_max_score = 30.0
        max_score = 22.0
        
        buyers = []
        for record in stock_data:
            buy_amount = record.get('买入金额', 0) or record.get('mrje', 0)
            # 确保转换为数值类型
            try:
                buy_amount = float(buy_amount) if buy_amount else 0
            except (ValueError, TypeError):
                buy_amount = 0
            
            if buy_amount > 0:
                youzi_name = record.get('游资名称', '') or record.get('yzmc', '')
                yingye_bu = record.get('营业部', '') or record.get('yyb', '')
                buyers.append({
                    'name': youzi_name,
                    'yingye_bu': yingye_bu,
                    'amount': float(buy_amount)
                })
        
        if not buyers:
            return 0.0
        
        # 顶级游资：每个加8-10分
        top_youzi_count = 0
        for buyer in buyers:
            for top in self.top_youzi:
                if top in buyer['name'] or top in buyer['yingye_bu']:
                    top_youzi_count += 1
                    score += 10.0
                    break
        
        # 知名游资：每个加4-6分
        famous_youzi_count = 0
        for buyer in buyers:
            is_top = any(top in buyer['name'] or top in buyer['yingye_bu'] 
                        for top in self.top_youzi)
            if not is_top:
                for famous in self.famous_youzi:
                    if famous in buyer['name'] or famous in buyer['yingye_bu']:
                        famous_youzi_count += 1
                        score += 5.0
                        break
        
        # 普通游资：每个加1-2分
        ordinary_count = len(buyers) - top_youzi_count - famous_youzi_count
        score += ordinary_count * 1.5
        
        # 先按旧量纲封顶，再映射到新量纲
        base_score = min(score, old_max_score)
        mapped_score = base_score / old_max_score * max_score
        p1 = self._get_p1_detail(stock_data, p1_signal_data)
        if p1:
            # 机构净买入+机构买入占比，强化“资金含金量”判断
            inst_ratio = float(p1.get("inst_buy_ratio", 0.0) or 0.0)
            inst_net = float(p1.get("inst_net_amt", 0.0) or 0.0)
            inst_boost = min(inst_ratio * 1.2, 1.2)
            if inst_net > 0:
                inst_boost += 0.5
            # 游资画像增强：活跃游资持续净买入时提升含金量
            youzi_signal = float(p1.get("youzi_signal_score", 0.0) or 0.0)
            youzi_net = float(p1.get("youzi_net_amt_sum", 0.0) or 0.0)
            youzi_boost = min(youzi_signal, 1.0) * 0.8
            if youzi_net > 0:
                youzi_boost += 0.25
            mapped_score += min(inst_boost, 1.7)
            mapped_score += min(youzi_boost, 1.05)
        return round(min(mapped_score, max_score), 2)
    
    def _calculate_net_inflow_score(
        self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None
    ) -> float:
        """
        计算净买入额评分 (0-18分)
        真金白银越多分数越高
        """
        old_max_score = 25.0
        max_score = 18.0
        
        # 计算总净流入
        total_net_inflow = 0.0
        for record in stock_data:
            net_inflow = record.get('净流入金额', 0) or record.get('jlrje', 0)
            try:
                net_inflow = float(net_inflow) if net_inflow else 0
                total_net_inflow += net_inflow
            except (ValueError, TypeError):
                pass
        
        if total_net_inflow <= 0:
            return 0.0
        
        # 净流入分段评分
        # 1000万以下：0-10分
        # 1000-5000万：10-18分
        # 5000万-1亿：18-22分
        # 1亿以上：22-25分
        net_inflow_wan = total_net_inflow / 10000  # 转换为万元
        
        if net_inflow_wan < 1000:
            score = (net_inflow_wan / 1000) * 10
        elif net_inflow_wan < 5000:
            score = 10 + ((net_inflow_wan - 1000) / 4000) * 8
        elif net_inflow_wan < 10000:
            score = 18 + ((net_inflow_wan - 5000) / 5000) * 4
        else:
            score = 22 + min((net_inflow_wan - 10000) / 10000, 1) * 3
        
        base_score = min(score, old_max_score)
        mapped_score = base_score / old_max_score * max_score
        p1 = self._get_p1_detail(stock_data, p1_signal_data)
        if p1:
            # 龙虎榜净额 + 同花顺主力净流入联合确认
            lhb_net = float(p1.get("lhb_net_amt", 0.0) or 0.0)
            main_net_sum = float(p1.get("main_net_amt_sum", 0.0) or 0.0)
            positive_days = int(p1.get("money_positive_days", 0) or 0)
            money_hits = int(p1.get("money_hits", 0) or 0)
            youzi_net = float(p1.get("youzi_net_amt_sum", 0.0) or 0.0)
            youzi_pos = int(p1.get("youzi_positive_hits", 0) or 0)
            youzi_hits = int(p1.get("youzi_hits", 0) or 0)
            boost = 0.0
            if lhb_net > 0:
                boost += 0.7
            if main_net_sum > 0:
                boost += 0.9
            if money_hits > 0:
                boost += min(positive_days / max(money_hits, 1), 1.0) * 0.9
            if youzi_net > 0:
                boost += 0.4
            if youzi_hits > 0:
                boost += min(youzi_pos / max(youzi_hits, 1), 1.0) * 0.45
            # 主力资金持续性加分
            consecutive_days = int(p1.get("main_inflow_consecutive_days", 0) or 0)
            if consecutive_days >= 3:
                boost += 0.5
            mapped_score += min(boost, 2.7)
        return round(min(mapped_score, max_score), 2)
    
    def _calculate_sell_pressure_score(
        self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None
    ) -> float:
        """
        计算卖出压力评分 (0-14分)
        卖出压力越小分数越高
        """
        old_max_score = 20.0
        max_score = 14.0
        
        total_buy = 0.0
        total_sell = 0.0
        
        for record in stock_data:
            buy_amount = record.get('买入金额', 0) or record.get('mrje', 0)
            sell_amount = record.get('卖出金额', 0) or record.get('mcje', 0)
            
            try:
                buy_amount = float(buy_amount) if buy_amount else 0
                total_buy += buy_amount
            except (ValueError, TypeError):
                pass
            
            try:
                sell_amount = float(sell_amount) if sell_amount else 0
                total_sell += sell_amount
            except (ValueError, TypeError):
                pass
        
        if total_buy == 0:
            return 0.0
        
        # 计算卖出比例
        sell_ratio = total_sell / total_buy if total_buy > 0 else 1.0
        
        # 卖出压力评分
        # 卖出比例0-10%：20分
        # 卖出比例10-30%：15-20分
        # 卖出比例30-50%：10-15分
        # 卖出比例50-80%：5-10分
        # 卖出比例80%以上：0-5分
        if sell_ratio < 0.1:
            score = 20.0
        elif sell_ratio < 0.3:
            score = 20.0 - (sell_ratio - 0.1) / 0.2 * 5
        elif sell_ratio < 0.5:
            score = 15.0 - (sell_ratio - 0.3) / 0.2 * 5
        elif sell_ratio < 0.8:
            score = 10.0 - (sell_ratio - 0.5) / 0.3 * 5
        else:
            score = 5.0 - min(sell_ratio - 0.8, 0.2) / 0.2 * 5
        
        base_score = max(0, min(score, old_max_score))
        mapped_score = base_score / old_max_score * max_score
        p1 = self._get_p1_detail(stock_data, p1_signal_data)
        if p1:
            # 封板质量高、炸板次数少 -> 卖压评分上调；反之下调
            seal_quality = float(p1.get("seal_quality", 0.0) or 0.0)  # 0~1.2
            open_times = float(p1.get("open_times_avg", 0.0) or 0.0)
            adj = min(seal_quality, 1.0) * 1.0 - min(open_times * 0.25, 1.1)
            # 游资净卖出占比高时，卖压加大
            youzi_hits = int(p1.get("youzi_hits", 0) or 0)
            if youzi_hits > 0:
                neg_ratio = float(p1.get("youzi_negative_hits", 0) or 0.0) / max(youzi_hits, 1)
                adj -= min(max(neg_ratio, 0.0), 1.0) * 0.8
            mapped_score += adj
        return round(max(0.0, min(mapped_score, max_score)), 2)
    
    def _calculate_institution_score(
        self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None
    ) -> float:
        """
        计算机构共振评分 (0-15分)
        机构+游资共振最高分
        """
        max_score = 15.0
        
        has_institution = False
        has_youzi = False
        institution_count = 0
        youzi_count = 0
        
        for record in stock_data:
            buy_amount = record.get('买入金额', 0) or record.get('mrje', 0)
            try:
                buy_amount = float(buy_amount) if buy_amount else 0
            except (ValueError, TypeError):
                buy_amount = 0
            
            if buy_amount <= 0:
                continue
            
            youzi_name = record.get('游资名称', '') or record.get('yzmc', '')
            yingye_bu = record.get('营业部', '') or record.get('yyb', '')
            
            # 检查是否是机构
            if any(keyword in youzi_name or keyword in yingye_bu 
                  for keyword in self.institution_keywords):
                has_institution = True
                institution_count += 1
            else:
                has_youzi = True
                youzi_count += 1
        
        # 评分逻辑
        if has_institution and has_youzi:
            # 机构+游资共振：最高分
            score = 15.0
        elif has_institution:
            # 仅机构：8-12分
            score = min(8 + institution_count * 2, 12)
        elif has_youzi:
            # 仅游资：5-10分
            score = min(5 + youzi_count * 1, 10)
        else:
            score = 0.0
        
        final_score = min(score, max_score)
        p1 = self._get_p1_detail(stock_data, p1_signal_data)
        if p1:
            inst_ratio = float(p1.get("inst_buy_ratio", 0.0) or 0.0)
            inst_net = float(p1.get("inst_net_amt", 0.0) or 0.0)
            youzi_signal = float(p1.get("youzi_signal_score", 0.0) or 0.0)
            if inst_ratio >= 0.6:
                final_score += 1.0
            elif inst_ratio >= 0.5:
                final_score += 0.6
            if inst_net > 0:
                final_score += 0.8
            if youzi_signal >= 0.8:
                final_score += 0.5
            # 机构席位复现率加分
            inst_recurrence = float(p1.get("inst_seat_recurrence_rate", 0.0) or 0.0)
            if inst_recurrence >= 0.5:
                final_score += 0.8
        return min(final_score, max_score)
    
    def _calculate_bonus_score(self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None) -> float:
        """
        计算其他加分项 (0-4分)
        买卖比例、主力集中度、热门概念等
        """
        max_score = 4.0
        score = 0.0
        
        if not stock_data:
            return 0.0
        
        # 1. 主力集中度加分 (0-1.4分)
        # 如果资金集中在少数几个席位，说明主力信心强
        seat_count = len(stock_data)
        if seat_count == 1:
            score += 1.4
        elif seat_count == 2:
            score += 1.2
        elif seat_count == 3:
            score += 0.9
        elif seat_count <= 5:
            score += 0.6
        else:
            score += 0.4
        
        # 2. 热门概念加分 (0-1.2分)
        all_concepts = self._extract_stock_concepts(stock_data)
        
        hot_keywords = [
            '人工智能', 'AI', 'ChatGPT', '算力', '新能源', '芯片', '半导体',
            '军工', '医药', '消费', '5G', '新材料', '量子', '光伏',
            '储能', '锂电池', '汽车', '游戏', '传媒', '元宇宙'
        ]
        
        concept_score = 0
        for concept in all_concepts:
            if any(keyword in concept for keyword in hot_keywords):
                concept_score += 0.12
        
        score += min(concept_score, 1.2)
        
        # 3. 连续上榜加分 (0-0.8分)
        # 这里简化处理，如果有多条记录可能表示连续上榜
        if len(stock_data) >= 3:
            score += 0.8
        elif len(stock_data) == 2:
            score += 0.4
        
        # 4. 买卖比例优秀加分 (0-0.6分)
        total_buy = 0.0
        total_sell = 0.0
        for r in stock_data:
            try:
                buy = float(r.get('买入金额', 0) or r.get('mrje', 0) or 0)
                total_buy += buy
            except (ValueError, TypeError):
                pass
            try:
                sell = float(r.get('卖出金额', 0) or r.get('mcje', 0) or 0)
                total_sell += sell
            except (ValueError, TypeError):
                pass
        
        if total_buy > 0:
            buy_sell_ratio = total_buy / (total_sell + 1)
            if buy_sell_ratio >= 10:
                score += 0.6
            elif buy_sell_ratio >= 5:
                score += 0.45
            elif buy_sell_ratio >= 3:
                score += 0.3

        # 5. 游资席位复现率加分
        p1 = self._get_p1_detail(stock_data, p1_signal_data)
        if p1:
            youzi_recurrence = float(p1.get("youzi_seat_recurrence_rate", 0.0) or 0.0)
            if youzi_recurrence >= 0.5:
                score += 0.6

        return min(score, max_score)

    def _calculate_board_rotation_score(
        self,
        stock_data: List[Dict],
        concept_rotation_data: Dict[str, Any] = None,
        limit_step_data: Dict[str, Any] = None,
    ) -> float:
        """
        板块轮动评分 (0-15分)
        同时参考：
        1) Tushare dc_member+dc_index（失败回退 dc_concept_cons/kpl_concept_cons）的概念主线强度
        2) Tushare limit_step 的连板梯队概念强度
        """
        max_score = 15.0
        if not stock_data:
            return 0.0
        # 优先使用概念主线成分映射（dc_member+dc_index，回退 dc/kpl concept_cons），叠加原始概念与连板梯队概念兜底
        stock_concepts = self._get_enriched_stock_concepts(
            stock_data,
            concept_rotation_data=concept_rotation_data,
            limit_step_data=limit_step_data,
        )
        if not stock_concepts:
            return 0.0

        rotation_strength_map = {}
        strongest_concept = ""
        rotation_top3: List[str] = []
        if concept_rotation_data and concept_rotation_data.get("data_success"):
            rotation_strength_map = concept_rotation_data.get("concept_strength_map", {}) or {}
            strongest_today = concept_rotation_data.get("strongest_today", {}) or {}
            strongest_concept = strongest_today.get("concept", "")
            daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
            if daily_rankings:
                rotation_top3 = [
                    x.get("concept", "")
                    for x in daily_rankings[0].get("top_concepts", [])[:3]
                    if x.get("concept")
                ]

        step_strength_map = {}
        strongest_step_concept = ""
        step_top3: List[str] = []
        if limit_step_data and limit_step_data.get("data_success"):
            step_strength_map = limit_step_data.get("concept_step_strength_map", {}) or {}
            strongest_step_list = limit_step_data.get("strongest_step_concepts", []) or []
            if strongest_step_list:
                strongest_step_concept = strongest_step_list[0].get("concept", "")
                step_top3 = [x.get("concept", "") for x in strongest_step_list[:3] if x.get("concept")]

        if not rotation_strength_map and not step_strength_map:
            return 0.0

        score = 0.0

        matched_rotation = self._match_hot_concepts(stock_concepts, rotation_strength_map) if rotation_strength_map else []
        if matched_rotation:
            best_rotation = max([v for _, v in matched_rotation])
            score += min(best_rotation * 1.5, 6.0)  # 0-6

            # 命中当日最强概念（+1.5）
            if strongest_concept and self._concept_hit(stock_concepts, strongest_concept):
                score += 1.5

            # 命中当日TOP3概念（+0.8）
            top3_hits = sum(1 for c in rotation_top3 if self._concept_hit(stock_concepts, c))
            if top3_hits >= 1:
                score += 0.8

            # 命中多个轮动主线（+0.7）
            if len(matched_rotation) >= 2:
                score += 0.7

            # 命中主线后，再按板块强度分(strength_100)进行二次加权
            strength100_map = self._extract_rotation_strength100_map(concept_rotation_data)
            if strength100_map:
                matched_s100: List[float] = []
                for concept, _ in matched_rotation:
                    raw = float(strength100_map.get(concept, 0.0) or 0.0)
                    if raw > 0:
                        matched_s100.append(raw)
                if matched_s100:
                    best_s100 = max(matched_s100) / 100.0
                    avg_s100 = (sum(matched_s100) / len(matched_s100)) / 100.0
                    # 额外贡献上限1.6分，避免侵占其它维度
                    score += min(best_s100 * 1.0 + avg_s100 * 0.6, 1.6)

        matched_step = self._match_hot_concepts(stock_concepts, step_strength_map) if step_strength_map else []
        if matched_step:
            best_step = max([v for _, v in matched_step])
            score += min(best_step * 1.2, 4.8)  # 0-4.8

            # 命中连板梯队最强概念（+0.8）
            if strongest_step_concept and self._concept_hit(stock_concepts, strongest_step_concept):
                score += 0.8

            # 命中连板梯队TOP3（+0.4）
            step_hits = sum(1 for c in step_top3 if self._concept_hit(stock_concepts, c))
            if step_hits >= 1:
                score += 0.4

            # 命中多个梯队强概念（+0.3）
            if len(matched_step) >= 2:
                score += 0.3

        return round(min(score, max_score), 2)

    def _calculate_limit_step_score(
        self, stock_data: List[Dict], limit_step_data: Dict[str, Any] = None
    ) -> float:
        """连板晋级评分 (0-2分)"""
        if not stock_data or not limit_step_data or not limit_step_data.get("data_success"):
            return 0.0
        code = self._get_primary_stock_code(stock_data)
        if not code:
            return 0.0
        step_map = limit_step_data.get("stock_step_heat_map", {}) or {}
        detail = step_map.get(code, {})
        return round(min(float(detail.get("score", 0.0)), 2.0), 2)

    def _calculate_ths_hot_score(
        self, stock_data: List[Dict], ths_hot_data: Dict[str, Any] = None
    ) -> float:
        """同花顺热榜评分 (0-10分)"""
        if not stock_data or not ths_hot_data or not ths_hot_data.get("data_success"):
            return 0.0
        code = self._get_primary_stock_code(stock_data)
        if not code:
            return 0.0
        hot_map = ths_hot_data.get("stock_hot_map", {}) or {}
        detail = hot_map.get(code, {})
        base_score = float(detail.get("score", 0.0) or 0.0)  # 原始范围 0-1
        return round(min(base_score * 10.0, 10.0), 2)

    def _get_primary_stock_code(self, stock_data: List[Dict]) -> str:
        """从单股票记录中提取6位代码"""
        if not stock_data:
            return ""
        for record in stock_data:
            raw = record.get("股票代码") or record.get("gpdm") or ""
            code = self._normalize_code(raw)
            if code:
                return code
        return ""

    def _normalize_code(self, code_value: Any) -> str:
        text = str(code_value or "").strip()
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        if len(text) == 6 and text.isdigit():
            return text
        return ""

    def _derive_stock_position_tag(self, p1_detail: Dict[str, Any]) -> str:
        status = str((p1_detail or {}).get("today_limit_up_status", "") or "").strip()
        streak = 0
        m = re.search(r"(\d+)\s*天\s*(\d+)\s*板", status)
        if m:
            try:
                streak = int(m.group(2))
            except Exception:
                streak = 0
        if streak >= 4:
            return "high_divergence"
        if streak >= 2:
            return "mid_accel"
        quality = float((p1_detail or {}).get("limit_quality_score", 0.0) or 0.0)
        if quality >= 0.65:
            return "low_start"
        if float((p1_detail or {}).get("main_net_amt_sum", 0.0) or 0.0) < 0:
            return "fading"
        return "low_start"

    def _derive_capital_behavior_tag(self, p1_detail: Dict[str, Any]) -> str:
        p1 = dict(p1_detail or {})
        inst_net = float(p1.get("inst_net_amt", 0.0) or 0.0)
        youzi_net = float(p1.get("youzi_net_amt_sum", 0.0) or 0.0)
        main_net = float(p1.get("main_net_amt_sum", 0.0) or 0.0)
        if inst_net > 0 and inst_net >= abs(youzi_net) * 0.8:
            return "institutional_led"
        if youzi_net > 0 and youzi_net >= abs(inst_net) * 0.8:
            return "hot_money_led"
        if main_net < 0 and youzi_net < 0:
            return "distribution_pressure"
        return "mixed"

    def _resolve_sector_tier(self, stock_concepts: List[str], sector_tier_map: Dict[str, str] = None) -> str:
        normalized_map = {
            self._normalize_theme_key(k): str(v or "rotation")
            for k, v in dict(sector_tier_map or {}).items()
            if self._normalize_theme_key(k)
        }
        if not stock_concepts or not normalized_map:
            return "rotation"
        rank = {"mainline": 4, "secondary": 3, "rotation": 2, "fading": 1}
        best = "rotation"
        best_score = 0
        for concept in stock_concepts:
            key = self._normalize_theme_key(concept)
            tier = normalized_map.get(key, "")
            s = rank.get(tier, 0)
            if s > best_score:
                best_score = s
                best = tier
        return best

    def _normalize_theme_key(self, text: Any) -> str:
        s = str(text or "").strip().lower()
        if not s:
            return ""
        s = re.sub(r"[\s\-\_＋+,，;；/|、]+", "", s)
        s = re.sub(r"(概念|题材|板块|方向)$", "", s)
        return s

    def _calc_helper_adjust(
        self,
        position_tag: str,
        capital_behavior_tag: str,
        sector_tier: str,
        style_regime: str,
    ) -> float:
        pos_map = {"low_start": 1.5, "mid_accel": 0.8, "high_divergence": -1.8, "fading": -2.4}
        cap_map = {"institutional_led": 1.8, "hot_money_led": 1.0, "mixed": 0.0, "distribution_pressure": -2.0}
        sector_map = {"mainline": 1.2, "secondary": 0.6, "rotation": 0.0, "fading": -1.2}
        style_map = {"risk_on": 0.8, "balanced": 0.0, "defensive": -0.8}
        raw = (
            float(pos_map.get(str(position_tag or ""), 0.0))
            + float(cap_map.get(str(capital_behavior_tag or ""), 0.0))
            + float(sector_map.get(str(sector_tier or ""), 0.0))
            + float(style_map.get(str(style_regime or ""), 0.0))
        )
        return round(max(-6.0, min(6.0, raw)), 2)

    def _get_p1_detail(
        self, stock_data: List[Dict], p1_signal_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        if not stock_data or not p1_signal_data or not p1_signal_data.get("data_success"):
            return {}
        code = self._get_primary_stock_code(stock_data)
        if not code:
            return {}
        return (p1_signal_data.get("stock_signal_map", {}) or {}).get(code, {}) or {}

    def _extract_stock_concepts(self, stock_data: List[Dict]) -> List[str]:
        concepts = []
        for record in stock_data:
            raw = record.get("概念", "") or record.get("gl", "")
            if not raw:
                continue
            concepts.extend(self._split_concepts(raw))
        dedup = []
        seen = set()
        for concept in concepts:
            if concept not in seen:
                seen.add(concept)
                dedup.append(concept)
        return dedup

    def _split_concepts(self, raw: Any) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        parts = re.split(r"[,，;；/|、\s]+", text)
        noise = {"概念", "题材", "板块", "-", "--", "N/A", "None", "nan"}
        out: List[str] = []
        for part in parts:
            concept = str(part or "").strip()
            if not concept:
                continue
            if concept in noise or len(concept) < 2 or concept.isdigit():
                continue
            out.append(concept)
        return out

    def _get_enriched_stock_concepts(
        self,
        stock_data: List[Dict],
        concept_rotation_data: Dict[str, Any] = None,
        limit_step_data: Dict[str, Any] = None,
    ) -> List[str]:
        concepts: List[str] = []
        code = self._get_primary_stock_code(stock_data)
        if code and concept_rotation_data and concept_rotation_data.get("data_success"):
            kpl_concepts = (
                (concept_rotation_data.get("stock_concept_map", {}) or {})
                .get(code, [])
                or []
            )
            for concept in kpl_concepts:
                clean = str(concept or "").strip()
                if clean:
                    concepts.append(clean)

        # 保留原始概念字段作为补充
        concepts.extend(self._extract_stock_concepts(stock_data))

        # 连板梯队概念兜底
        if code and limit_step_data and limit_step_data.get("data_success"):
            step_concepts = (
                (limit_step_data.get("stock_step_heat_map", {}) or {})
                .get(code, {})
                .get("concepts", [])
                or []
            )
            for concept in step_concepts:
                clean = str(concept or "").strip()
                if clean:
                    concepts.append(clean)
        dedup: List[str] = []
        seen = set()
        for concept in concepts:
            if concept not in seen:
                seen.add(concept)
                dedup.append(concept)
        return dedup

    def _concept_hit(self, stock_concepts: List[str], hot_concept: str) -> bool:
        if not hot_concept:
            return False
        for concept in stock_concepts:
            if hot_concept in concept or concept in hot_concept:
                return True
        return False

    def _match_hot_concepts(
        self, stock_concepts: List[str], strength_map: Dict[str, float]
    ) -> List[Any]:
        matched = []
        for hot_concept, strength in strength_map.items():
            if self._concept_hit(stock_concepts, hot_concept):
                matched.append((hot_concept, float(strength)))
        matched.sort(key=lambda x: x[1], reverse=True)
        return matched[:5]

    def _extract_rotation_strength100_map(
        self, concept_rotation_data: Dict[str, Any] = None
    ) -> Dict[str, float]:
        """提取概念主线中的 strength_100（0-100）映射。"""
        if not concept_rotation_data or not concept_rotation_data.get("data_success"):
            return {}
        out: Dict[str, float] = {}
        daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
        if daily_rankings:
            top_concepts = (daily_rankings[0] or {}).get("top_concepts", []) or []
            for item in top_concepts:
                concept = str(item.get("concept", "") or "").strip()
                if not concept:
                    continue
                val = float(item.get("strength_100", 0.0) or 0.0)
                if val > 0:
                    out[concept] = max(out.get(concept, 0.0), val)
        if out:
            return out
        board_top10 = concept_rotation_data.get("board_top10", []) or []
        for item in board_top10:
            concept = str(item.get("concept", "") or "").strip()
            if not concept:
                continue
            val = float(item.get("strength_100", 0.0) or 0.0)
            if val > 0:
                out[concept] = max(out.get(concept, 0.0), val)
        return out
    
    def score_all_stocks(
        self,
        data_list: List[Dict],
        concept_rotation_data: Dict[str, Any] = None,
        limit_step_data: Dict[str, Any] = None,
        ths_hot_data: Dict[str, Any] = None,
        p1_signal_data: Dict[str, Any] = None,
        market_style_context: Dict[str, Any] = None,
        sector_tier_map: Dict[str, str] = None,
    ) -> pd.DataFrame:
        """
        对所有上榜股票进行评分排名
        
        Args:
            data_list: 龙虎榜数据列表
            
        Returns:
            评分排名DataFrame
        """
        if not data_list:
            return pd.DataFrame()
        
        # 按股票代码分组
        stocks_dict = {}
        for record in data_list:
            code = record.get('股票代码') or record.get('gpdm')
            name = record.get('股票名称') or record.get('gpmc')
            
            if not code:
                continue
            
            if code not in stocks_dict:
                stocks_dict[code] = {
                    'code': code,
                    'name': name,
                    'records': []
                }
            
            stocks_dict[code]['records'].append(record)
        
        # 计算每只股票的评分
        results = []
        for code, stock_info in stocks_dict.items():
            records = stock_info['records']
            
            # 计算各维度评分
            capital_quality = self._calculate_capital_quality(records, p1_signal_data)
            net_inflow = self._calculate_net_inflow_score(records, p1_signal_data)
            sell_pressure = self._calculate_sell_pressure_score(records, p1_signal_data)
            institution = self._calculate_institution_score(records, p1_signal_data)
            bonus = self._calculate_bonus_score(records)
            board_rotation = self._calculate_board_rotation_score(
                records, concept_rotation_data, limit_step_data
            )
            limit_step_score = self._calculate_limit_step_score(records, limit_step_data)
            ths_hot_score = self._calculate_ths_hot_score(records, ths_hot_data)
            
            total_score = (
                capital_quality
                + net_inflow
                + sell_pressure
                + institution
                + bonus
                + board_rotation
                + limit_step_score
                + ths_hot_score
            )
            stock_concepts = self._get_enriched_stock_concepts(
                records,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
            )
            p1_detail = self._get_p1_detail(records, p1_signal_data)
            position_tag = self._derive_stock_position_tag(p1_detail)
            capital_behavior_tag = self._derive_capital_behavior_tag(p1_detail)
            sector_tier = self._resolve_sector_tier(stock_concepts, sector_tier_map)
            style_regime = str((market_style_context or {}).get("style_regime", "balanced") or "balanced")
            helper_adjust = self._calc_helper_adjust(
                position_tag=position_tag,
                capital_behavior_tag=capital_behavior_tag,
                sector_tier=sector_tier,
                style_regime=style_regime,
            )
            total_score = max(0.0, min(100.0, total_score + helper_adjust))
            
            # 计算实际数据（安全转换）
            total_buy = 0.0
            total_sell = 0.0
            total_net = 0.0
            for r in records:
                try:
                    buy = float(r.get('买入金额', 0) or r.get('mrje', 0) or 0)
                    total_buy += buy
                except (ValueError, TypeError):
                    pass
                try:
                    sell = float(r.get('卖出金额', 0) or r.get('mcje', 0) or 0)
                    total_sell += sell
                except (ValueError, TypeError):
                    pass
                try:
                    net = float(r.get('净流入金额', 0) or r.get('jlrje', 0) or 0)
                    total_net += net
                except (ValueError, TypeError):
                    pass
            
            # 统计买入席位数（安全比较）
            buy_seats = 0
            for r in records:
                try:
                    buy = float(r.get('买入金额', 0) or r.get('mrje', 0) or 0)
                    if buy > 0:
                        buy_seats += 1
                except (ValueError, TypeError):
                    pass
            
            # 统计机构数量
            institution_count = sum(1 for r in records 
                                  if any(kw in (r.get('游资名称', '') or r.get('yzmc', '')) or 
                                        kw in (r.get('营业部', '') or r.get('yyb', ''))
                                        for kw in self.institution_keywords))
            
            primary_code = self._get_primary_stock_code(records)

            matched_hot_concepts = ""
            matched_hot_concepts_set = []
            if concept_rotation_data and concept_rotation_data.get("data_success"):
                matched_pairs = self._match_hot_concepts(
                    stock_concepts,
                    concept_rotation_data.get("concept_strength_map", {}) or {},
                )
                for concept, _ in matched_pairs[:3]:
                    if concept not in matched_hot_concepts_set:
                        matched_hot_concepts_set.append(concept)
            if limit_step_data and limit_step_data.get("data_success"):
                step_matched_pairs = self._match_hot_concepts(
                    stock_concepts,
                    limit_step_data.get("concept_step_strength_map", {}) or {},
                )
                for concept, _ in step_matched_pairs[:3]:
                    if concept not in matched_hot_concepts_set:
                        matched_hot_concepts_set.append(concept)
            matched_hot_concepts = ",".join(matched_hot_concepts_set[:3])

            step_detail = {}
            if limit_step_data and limit_step_data.get("data_success"):
                step_detail = (limit_step_data.get("stock_step_heat_map", {}) or {}).get(primary_code, {})
            ths_detail = {}
            if ths_hot_data and ths_hot_data.get("data_success"):
                ths_detail = (ths_hot_data.get("stock_hot_map", {}) or {}).get(primary_code, {})
            best_rank = int(ths_detail.get("best_rank", 0) or 0)
            best_rank_display = f"TOP {best_rank}" if best_rank > 0 else "-"
            # 判断机构参与
            has_institution = institution_count > 0
            
            results.append({
                '排名': 0,  # 稍后填充
                '排名_display': '',  # 用于显示奖牌
                '股票名称': stock_info['name'],
                '股票代码': code,
                '综合评分': round(total_score, 1),
                '资金含金量': round(capital_quality, 0),
                '净买入额': round(net_inflow, 0),
                '卖出压力': round(sell_pressure, 0),
                '机构共振': round(institution, 0),
                '加分项': round(bonus, 0),
                '板块轮动': round(board_rotation, 1),
                '连板晋级': round(limit_step_score, 1),
                '同花顺热榜': round(ths_hot_score, 1),
                '顶级游资': self._count_top_youzi(records),
                '买方数': buy_seats,
                '机构参与': '✅' if has_institution else '❌',
                '净流入': round(total_net, 2),
                '主线匹配': matched_hot_concepts,
                '连板高度': int(step_detail.get("max_step", 0) or 0),
                '热榜最佳': best_rank_display,
                'P1增强': round(float(p1_detail.get("score", 0.0) or 0.0) * 10, 1),
                '机构净额': round(float(p1_detail.get("inst_net_amt", 0.0) or 0.0), 2),
                '主力净流': round(float(p1_detail.get("main_net_amt_sum", 0.0) or 0.0), 2),
                '封板质量': round(float(p1_detail.get("limit_quality_score", 0.0) or 0.0) * 10, 1),
                '游资信号': round(float(p1_detail.get("youzi_signal_score", 0.0) or 0.0) * 10, 1),
                '游资画像': p1_detail.get("top_hm_name", "") or "-",
                '辅助微调': round(float(helper_adjust), 2),
                '位置标签': position_tag,
                '资金行为': capital_behavior_tag,
                '板块分层': sector_tier,
                '市场风格': style_regime,
            })
        
        # 转换为DataFrame并排序
        df = pd.DataFrame(results)
        if df.empty:
            return df
        
        df = df.sort_values('综合评分', ascending=False).reset_index(drop=True)
        df['排名'] = range(1, len(df) + 1)
        
        # 添加奖牌显示
        df['排名_display'] = df['排名'].astype(str)
        if len(df) >= 1:
            df.loc[0, '排名_display'] = '🥇 1'
        if len(df) >= 2:
            df.loc[1, '排名_display'] = '🥈 2'
        if len(df) >= 3:
            df.loc[2, '排名_display'] = '🥉 3'
        
        return df
    
    def _count_top_youzi(self, records: List[Dict]) -> int:
        """统计顶级游资数量"""
        count = 0
        for record in records:
            buy_amount = record.get('买入金额', 0) or record.get('mrje', 0)
            try:
                buy_amount = float(buy_amount) if buy_amount else 0
            except (ValueError, TypeError):
                buy_amount = 0
            
            if buy_amount <= 0:
                continue
            
            youzi_name = record.get('游资名称', '') or record.get('yzmc', '')
            yingye_bu = record.get('营业部', '') or record.get('yyb', '')
            
            if any(top in youzi_name or top in yingye_bu for top in self.top_youzi):
                count += 1
        
        return count
    
    def get_score_explanation(self) -> str:
        """获取评分维度说明"""
        explanation = """
        【AI智能评分维度说明】
        
        📊 总分100分，由8个维度组成：
        
        1️⃣ 买入资金含金量 (0-22分)
           - 顶级游资（赵老哥、章盟主等）：每个+10分
           - 知名游资（深股通、中信等）：每个+5分
           - 普通游资：每个+1.5分
           
        2️⃣ 净买入额评分 (0-18分)
           - 净流入1000万以下：0-10分
           - 净流入1000-5000万：10-18分
           - 净流入5000万-1亿：18-22分
           - 净流入1亿以上：22-25分
           - 以上分段结果按比例映射至0-18分
           
        3️⃣ 卖出压力评分 (0-14分)
           - 卖出比例0-10%：20分（压力极小）
           - 卖出比例10-30%：15-20分（压力较小）
           - 卖出比例30-50%：10-15分（压力中等）
           - 卖出比例50-80%：5-10分（压力较大）
           - 卖出比例80%以上：0-5分（压力极大）
           - 以上分段结果按比例映射至0-14分
           
        4️⃣ 机构共振评分 (0-15分)
           - 机构+游资共振：15分（最强信号）
           - 仅机构买入：8-12分
           - 仅游资买入：5-10分
           
        5️⃣ 其他加分项 (0-4分)
           - 主力集中度、热门概念、连续上榜、买卖比例

        6️⃣ 板块轮动评分 (0-15分)
           - 使用 Tushare `dc_member + dc_index`（失败回退 `dc_concept_cons` / `kpl_concept_cons`）识别“概念主线与个股概念映射”
           - 叠加 Tushare `limit_step` 提取“连板梯队最强概念”
           - 个股概念命中主线与梯队强概念，获得更高分
        
        7️⃣ 连板晋级评分 (0-2分)
           - 使用 Tushare `limit_step` 识别连板高度与晋级持续性
           - 连板高度越高、晋级天数越多，得分越高

        8️⃣ 同花顺热榜评分 (0-10分)
           - 使用 Tushare `ths_hot`（仅A股）
           - 热榜排名越靠前、连续在榜天数越多，得分越高

        🔧 P1增强修正（嵌入以上维度，不单独加总）
           - 龙虎榜统计/机构明细：`top_list` + `top_inst`
           - 游资画像：`hm_list` + `hm_detail`
           - 资金确认：`moneyflow_ths` + `moneyflow_cnt_ths` + `moneyflow_ind_ths`
           - 封板质量：`limit_list_ths` + `limit_list_d`
           - 用于细化“资金含金量 / 净买入额 / 卖出压力 / 机构共振”的评分准确度
        
        💡 评分越高，表示该股票受到资金青睐程度越高，
           但仍需结合市场环境、技术面等因素综合判断！
        """
        return explanation


# 测试函数
if __name__ == "__main__":
    print("=" * 60)
    print("测试智瞰龙虎评分系统")
    print("=" * 60)
    
    # 创建测试数据
    test_data = [
        {
            '股票代码': '001337',
            '股票名称': '四川黄金',
            '游资名称': '92科比',
            '营业部': '兴业证券股份有限公司南京天元东路证券营业部',
            '买入金额': 14470401,
            '卖出金额': 15080,
            '净流入金额': 14455321,
            '概念': '贵金属,黄金概念,次新股'
        },
        {
            '股票代码': '001337',
            '股票名称': '四川黄金',
            '游资名称': '赵老哥',
            '营业部': '某证券公司',
            '买入金额': 10000000,
            '卖出金额': 0,
            '净流入金额': 10000000,
            '概念': '贵金属,黄金概念'
        }
    ]
    
    scoring = LonghubangScoring()
    
    # 测试评分
    df_result = scoring.score_all_stocks(test_data)
    
    print("\n评分结果：")
    print(df_result)
    
    print("\n" + scoring.get_score_explanation())
