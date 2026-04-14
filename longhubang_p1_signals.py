"""
龙虎榜P1增强信号（Tushare）
1) 龙虎榜统计/机构明细：top_list + top_inst
1.5) 游资画像：hm_list + hm_detail
2) 资金确认：moneyflow_ths + moneyflow_cnt_ths + moneyflow_ind_ths
3) 封板质量：limit_list_ths + limit_list_d
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout


class AdvancedP1SignalFetcher:
    """P1增强数据抓取与特征聚合"""

    def __init__(self):
        self.data_source_manager = data_source_manager
        self._next_day_focus = False

    def get_signal_data(
        self,
        days: int = 3,
        end_date: Optional[str] = None,
        stock_codes: Optional[List[str]] = None,
        next_day_focus: bool = False,
    ) -> Dict[str, Any]:
        self._next_day_focus = bool(next_day_focus)
        result: Dict[str, Any] = {
            "data_success": False,
            "source": (
                "tushare.top_list/top_inst;"
                "tushare.hm_list/hm_detail;"
                "tushare.moneyflow_ths/moneyflow_cnt_ths/moneyflow_ind_ths;"
                "tushare.limit_list_ths/limit_list_d"
            ),
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stock_signal_map": {},
            "top_candidates": [],
            "lhb_summary": {},
            "moneyflow_summary": {},
            "limit_quality_summary": {},
            "youzi_profile_summary": {},
            "concept_flow_top": [],
            "industry_flow_top": [],
            "youzi_profile_top": [],
            "mode": "next_day_focus" if self._next_day_focus else "default",
        }

        pro = self.data_source_manager.tushare_api
        if not self.data_source_manager.tushare_available or pro is None:
            result["error"] = "Tushare不可用或未配置Token"
            return result

        target_codes = self._normalize_codes(stock_codes or [])
        if not target_codes:
            result["error"] = "未提供龙虎榜股票池，跳过P1增强信号"
            return result
        target_code_set = set(target_codes)

        trade_dates = self._build_recent_trade_dates(days=max(days, 2), end_date=end_date)
        if not trade_dates:
            result["error"] = "未生成有效交易日"
            return result

        stock_state: Dict[str, Dict[str, Any]] = {}
        for code in target_codes:
            stock_state[code] = self._init_stock_state(code)

        # A) 龙虎榜统计 + 机构明细
        self._collect_top_list(pro, trade_dates, target_code_set, stock_state)
        self._collect_top_inst(pro, trade_dates, target_code_set, stock_state)
        hm_profile_data = self._collect_hm_list(pro, trade_dates)
        self._collect_hm_detail(
            pro=pro,
            trade_dates=trade_dates,
            target_code_set=target_code_set,
            stock_state=stock_state,
            hm_profile_map=hm_profile_data.get("profile_map", {}) or {},
        )

        # B) 资金确认（个股/概念/行业）
        concept_flow_counter: Counter = Counter()
        industry_flow_counter: Counter = Counter()
        self._collect_moneyflow_stock(pro, trade_dates, target_code_set, stock_state)
        self._collect_moneyflow_concept(pro, trade_dates, concept_flow_counter)
        self._collect_moneyflow_industry(pro, trade_dates, industry_flow_counter)

        # C) 封板质量（以分析日为主，兼顾最近数日）
        self._collect_limit_quality(pro, trade_dates, target_code_set, stock_state)

        # D) 聚合打分
        stock_signal_map = self._build_stock_signal_map(stock_state)
        top_candidates = sorted(
            [
                {
                    "code": code,
                    "name": info.get("name", ""),
                    "score": info.get("score", 0.0),
                    "lhb_score": info.get("lhb_signal_score", 0.0),
                    "money_score": info.get("money_signal_score", 0.0),
                    "limit_score": info.get("limit_quality_score", 0.0),
                    "inst_net_amt": info.get("inst_net_amt", 0.0),
                    "main_net_amt_avg": info.get("main_net_amt_avg", 0.0),
                    "seal_quality": info.get("seal_quality", 0.0),
                }
                for code, info in stock_signal_map.items()
            ],
            key=lambda x: x["score"],
            reverse=True,
        )[:12]

        concept_flow_top = [
            {"concept": k, "flow_score": round(v, 2)}
            for k, v in concept_flow_counter.most_common(10)
        ]
        industry_flow_top = [
            {"industry": k, "flow_score": round(v, 2)}
            for k, v in industry_flow_counter.most_common(10)
        ]

        result.update(
            {
                "stock_signal_map": stock_signal_map,
                "top_candidates": top_candidates,
                "lhb_summary": self._build_lhb_summary(stock_signal_map),
                "moneyflow_summary": self._build_moneyflow_summary(stock_signal_map),
                "limit_quality_summary": self._build_limit_summary(stock_signal_map),
                "youzi_profile_summary": self._build_youzi_summary(
                    stock_signal_map, hm_profile_data.get("profile_map", {}) or {}
                ),
                "concept_flow_top": concept_flow_top,
                "industry_flow_top": industry_flow_top,
                "youzi_profile_top": hm_profile_data.get("top_profiles", []) or [],
            }
        )
        result["data_success"] = bool(stock_signal_map)
        if not result["data_success"]:
            result["error"] = "P1增强信号未命中有效数据"
        return result

    def _collect_top_list(
        self,
        pro: Any,
        trade_dates: List[str],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
    ) -> None:
        if not hasattr(pro, "top_list"):
            return
        for trade_date in trade_dates:
            df = self._call_api_with_candidates(
                pro.top_list,
                api_name="top_list",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                    {"trade_date": trade_date, "limit": 5000},
                    {"date": trade_date, "limit": 5000},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_top_list_rows(df)
            for row in rows:
                code = row.get("code", "")
                if code not in target_code_set:
                    continue
                st = stock_state.get(code)
                if st is None:
                    continue
                st["name"] = st["name"] or row.get("name", "")
                st["top_list_hits"] += 1
                st["lhb_net_amt"] += float(row.get("net_amt", 0.0) or 0.0)
                st["lhb_turnover_sum"] += float(row.get("turnover_rate", 0.0) or 0.0)
                st["reason_counter"].update(self._split_tokens(row.get("reason", "")))

    def _collect_top_inst(
        self,
        pro: Any,
        trade_dates: List[str],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
    ) -> None:
        if not hasattr(pro, "top_inst"):
            return
        for trade_date in trade_dates:
            df = self._call_api_with_candidates(
                pro.top_inst,
                api_name="top_inst",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                    {"trade_date": trade_date, "limit": 5000},
                    {"date": trade_date, "limit": 5000},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_top_inst_rows(df)
            for row in rows:
                code = row.get("code", "")
                if code not in target_code_set:
                    continue
                st = stock_state.get(code)
                if st is None:
                    continue
                st["name"] = st["name"] or row.get("name", "")
                st["inst_hits"] += 1
                st["inst_buy_amt"] += float(row.get("buy_amt", 0.0) or 0.0)
                st["inst_sell_amt"] += float(row.get("sell_amt", 0.0) or 0.0)
                st["inst_net_amt"] += float(row.get("net_amt", 0.0) or 0.0)

    def _collect_hm_list(self, pro: Any, trade_dates: List[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"profile_map": {}, "top_profiles": []}
        if not hasattr(pro, "hm_list"):
            return out

        profile_map: Dict[str, Dict[str, Any]] = {}
        for day_idx, trade_date in enumerate(trade_dates[:5]):
            decay = max(0.45, 1.0 - day_idx * 0.15)
            df = self._call_api_with_candidates(
                pro.hm_list,
                api_name="hm_list",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                    {"start_date": trade_date, "end_date": trade_date},
                    {"trade_date": trade_date, "limit": 5000},
                    {"date": trade_date, "limit": 5000},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_hm_list_rows(df)
            for rank_idx, row in enumerate(rows[:80]):
                hm_name = row.get("hm_name", "")
                if not hm_name:
                    continue
                profile = profile_map.setdefault(
                    hm_name,
                    {
                        "hm_name": hm_name,
                        "appear_days": 0,
                        "heat_score": 0.0,
                        "win_rate_sum": 0.0,
                        "win_rate_count": 0,
                        "style_counter": Counter(),
                        "seat_counter": Counter(),
                    },
                )
                profile["appear_days"] += 1
                profile["heat_score"] += max(0.35, 2.0 - rank_idx * 0.03) * decay
                win_rate = float(row.get("win_rate", 0.0) or 0.0)
                if win_rate > 0:
                    profile["win_rate_sum"] += win_rate
                    profile["win_rate_count"] += 1
                style = row.get("style", "")
                if style:
                    profile["style_counter"].update([style])
                seat = row.get("seat", "")
                if seat:
                    profile["seat_counter"].update([seat])

        if not profile_map:
            return out

        max_heat = max([float(v.get("heat_score", 0.0) or 0.0) for v in profile_map.values()] + [1.0])
        for profile in profile_map.values():
            heat = float(profile.get("heat_score", 0.0) or 0.0)
            profile["profile_score"] = round(max(0.0, min(heat / max_heat, 1.2)), 3)
            if profile.get("style_counter"):
                profile["style"] = profile["style_counter"].most_common(1)[0][0]
            else:
                profile["style"] = ""
            if profile.get("seat_counter"):
                profile["seat"] = profile["seat_counter"].most_common(1)[0][0]
            else:
                profile["seat"] = ""
            win_cnt = int(profile.get("win_rate_count", 0) or 0)
            profile["win_rate_avg"] = round(
                float(profile.get("win_rate_sum", 0.0) or 0.0) / max(win_cnt, 1), 3
            )

        top_profiles = sorted(
            [
                {
                    "hm_name": name,
                    "appear_days": int(info.get("appear_days", 0) or 0),
                    "heat_score": round(float(info.get("heat_score", 0.0) or 0.0), 3),
                    "profile_score": round(float(info.get("profile_score", 0.0) or 0.0), 3),
                    "win_rate_avg": round(float(info.get("win_rate_avg", 0.0) or 0.0), 3),
                    "style": info.get("style", ""),
                    "seat": info.get("seat", ""),
                }
                for name, info in profile_map.items()
            ],
            key=lambda x: (x["profile_score"], x["appear_days"], x["heat_score"]),
            reverse=True,
        )[:12]
        out["profile_map"] = profile_map
        out["top_profiles"] = top_profiles
        return out

    def _collect_hm_detail(
        self,
        pro: Any,
        trade_dates: List[str],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
        hm_profile_map: Dict[str, Dict[str, Any]],
    ) -> None:
        if not hasattr(pro, "hm_detail"):
            return
        for trade_date in trade_dates:
            df = self._call_api_with_candidates(
                pro.hm_detail,
                api_name="hm_detail",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                    {"trade_date": trade_date, "limit": 5000},
                    {"date": trade_date, "limit": 5000},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_hm_detail_rows(df)
            for row in rows:
                code = row.get("code", "")
                if code not in target_code_set:
                    continue
                st = stock_state.get(code)
                if st is None:
                    continue
                st["name"] = st["name"] or row.get("name", "")
                st["youzi_hits"] += 1
                buy_amt = float(row.get("buy_amt", 0.0) or 0.0)
                sell_amt = float(row.get("sell_amt", 0.0) or 0.0)
                net_amt = float(row.get("net_amt", 0.0) or 0.0)
                st["youzi_buy_amt_sum"] += buy_amt
                st["youzi_sell_amt_sum"] += sell_amt
                st["youzi_net_amt_sum"] += net_amt
                if net_amt > 0:
                    st["youzi_positive_hits"] += 1
                elif net_amt < 0:
                    st["youzi_negative_hits"] += 1

                hm_name = row.get("hm_name", "")
                if hm_name:
                    st["youzi_name_counter"].update([hm_name])
                    profile = hm_profile_map.get(hm_name, {}) or {}
                    st["youzi_profile_score_sum"] += float(profile.get("profile_score", 0.0) or 0.0)
                    style = profile.get("style", "")
                    if style:
                        st["youzi_style_counter"].update([style])

    def _collect_moneyflow_stock(
        self,
        pro: Any,
        trade_dates: List[str],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
    ) -> None:
        if not hasattr(pro, "moneyflow_ths"):
            return
        for trade_date in trade_dates:
            df = self._call_api_with_candidates(
                pro.moneyflow_ths,
                api_name="moneyflow_ths",
                candidates=[
                    {"trade_date": trade_date},
                    {"trade_date": trade_date, "market": "A"},
                    {"date": trade_date},
                    {"date": trade_date, "market": "A"},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_moneyflow_stock_rows(df)
            for row in rows:
                code = row.get("code", "")
                if code not in target_code_set:
                    continue
                st = stock_state.get(code)
                if st is None:
                    continue
                st["name"] = st["name"] or row.get("name", "")
                st["money_hits"] += 1
                main_net = float(row.get("main_net_amt", 0.0) or 0.0)
                st["main_net_amt_sum"] += main_net
                st["money_net_amt_sum"] += float(row.get("net_amt", 0.0) or 0.0)
                if main_net > 0:
                    st["money_positive_days"] += 1

    def _collect_moneyflow_concept(
        self,
        pro: Any,
        trade_dates: List[str],
        concept_flow_counter: Counter,
    ) -> None:
        if not hasattr(pro, "moneyflow_cnt_ths"):
            return
        for day_idx, trade_date in enumerate(trade_dates):
            decay = max(0.4, 1.0 - day_idx * 0.15)
            df = self._call_api_with_candidates(
                pro.moneyflow_cnt_ths,
                api_name="moneyflow_cnt_ths",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_moneyflow_concept_rows(df)
            for rank_idx, row in enumerate(rows[:30]):
                concept = row.get("concept", "")
                if not concept:
                    continue
                flow = float(row.get("net_amt", 0.0) or 0.0)
                if flow <= 0:
                    continue
                rank_weight = max(0.4, 2.0 - rank_idx * 0.05)
                concept_flow_counter[concept] += flow / 1e8 * rank_weight * decay

    def _collect_moneyflow_industry(
        self,
        pro: Any,
        trade_dates: List[str],
        industry_flow_counter: Counter,
    ) -> None:
        if not hasattr(pro, "moneyflow_ind_ths"):
            return
        for day_idx, trade_date in enumerate(trade_dates):
            decay = max(0.4, 1.0 - day_idx * 0.15)
            df = self._call_api_with_candidates(
                pro.moneyflow_ind_ths,
                api_name="moneyflow_ind_ths",
                candidates=[
                    {"trade_date": trade_date},
                    {"date": trade_date},
                ],
            )
            if df is None or df.empty:
                continue
            rows = self._extract_moneyflow_industry_rows(df)
            for rank_idx, row in enumerate(rows[:30]):
                industry = row.get("industry", "")
                if not industry:
                    continue
                flow = float(row.get("net_amt", 0.0) or 0.0)
                if flow <= 0:
                    continue
                rank_weight = max(0.4, 2.0 - rank_idx * 0.05)
                industry_flow_counter[industry] += flow / 1e8 * rank_weight * decay

    def _collect_limit_quality(
        self,
        pro: Any,
        trade_dates: List[str],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
    ) -> None:
        has_ths = hasattr(pro, "limit_list_ths")
        has_d = hasattr(pro, "limit_list_d")
        if not has_ths and not has_d:
            return

        # 封板质量以分析日为主，最多回看3个交易日
        for trade_date in trade_dates[:3]:
            if has_ths:
                df = self._call_api_with_candidates(
                    pro.limit_list_ths,
                    api_name="limit_list_ths",
                    candidates=[
                        {"trade_date": trade_date},
                        {"date": trade_date},
                    ],
                )
                self._merge_limit_rows(df, target_code_set, stock_state)

            if has_d:
                df = self._call_api_with_candidates(
                    pro.limit_list_d,
                    api_name="limit_list_d",
                    candidates=[
                        {"trade_date": trade_date},
                        {"date": trade_date},
                    ],
                )
                self._merge_limit_rows(df, target_code_set, stock_state)

    def _merge_limit_rows(
        self,
        df: Optional[pd.DataFrame],
        target_code_set: set,
        stock_state: Dict[str, Dict[str, Any]],
    ) -> None:
        if df is None or df.empty:
            return
        rows = self._extract_limit_quality_rows(df)
        for row in rows:
            code = row.get("code", "")
            if code not in target_code_set:
                continue
            st = stock_state.get(code)
            if st is None:
                continue
            st["name"] = st["name"] or row.get("name", "")
            st["limit_hits"] += 1
            st["open_times_sum"] += int(row.get("open_times", 0) or 0)
            st["fd_amount_sum"] += float(row.get("fd_amount", 0.0) or 0.0)
            st["limit_times_max"] = max(st["limit_times_max"], int(row.get("limit_times", 0) or 0))
            st["seal_quality_sum"] += float(row.get("seal_quality", 0.0) or 0.0)

    def _build_stock_signal_map(self, stock_state: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        max_abs_lhb_net = max([abs(v.get("lhb_net_amt", 0.0)) for v in stock_state.values()] + [1.0])
        max_abs_main_net = max([abs(v.get("main_net_amt_sum", 0.0)) for v in stock_state.values()] + [1.0])
        max_fd_amount = max([v.get("fd_amount_sum", 0.0) for v in stock_state.values()] + [1.0])
        max_abs_youzi_net = max([abs(v.get("youzi_net_amt_sum", 0.0)) for v in stock_state.values()] + [1.0])
        max_youzi_profile = max([v.get("youzi_profile_score_sum", 0.0) for v in stock_state.values()] + [1.0])

        for code, st in stock_state.items():
            inst_buy = float(st.get("inst_buy_amt", 0.0) or 0.0)
            inst_sell = float(st.get("inst_sell_amt", 0.0) or 0.0)
            inst_net = float(st.get("inst_net_amt", 0.0) or 0.0)
            inst_buy_ratio = inst_buy / max(inst_buy + inst_sell, 1.0)

            lhb_net = float(st.get("lhb_net_amt", 0.0) or 0.0)
            lhb_signal = 0.45 + 0.55 * (lhb_net / max_abs_lhb_net)
            lhb_signal += min(inst_buy_ratio, 1.0) * 0.25
            lhb_signal += 0.15 if inst_net > 0 else 0.0
            lhb_signal = max(0.0, min(lhb_signal, 1.2))

            money_hits = int(st.get("money_hits", 0) or 0)
            main_net_sum = float(st.get("main_net_amt_sum", 0.0) or 0.0)
            main_net_avg = main_net_sum / max(money_hits, 1)
            money_pos_days = int(st.get("money_positive_days", 0) or 0)
            money_signal = 0.45 + 0.45 * (main_net_sum / max_abs_main_net)
            money_signal += min(money_pos_days / max(money_hits, 1), 1.0) * 0.25
            if self._next_day_focus and money_hits > 0 and main_net_avg > 0:
                money_signal += 0.12
            money_signal = max(0.0, min(money_signal, 1.2))

            limit_hits = int(st.get("limit_hits", 0) or 0)
            open_times_avg = float(st.get("open_times_sum", 0.0) or 0.0) / max(limit_hits, 1)
            fd_amount_avg = float(st.get("fd_amount_sum", 0.0) or 0.0) / max(limit_hits, 1)
            seal_quality_avg = float(st.get("seal_quality_sum", 0.0) or 0.0) / max(limit_hits, 1)
            fd_boost = min(fd_amount_avg / max_fd_amount, 1.0) * 0.5 if max_fd_amount > 0 else 0.0
            open_penalty = min(open_times_avg * 0.15, 0.6)
            limit_quality = seal_quality_avg + fd_boost - open_penalty
            if int(st.get("limit_times_max", 0) or 0) >= 2:
                limit_quality += 0.15
            if self._next_day_focus:
                # 次日预期模式：更重视当日封板质量与炸板风险
                extra_fd_boost = min(fd_amount_avg / max_fd_amount, 1.0) * 0.15 if max_fd_amount > 0 else 0.0
                extra_open_penalty = min(open_times_avg * 0.12, 0.35)
                limit_quality += extra_fd_boost - extra_open_penalty
                if int(st.get("limit_times_max", 0) or 0) >= 2 and open_times_avg <= 1.0:
                    limit_quality += 0.12
            limit_quality = max(0.0, min(limit_quality, 1.2))

            youzi_hits = int(st.get("youzi_hits", 0) or 0)
            youzi_net_sum = float(st.get("youzi_net_amt_sum", 0.0) or 0.0)
            youzi_profile_avg = float(st.get("youzi_profile_score_sum", 0.0) or 0.0) / max(youzi_hits, 1)
            youzi_pos = int(st.get("youzi_positive_hits", 0) or 0)
            youzi_neg = int(st.get("youzi_negative_hits", 0) or 0)
            youzi_signal = 0.0
            if youzi_hits > 0:
                youzi_signal = 0.45 + 0.4 * (youzi_net_sum / max_abs_youzi_net)
                youzi_signal += min(youzi_pos / max(youzi_hits, 1), 1.0) * 0.2
                if max_youzi_profile > 0:
                    youzi_signal += min(youzi_profile_avg / max_youzi_profile, 1.0) * 0.25
                youzi_signal -= min(youzi_neg / max(youzi_hits, 1), 1.0) * 0.15
            youzi_signal = max(0.0, min(youzi_signal, 1.2))

            if self._next_day_focus:
                if youzi_hits > 0:
                    total = (
                        lhb_signal * 0.24
                        + money_signal * 0.34
                        + limit_quality * 0.32
                        + youzi_signal * 0.10
                    )
                else:
                    total = lhb_signal * 0.30 + money_signal * 0.38 + limit_quality * 0.32
            else:
                if youzi_hits > 0:
                    total = lhb_signal * 0.30 + money_signal * 0.30 + limit_quality * 0.25 + youzi_signal * 0.15
                else:
                    # 没有游资画像数据时，退回原P1三因子权重，避免空数据稀释得分
                    total = lhb_signal * 0.35 + money_signal * 0.35 + limit_quality * 0.30
            top_reason = ""
            if st.get("reason_counter"):
                top_reason = st["reason_counter"].most_common(1)[0][0]
            top_hm_name = ""
            if st.get("youzi_name_counter"):
                top_hm_name = st["youzi_name_counter"].most_common(1)[0][0]
            top_hm_style = ""
            if st.get("youzi_style_counter"):
                top_hm_style = st["youzi_style_counter"].most_common(1)[0][0]
            out[code] = {
                "name": st.get("name", ""),
                "score": round(max(0.0, min(total, 1.2)), 3),
                "lhb_signal_score": round(max(0.0, min(lhb_signal, 1.2)), 3),
                "money_signal_score": round(max(0.0, min(money_signal, 1.2)), 3),
                "limit_quality_score": round(max(0.0, min(limit_quality, 1.2)), 3),
                "youzi_signal_score": round(max(0.0, min(youzi_signal, 1.2)), 3),
                "lhb_net_amt": round(lhb_net, 2),
                "inst_buy_amt": round(inst_buy, 2),
                "inst_sell_amt": round(inst_sell, 2),
                "inst_net_amt": round(inst_net, 2),
                "inst_buy_ratio": round(inst_buy_ratio, 4),
                "main_net_amt_sum": round(main_net_sum, 2),
                "main_net_amt_avg": round(main_net_avg, 2),
                "money_positive_days": int(money_pos_days),
                "money_hits": int(money_hits),
                "limit_hits": int(limit_hits),
                "open_times_avg": round(open_times_avg, 2),
                "fd_amount_avg": round(fd_amount_avg, 2),
                "seal_quality": round(seal_quality_avg, 3),
                "limit_times": int(st.get("limit_times_max", 0) or 0),
                "top_reason": top_reason,
                "youzi_hits": int(youzi_hits),
                "youzi_net_amt_sum": round(youzi_net_sum, 2),
                "youzi_positive_hits": int(youzi_pos),
                "youzi_negative_hits": int(youzi_neg),
                "youzi_profile_avg": round(youzi_profile_avg, 3),
                "top_hm_name": top_hm_name,
                "top_hm_style": top_hm_style,
                "mode": "next_day_focus" if self._next_day_focus else "default",
            }
        return out

    def _build_lhb_summary(self, stock_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        vals = list(stock_map.values())
        if not vals:
            return {}
        inst_positive = sum(1 for v in vals if float(v.get("inst_net_amt", 0.0) or 0.0) > 0)
        avg_inst_ratio = sum(float(v.get("inst_buy_ratio", 0.0) or 0.0) for v in vals) / max(len(vals), 1)
        return {
            "tracked_stocks": len(vals),
            "inst_positive_count": int(inst_positive),
            "avg_inst_buy_ratio": round(avg_inst_ratio, 4),
        }

    def _build_moneyflow_summary(self, stock_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        vals = list(stock_map.values())
        if not vals:
            return {}
        pos = sum(1 for v in vals if float(v.get("main_net_amt_sum", 0.0) or 0.0) > 0)
        return {
            "tracked_stocks": len(vals),
            "main_net_positive_count": int(pos),
            "positive_ratio": round(pos / max(len(vals), 1), 4),
        }

    def _build_limit_summary(self, stock_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        vals = list(stock_map.values())
        if not vals:
            return {}
        high_quality = sum(1 for v in vals if float(v.get("limit_quality_score", 0.0) or 0.0) >= 0.8)
        avg_open = sum(float(v.get("open_times_avg", 0.0) or 0.0) for v in vals) / max(len(vals), 1)
        return {
            "tracked_stocks": len(vals),
            "high_quality_count": int(high_quality),
            "avg_open_times": round(avg_open, 3),
        }

    def _build_youzi_summary(
        self,
        stock_map: Dict[str, Dict[str, Any]],
        hm_profile_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        vals = list(stock_map.values())
        if not vals:
            return {}
        positive = sum(1 for v in vals if float(v.get("youzi_net_amt_sum", 0.0) or 0.0) > 0)
        avg_signal = sum(float(v.get("youzi_signal_score", 0.0) or 0.0) for v in vals) / max(len(vals), 1)
        top_hm = sorted(
            hm_profile_map.values(),
            key=lambda x: (float(x.get("profile_score", 0.0) or 0.0), int(x.get("appear_days", 0) or 0)),
            reverse=True,
        )
        top_hm_name = top_hm[0].get("hm_name", "") if top_hm else ""
        return {
            "tracked_stocks": len(vals),
            "youzi_positive_count": int(positive),
            "avg_youzi_signal": round(avg_signal, 4),
            "active_youzi_profiles": len(hm_profile_map),
            "top_youzi_profile": top_hm_name,
        }

    def _extract_top_list_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        reason_col = self._find_col(df, ["reason", "上榜原因", "reason_type"])
        net_col = self._find_col(df, ["net", "净额", "净买入"])
        turnover_col = self._find_col(df, ["turnover", "换手", "turnover_rate"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                    "reason": self._clean_text(row.get(reason_col)) if reason_col else "",
                    "net_amt": self._safe_number(row.get(net_col)) if net_col else 0.0,
                    "turnover_rate": self._safe_number(row.get(turnover_col)) if turnover_col else 0.0,
                }
            )
        return rows

    def _extract_top_inst_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        buy_col = self._find_col(df, ["buy", "买入"])
        sell_col = self._find_col(df, ["sell", "卖出"])
        net_col = self._find_col(df, ["net", "净额", "净买入"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code:
                continue
            buy_amt = self._safe_number(row.get(buy_col)) if buy_col else 0.0
            sell_amt = self._safe_number(row.get(sell_col)) if sell_col else 0.0
            net_amt = self._safe_number(row.get(net_col)) if net_col else (buy_amt - sell_amt)
            rows.append(
                {
                    "code": code,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                    "buy_amt": buy_amt,
                    "sell_amt": sell_amt,
                    "net_amt": net_amt,
                }
            )
        return rows

    def _extract_hm_list_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        hm_col = self._find_col(df, ["hm_name", "hot_money", "游资名称", "游资", "席位名称"])
        if not hm_col:
            hm_col = self._find_col(df, ["name"])
        if not hm_col:
            return []
        style_col = self._find_col(df, ["style", "风格", "tag", "标签"])
        seat_col = self._find_col(df, ["seat", "营业部", "branch"])
        win_col = self._find_col(df, ["win_rate", "胜率", "success_rate"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            hm_name = self._clean_text(row.get(hm_col))
            if not hm_name:
                continue
            rows.append(
                {
                    "hm_name": hm_name,
                    "style": self._clean_text(row.get(style_col)) if style_col else "",
                    "seat": self._clean_text(row.get(seat_col)) if seat_col else "",
                    "win_rate": self._safe_number(row.get(win_col)) if win_col else 0.0,
                }
            )
        return rows

    def _extract_hm_detail_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        hm_col = self._find_col(df, ["hm_name", "hot_money", "游资名称", "游资", "席位名称"])
        buy_col = self._find_col(df, ["buy", "买入"])
        sell_col = self._find_col(df, ["sell", "卖出"])
        net_col = self._find_col(df, ["net", "净额", "净买入"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code:
                continue
            buy_amt = self._safe_number(row.get(buy_col)) if buy_col else 0.0
            sell_amt = self._safe_number(row.get(sell_col)) if sell_col else 0.0
            net_amt = self._safe_number(row.get(net_col)) if net_col else (buy_amt - sell_amt)
            rows.append(
                {
                    "code": code,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                    "hm_name": self._clean_text(row.get(hm_col)) if hm_col else "",
                    "buy_amt": buy_amt,
                    "sell_amt": sell_amt,
                    "net_amt": net_amt,
                }
            )
        return rows

    def _extract_moneyflow_stock_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        net_col = self._find_col(df, ["net", "净流入", "净额", "net_amount"])
        main_net_col = self._find_col(df, ["主力净", "main", "net_mf", "main_net"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code:
                continue
            net_amt = self._safe_number(row.get(net_col)) if net_col else 0.0
            main_net = self._safe_number(row.get(main_net_col)) if main_net_col else net_amt
            rows.append(
                {
                    "code": code,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                    "net_amt": net_amt,
                    "main_net_amt": main_net,
                }
            )
        return rows

    def _extract_moneyflow_concept_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        name_col = self._find_col(df, ["概念", "concept", "name"])
        if not name_col:
            return []
        net_col = self._find_col(df, ["net", "净流入", "净额", "net_amount"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            concept = self._clean_text(row.get(name_col))
            if not concept:
                continue
            rows.append(
                {
                    "concept": concept,
                    "net_amt": self._safe_number(row.get(net_col)) if net_col else 0.0,
                }
            )
        rows.sort(key=lambda x: x["net_amt"], reverse=True)
        return rows

    def _extract_moneyflow_industry_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        name_col = self._find_col(df, ["行业", "industry", "name"])
        if not name_col:
            return []
        net_col = self._find_col(df, ["net", "净流入", "净额", "net_amount"])
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            industry = self._clean_text(row.get(name_col))
            if not industry:
                continue
            rows.append(
                {
                    "industry": industry,
                    "net_amt": self._safe_number(row.get(net_col)) if net_col else 0.0,
                }
            )
        rows.sort(key=lambda x: x["net_amt"], reverse=True)
        return rows

    def _extract_limit_quality_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        open_col = self._find_col(df, ["open_num", "open_times", "炸板", "开板"])
        fd_col = self._find_col(df, ["fd_amount", "封单", "封板资金"])
        limit_times_col = self._find_col(df, ["limit_times", "连板", "板数", "up_stat"])
        first_col = self._find_col(df, ["first_time", "首次封板"])
        last_col = self._find_col(df, ["last_time", "最后封板"])
        status_col = self._find_col(df, ["status", "封板状态"])

        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code:
                continue
            open_times = int(round(self._safe_number(row.get(open_col)))) if open_col else 0
            fd_amount = self._safe_number(row.get(fd_col)) if fd_col else 0.0
            limit_times = self._parse_limit_times(row.get(limit_times_col)) if limit_times_col else 0
            first_time = self._clean_text(row.get(first_col)) if first_col else ""
            last_time = self._clean_text(row.get(last_col)) if last_col else ""
            status = self._clean_text(row.get(status_col)) if status_col else ""
            seal_quality = self._calc_seal_quality(
                open_times=open_times,
                fd_amount=fd_amount,
                limit_times=limit_times,
                first_time=first_time,
                last_time=last_time,
                status=status,
            )
            rows.append(
                {
                    "code": code,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                    "open_times": max(open_times, 0),
                    "fd_amount": max(fd_amount, 0.0),
                    "limit_times": max(limit_times, 0),
                    "first_time": first_time,
                    "last_time": last_time,
                    "status": status,
                    "seal_quality": seal_quality,
                }
            )
        return rows

    def _calc_seal_quality(
        self,
        open_times: int,
        fd_amount: float,
        limit_times: int,
        first_time: str,
        last_time: str,
        status: str,
    ) -> float:
        score = 0.55
        score += min(max(fd_amount, 0.0) / 2e8, 0.35)
        score += min(max(limit_times, 0) * 0.06, 0.18)
        score -= min(max(open_times, 0) * 0.12, 0.5)
        if "炸" in status:
            score -= 0.2
        if first_time and last_time and first_time == last_time:
            score += 0.08
        return round(max(0.0, min(score, 1.2)), 3)

    def _parse_limit_times(self, value: Any) -> int:
        text = self._clean_text(value)
        if not text:
            return 0
        m = re.search(r"(\d+)", text)
        if not m:
            return 0
        try:
            return int(m.group(1))
        except Exception:
            return 0

    def _split_tokens(self, text: str) -> List[str]:
        raw = self._clean_text(text)
        if not raw:
            return []
        parts = re.split(r"[,，;；/|、\s]+", raw)
        noise = {"上榜", "原因", "N/A", "None", "nan", "-"}
        out: List[str] = []
        for part in parts:
            token = self._clean_text(part)
            if not token or token in noise or len(token) < 2:
                continue
            out.append(token)
        return out[:8]

    def _call_api_with_candidates(
        self,
        api_callable: Any,
        api_name: str,
        candidates: List[Dict[str, Any]],
    ) -> Optional[pd.DataFrame]:
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: api_callable(**kwargs),
                    timeout_sec=60,
                    api_name=api_name,
                )
            except TypeError:
                continue
            except TimeoutError:
                continue
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return None

    def _init_stock_state(self, code: str) -> Dict[str, Any]:
        return {
            "code": code,
            "name": "",
            "top_list_hits": 0,
            "lhb_net_amt": 0.0,
            "lhb_turnover_sum": 0.0,
            "reason_counter": Counter(),
            "inst_hits": 0,
            "inst_buy_amt": 0.0,
            "inst_sell_amt": 0.0,
            "inst_net_amt": 0.0,
            "money_hits": 0,
            "money_net_amt_sum": 0.0,
            "main_net_amt_sum": 0.0,
            "money_positive_days": 0,
            "youzi_hits": 0,
            "youzi_buy_amt_sum": 0.0,
            "youzi_sell_amt_sum": 0.0,
            "youzi_net_amt_sum": 0.0,
            "youzi_positive_hits": 0,
            "youzi_negative_hits": 0,
            "youzi_profile_score_sum": 0.0,
            "youzi_name_counter": Counter(),
            "youzi_style_counter": Counter(),
            "limit_hits": 0,
            "open_times_sum": 0.0,
            "fd_amount_sum": 0.0,
            "limit_times_max": 0,
            "seal_quality_sum": 0.0,
        }

    def _build_recent_trade_dates(self, days: int, end_date: Optional[str] = None) -> List[str]:
        if end_date:
            end_dt = self._parse_date(end_date) or datetime.now()
        else:
            end_dt = datetime.now()
        out: List[str] = []
        cur = end_dt
        while len(out) < days and len(out) < 10:
            if cur.weekday() < 5:
                out.append(cur.strftime("%Y%m%d"))
            cur -= timedelta(days=1)
        return out

    def _normalize_codes(self, codes: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for x in codes:
            code = self._normalize_code(x)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def _normalize_code(self, value: Any) -> str:
        text = self._clean_text(value)
        if not text:
            return ""
        if "." in text:
            base, suffix = text.split(".", 1)
            suffix = suffix.upper()
            if suffix not in ("SH", "SZ"):
                return ""
            if len(base) == 6 and base.isdigit() and base[0] in ("0", "3", "6"):
                return base
            return ""
        if len(text) == 6 and text.isdigit() and text[0] in ("0", "3", "6"):
            return text
        return ""

    def _find_col(self, df: pd.DataFrame, keywords: List[str]) -> str:
        for col in df.columns:
            name = str(col).lower()
            if any(k.lower() in name for k in keywords):
                return col
        return ""

    def _safe_number(self, value: Any) -> float:
        if value is None:
            return 0.0
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return 0.0
        m = re.search(r"[-+]?\d*\.?\d+", text)
        if not m:
            return 0.0
        try:
            return float(m.group())
        except Exception:
            return 0.0

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return ""
        return re.sub(r"\s+", " ", text)

    def _parse_date(self, value: str) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None
