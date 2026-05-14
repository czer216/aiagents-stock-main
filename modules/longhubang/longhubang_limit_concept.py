"""
龙虎榜-概念主线数据
主链：Tushare dc_member + dc_index（个股所属概念）
回退：dc_concept_cons / kpl_concept_cons
用于补充“最强概念板块”和“板块轮动”评分因子
"""

from collections import Counter, defaultdict
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from services.data_source_manager import data_source_manager
from infrastructure.external.tushare_proxy_rate_limit import call_tushare_with_timeout


class LimitConceptRotationFetcher:
    """优先基于 Tushare `dc_member + dc_index` 的概念主线数据获取器（失败时回退 `dc/kpl concept_cons`）"""

    def __init__(self):
        self.data_source_manager = data_source_manager

    def get_rotation_data(
        self,
        days: int = 5,
        end_date: Optional[str] = None,
        stock_codes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        获取龙虎榜股票的概念主线强度与匹配映射（优先 dc_member + dc_index）
        """
        result: Dict[str, Any] = {
            "data_success": False,
            "source": "tushare.dc_member/dc_index|dc_concept_cons|kpl_concept_cons",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strongest_today": {},
            "daily_rankings": [],
            "rotation_summary": {},
            "concept_strength_map": {},
            "stock_concept_map": {},
            "concept_field": "",
        }

        if not self.data_source_manager.tushare_available or self.data_source_manager.tushare_api is None:
            result["error"] = "Tushare不可用或未配置Token"
            return result

        pro = self.data_source_manager.tushare_api
        concept_source = self._choose_concept_source(pro)
        if not concept_source:
            result["error"] = "当前Tushare权限或版本不支持 dc_member/dc_index / dc_concept_cons / kpl_concept_cons"
            return result

        normalized_codes = self._normalize_codes(stock_codes or [])
        if not normalized_codes:
            result["error"] = "未提供可用股票代码，无法构建概念主线"
            return result
        preferred_trade_date = self._to_compact_trade_date(end_date) or datetime.now().strftime("%Y%m%d")
        target_codes_set = set(normalized_codes)

        source_candidates = [concept_source]
        for source in ["dc_member", "dc", "kpl"]:
            if source != concept_source and self._is_source_available(pro, source):
                source_candidates.append(source)

        concept_snapshot_df = self._fetch_dc_concept_snapshot(preferred_trade_date)
        concept_market_strength = {}
        if concept_snapshot_df is not None and not concept_snapshot_df.empty:
            concept_market_strength = self._extract_concept_strength_from_df(
                concept_snapshot_df, source="dc"
            )
        concept_whitelist: Set[str] = set()
        if concept_snapshot_df is not None and not concept_snapshot_df.empty:
            concept_whitelist = self._extract_valid_concepts_from_snapshot(
                concept_snapshot_df, source="dc"
            )

        day_df: Optional[pd.DataFrame] = None
        stock_concept_map: Dict[str, List[str]] = {}
        stock_hot_map: Dict[str, Dict[str, float]] = {}
        concept_field = ""
        for source in source_candidates:
            candidate_df = self._fetch_trade_date_snapshot(preferred_trade_date, source)
            if candidate_df is None or candidate_df.empty:
                continue

            if source == "dc_member":
                concept_code_name_map = self._fetch_dc_index_name_map(preferred_trade_date)
                candidate_map, candidate_hot = self._extract_target_mapping_from_dc_member_df(
                    candidate_df,
                    target_codes_set,
                    concept_code_name_map=concept_code_name_map,
                )
                if not candidate_map:
                    continue
                day_df = candidate_df
                concept_source = source
                stock_concept_map = candidate_map
                stock_hot_map = candidate_hot
                concept_field = "dc_index.name"
                break

            candidate_map, candidate_hot = self._extract_target_mapping_with_hot_from_df(
                candidate_df, target_codes_set, source=source
            )
            if source == "dc" and concept_whitelist:
                candidate_map, candidate_hot = self._filter_mapping_by_concept_whitelist(
                    candidate_map, candidate_hot, concept_whitelist
                )
            if not candidate_map:
                continue
            day_df = candidate_df
            concept_source = source
            stock_concept_map = candidate_map
            stock_hot_map = candidate_hot
            schema = self._resolve_schema(day_df, source=concept_source)
            if isinstance(schema, dict):
                fields = schema.get("concept_cols", []) or []
                concept_field = str(fields[0]) if fields else ""
            break

        concept_hot_num_total: Dict[str, float] = defaultdict(float)
        concept_stock_hits: Counter = Counter()
        for code, concepts in stock_concept_map.items():
            concept_hot_row = stock_hot_map.get(code, {})
            for concept in concepts:
                concept_stock_hits[concept] += 1
                concept_value = float(concept_hot_row.get(concept, 0.0) or 0.0)
                if concept_value <= 0:
                    concept_value = float(concept_market_strength.get(concept, 0.0) or 0.0)
                if concept_value <= 0:
                    concept_value = 1.0
                concept_hot_num_total[concept] += concept_value

        if not stock_concept_map:
            result["error"] = f"未从可用概念接口获取到有效映射（已尝试: {', '.join(source_candidates)}）"
            return result

        top_concepts = self._build_top_concepts(
            concept_hot_num_total=concept_hot_num_total,
            concept_stock_hits=concept_stock_hits,
        )
        if not top_concepts:
            result["error"] = "概念映射为空，无法构建主线"
            return result

        ref_date = self._fmt_trade_date(preferred_trade_date or end_date)
        daily_rankings = [
            {
                "trade_date": ref_date,
                "top_concepts": top_concepts[:10],
                "strongest_concept": top_concepts[0]["concept"],
                "strongest_count": top_concepts[0]["stock_count"],
                "strongest_hot_num": top_concepts[0]["hot_num_total"],
            }
        ]

        strength_map = self._build_strength_map_from_hot(
            concept_hot_num_total=concept_hot_num_total,
            concept_stock_hits=concept_stock_hits,
        )
        rotation_summary = self._build_rotation_summary(
            concept_stock_hits=concept_stock_hits,
            concept_hot_num_total=concept_hot_num_total,
            strength_map=strength_map,
            tracked_stocks=len(stock_concept_map),
            requested_stocks=len(normalized_codes),
            days=days,
            concept_source=concept_source,
        )

        result.update(
            {
                "data_success": True,
                "strongest_today": {
                    "trade_date": ref_date,
                    "concept": top_concepts[0]["concept"],
                    "limit_up_count": top_concepts[0]["stock_count"],
                    "stock_count": top_concepts[0]["stock_count"],
                    "hot_num_total": top_concepts[0]["hot_num_total"],
                    "hot_num_avg": top_concepts[0]["hot_num_avg"],
                },
                "daily_rankings": daily_rankings,
                "rotation_summary": rotation_summary,
                "concept_strength_map": strength_map,
                "stock_concept_map": stock_concept_map,
                "effective_trade_date": ref_date,
                "board_top10": top_concepts[:10],
                "concept_source": concept_source,
                "concept_field": concept_field,
                "concept_market_strength_top": [
                    {"concept": k, "score": v}
                    for k, v in sorted(
                        (concept_market_strength or {}).items(), key=lambda x: x[1], reverse=True
                    )[:10]
                ],
            }
        )
        return result

    def _choose_concept_source(self, pro: Any) -> str:
        if hasattr(pro, "dc_member") and hasattr(pro, "dc_index"):
            return "dc_member"
        if hasattr(pro, "dc_concept_cons"):
            return "dc"
        if hasattr(pro, "kpl_concept_cons"):
            return "kpl"
        return ""

    def _is_source_available(self, pro: Any, source: str) -> bool:
        if source == "dc_member":
            return hasattr(pro, "dc_member") and hasattr(pro, "dc_index")
        if source == "dc":
            return hasattr(pro, "dc_concept_cons")
        if source == "kpl":
            return hasattr(pro, "kpl_concept_cons")
        return False

    def _fetch_trade_date_snapshot(self, trade_date: str, source: str) -> Optional[pd.DataFrame]:
        """按交易日拉取整日快照，后续在内存中按股票代码过滤。"""
        pro = self.data_source_manager.tushare_api
        if source == "dc_member":
            api = getattr(pro, "dc_member", None)
            api_name = "dc_member"
            candidates = [
                {"trade_date": trade_date},
                {"date": trade_date},
                {"start_date": trade_date, "end_date": trade_date},
                {"trade_date": trade_date, "limit": 20000},
                {"date": trade_date, "limit": 20000},
                {"limit": 20000},
                {},
            ]
        elif source == "dc":
            api = getattr(pro, "dc_concept_cons", None)
            api_name = "dc_concept_cons"
            candidates = [
                {"trade_date": trade_date},
                {"date": trade_date},
                {"trade_date": trade_date, "limit": 20000},
                {"date": trade_date, "limit": 20000},
            ]
        else:
            api = getattr(pro, "kpl_concept_cons", None)
            api_name = "kpl_concept_cons"
            candidates = [
                {"trade_date": trade_date},
                {"date": trade_date},
                {"trade_date": trade_date, "limit": 20000},
                {"date": trade_date, "limit": 20000},
            ]
        if api is None:
            return None
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: api(**kwargs),
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

    def _fetch_dc_index_name_map(self, trade_date: str) -> Dict[str, str]:
        """
        拉取 dc_index 并构建 概念代码 -> 概念名称 映射。
        """
        pro = self.data_source_manager.tushare_api
        api = getattr(pro, "dc_index", None)
        if api is None:
            return {}

        candidates = [
            {"trade_date": trade_date},
            {"date": trade_date},
            {"trade_date": trade_date, "limit": 5000},
            {"date": trade_date, "limit": 5000},
            {"limit": 5000},
            {},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: api(**kwargs),
                    timeout_sec=60,
                    api_name="dc_index",
                )
            except TypeError:
                continue
            except TimeoutError:
                continue
            except Exception:
                continue
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            code_col = self._find_col(df, ["ts_code", "code", "index_code", "cpt_code", "概念代码"])
            name_col = self._find_col(df, ["name", "concept_name", "cpt_name", "概念名称", "概念"])
            if not code_col or not name_col:
                continue
            out: Dict[str, str] = {}
            for _, row in df.iterrows():
                code = self._normalize_concept_code_key(row.get(code_col))
                name = self._clean_text(row.get(name_col))
                if code and name and not self._is_noise_concept(name):
                    out[code] = name
            if out:
                return out
        return {}

    def _fetch_dc_concept_snapshot(self, trade_date: str) -> Optional[pd.DataFrame]:
        """补充获取概念层热度快照（dc_concept）。"""
        pro = self.data_source_manager.tushare_api
        api = getattr(pro, "dc_concept", None)
        if api is None:
            return None
        candidates = [
            {"trade_date": trade_date},
            {"date": trade_date},
            {"trade_date": trade_date, "limit": 5000},
            {"date": trade_date, "limit": 5000},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: api(**kwargs),
                    timeout_sec=60,
                    api_name="dc_concept",
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

    def _fetch_bulk_data(self) -> Optional[pd.DataFrame]:
        """兜底拉取全量，命中后再按股票过滤"""
        pro = self.data_source_manager.tushare_api
        candidates = [
            {"limit": 10000},
            {"limit": 6000},
            {},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: pro.kpl_concept_cons(**kwargs),
                    timeout_sec=60,
                    api_name="kpl_concept_cons",
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

    def _extract_target_mapping_with_hot_from_df(
        self, df: Optional[pd.DataFrame], target_codes: Set[str], source: str = ""
    ) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, float]]]:
        """
        从整日快照中提取目标股票的概念映射与hot_num映射。
        返回:
          - stock_concept_map: {code: [concepts]}
          - stock_hot_map: {code: {concept: hot_num}}
        """
        if df is None or df.empty or not target_codes:
            return {}, {}

        schema = self._resolve_schema(df, source=source)
        code_cols = schema.get("stock_code_cols", [])
        concept_cols = schema.get("concept_cols", [])
        trade_date_col = schema.get("trade_date_col", "")
        hot_num_col = schema.get("hot_num_col", "")
        stock_name_col = schema.get("stock_name_col", "")
        if not concept_cols or not code_cols:
            return {}, {}

        per_code_date_concepts: Dict[str, Dict[str, List[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        per_code_date_hot: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        for _, row in df.iterrows():
            row_codes = [self._normalize_code(row.get(col)) for col in code_cols]
            row_codes = [x for x in row_codes if x and x in target_codes]
            if not row_codes:
                continue
            concepts = self._extract_row_concepts(row, concept_cols)
            if stock_name_col:
                stock_name = self._clean_text(row.get(stock_name_col))
                if stock_name:
                    concepts = [c for c in concepts if c != stock_name]
            if not concepts:
                continue
            trade_key = ""
            if trade_date_col:
                trade_key = self._normalize_trade_date_key(row.get(trade_date_col))
            hot_num = self._extract_row_hot_num(row, hot_num_col)
            for code in row_codes:
                per_code_date_concepts[code][trade_key].extend(concepts)
                current_hot = per_code_date_hot[code][trade_key]
                for concept in concepts:
                    old = float(current_hot.get(concept, 0.0) or 0.0)
                    if hot_num > old:
                        current_hot[concept] = hot_num

        stock_concept_map: Dict[str, List[str]] = {}
        stock_hot_map: Dict[str, Dict[str, float]] = {}
        for code, date_map in per_code_date_concepts.items():
            if not date_map:
                continue
            valid_dates = sorted([x for x in date_map.keys() if x])
            latest_trade_key = valid_dates[-1] if valid_dates else ""
            concepts = self._dedup_concepts(date_map.get(latest_trade_key, []))
            if not concepts:
                continue
            stock_concept_map[code] = concepts
            hot_map = per_code_date_hot.get(code, {}).get(latest_trade_key, {})
            stock_hot_map[code] = {
                concept: float(hot_map.get(concept, 0.0) or 0.0)
                for concept in concepts
            }
        return stock_concept_map, stock_hot_map

    def _extract_target_mapping_from_dc_member_df(
        self,
        df: Optional[pd.DataFrame],
        target_codes: Set[str],
        concept_code_name_map: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, float]]]:
        """
        从 dc_member 快照提取目标股票的概念映射。
        """
        if df is None or df.empty or not target_codes:
            return {}, {}

        concept_code_name_map = concept_code_name_map or {}
        stock_code_cols = self._filter_stock_code_cols(
            df,
            self._find_cols(
                df,
                [
                    "con_code",
                    "stock_code",
                    "stock_ts_code",
                    "股票代码",
                    "证券代码",
                    "code",
                    "ts_code",
                ],
            ),
        )
        if not stock_code_cols:
            stock_code_cols = self._filter_stock_code_cols(df, self._find_stock_code_cols(df))
        if not stock_code_cols:
            return {}, {}

        concept_name_cols = self._find_concept_cols(df, source="dc_member")
        stock_name_cols = [
            col
            for col in self._find_cols(
                df, ["stock_name", "sec_name", "con_name", "gpmc", "证券简称", "股票名称"]
            )
            if col not in concept_name_cols
        ]
        concept_code_cols = [
            col
            for col in self._find_cols(df, ["cpt_code", "concept_code", "index_code", "dc_code", "ts_code"])
            if col not in stock_code_cols
        ]
        trade_date_col = self._find_col(df, ["trade_date", "date", "日期"])

        per_code_date_concepts: Dict[str, Dict[str, List[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for _, row in df.iterrows():
            row_codes = [self._normalize_code(row.get(col)) for col in stock_code_cols]
            row_codes = [x for x in row_codes if x and x in target_codes]
            if not row_codes:
                continue

            row_concepts: List[str] = []
            if concept_name_cols:
                row_concepts.extend(self._extract_row_concepts(row, concept_name_cols))
            if stock_name_cols and row_concepts:
                stock_names = {
                    self._clean_text(row.get(col))
                    for col in stock_name_cols
                    if self._clean_text(row.get(col))
                }
                if stock_names:
                    row_concepts = [x for x in row_concepts if x not in stock_names]

            if not row_concepts and concept_code_cols and concept_code_name_map:
                for col in concept_code_cols:
                    key = self._normalize_concept_code_key(row.get(col))
                    if not key:
                        continue
                    concept_name = self._clean_text(concept_code_name_map.get(key))
                    if concept_name and not self._is_noise_concept(concept_name):
                        row_concepts.append(concept_name)

            row_concepts = self._dedup_concepts(row_concepts)
            if not row_concepts:
                continue

            trade_key = self._normalize_trade_date_key(row.get(trade_date_col)) if trade_date_col else ""
            for code in row_codes:
                per_code_date_concepts[code][trade_key].extend(row_concepts)

        stock_concept_map: Dict[str, List[str]] = {}
        stock_hot_map: Dict[str, Dict[str, float]] = {}
        for code, date_map in per_code_date_concepts.items():
            valid_dates = sorted([x for x in date_map.keys() if x])
            latest_trade_key = valid_dates[-1] if valid_dates else ""
            concepts = self._dedup_concepts(date_map.get(latest_trade_key, []))
            if not concepts:
                continue
            stock_concept_map[code] = concepts
            stock_hot_map[code] = {concept: 0.0 for concept in concepts}
        return stock_concept_map, stock_hot_map

    def _fetch_single_stock_data(
        self, code: str, preferred_trade_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """按单股回退拉取，兼容不同参数签名"""
        pro = self.data_source_manager.tushare_api
        ts_code = self._to_ts_code(code)
        trade_dates: List[str] = []
        preferred = self._to_compact_trade_date(preferred_trade_date)
        if preferred:
            trade_dates.append(preferred)
        if not trade_dates:
            trade_dates = [datetime.now().strftime("%Y%m%d")]

        candidates: List[Dict[str, Any]] = []
        for day in trade_dates:
            candidates.extend(
                [
                    {"trade_date": day, "con_code": ts_code},
                    {"trade_date": day, "con_code": code},
                ]
            )

        # 按用户要求仅查询当天，不再做无日期回退
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: pro.kpl_concept_cons(**kwargs),
                    timeout_sec=60,
                    api_name="kpl_concept_cons",
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

    def _extract_target_mapping_from_df(
        self, df: pd.DataFrame, target_codes: Set[str]
    ) -> Dict[str, List[str]]:
        if df is None or df.empty or not target_codes:
            return {}

        schema = self._resolve_schema(df)
        code_cols = schema.get("stock_code_cols", [])
        concept_cols = schema.get("concept_cols", [])
        trade_date_col = schema.get("trade_date_col", "")
        if not concept_cols or not code_cols:
            return {}

        per_code_date_map: Dict[str, Dict[str, List[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for _, row in df.iterrows():
            row_codes = [self._normalize_code(row.get(col)) for col in code_cols]
            row_codes = [x for x in row_codes if x and x in target_codes]
            if not row_codes:
                continue
            concepts = self._extract_row_concepts(row, concept_cols)
            if not concepts:
                continue
            trade_key = ""
            if trade_date_col:
                trade_key = self._normalize_trade_date_key(row.get(trade_date_col))
            for code in row_codes:
                per_code_date_map[code][trade_key].extend(concepts)

        out: Dict[str, List[str]] = {}
        for code, date_map in per_code_date_map.items():
            if not date_map:
                continue
            valid_dates = sorted([d for d in date_map.keys() if d])
            if valid_dates:
                latest = valid_dates[-1]
                out[code] = self._dedup_concepts(date_map.get(latest, []))
            else:
                merged: List[str] = []
                for values in date_map.values():
                    merged.extend(values)
                out[code] = self._dedup_concepts(merged)
        return {code: values for code, values in out.items() if values}

    def _extract_single_stock_concepts(self, df: Optional[pd.DataFrame], code: str) -> List[str]:
        """兼容旧调用：只返回概念列表"""
        concepts, _ = self._extract_single_stock_concepts_with_hot(df, code)
        return concepts

    def _extract_single_stock_concepts_with_hot(
        self, df: Optional[pd.DataFrame], code: str
    ) -> Tuple[List[str], Dict[str, float]]:
        """单股回退解析：返回概念列表与该股概念hot_num映射"""
        if df is None or df.empty:
            return [], {}

        mapped = self._extract_target_mapping_from_df(df, {code})
        schema = self._resolve_schema(df)
        concept_cols = schema.get("concept_cols", [])
        hot_num_col = schema.get("hot_num_col", "")
        if not concept_cols:
            return mapped.get(code, []), {}

        # 防止单股接口参数未生效返回全量，导致把全市场名称当概念灌入
        code_cols = schema.get("stock_code_cols", [])
        if code_cols:
            symbol_mask = pd.Series(False, index=df.index)
            for col in code_cols:
                symbol_mask = symbol_mask | df[col].apply(
                    lambda value: self._normalize_code(value) == code
                )
            filtered = df[symbol_mask].copy()
            if filtered.empty:
                return mapped.get(code, []), {}
            df = filtered
        elif len(df) > 30:
            # 无股票代码列且返回行数过大，按高风险结果处理
            return mapped.get(code, []), {}

        trade_date_col = schema.get("trade_date_col", "")
        if trade_date_col and trade_date_col in df.columns:
            trade_keys = df[trade_date_col].apply(self._normalize_trade_date_key)
            valid_dates = sorted([x for x in trade_keys.tolist() if x])
            if valid_dates:
                df = df[trade_keys == valid_dates[-1]]

        concepts: List[str] = []
        concept_hot_map: Dict[str, float] = defaultdict(float)
        for _, row in df.iterrows():
            row_concepts = self._extract_row_concepts(row, concept_cols)
            if not row_concepts:
                continue
            concepts.extend(row_concepts)
            hot_num = self._extract_row_hot_num(row, hot_num_col)
            for concept in row_concepts:
                if hot_num > concept_hot_map[concept]:
                    concept_hot_map[concept] = hot_num

        dedup_concepts = self._dedup_concepts(concepts)
        if mapped.get(code):
            merged = self._dedup_concepts(mapped.get(code, []) + dedup_concepts)
        else:
            merged = dedup_concepts
        filtered_hot = {
            concept: float(concept_hot_map.get(concept, 0.0) or 0.0)
            for concept in merged
        }
        return merged, filtered_hot

    def _extract_row_concepts(self, row: pd.Series, concept_cols: List[str]) -> List[str]:
        concepts: List[str] = []
        for col in concept_cols:
            concepts.extend(self._split_concepts(row.get(col)))
        return self._dedup_concepts(concepts)

    def _extract_row_hot_num(self, row: pd.Series, hot_num_col: str) -> float:
        """优先读取 hot_num 字段，缺失时返回0"""
        if hot_num_col and hot_num_col in row.index:
            value = self._to_float(row.get(hot_num_col))
            return max(value, 0.0)
        return 0.0

    def _extract_concept_strength_from_df(
        self, df: Optional[pd.DataFrame], source: str = ""
    ) -> Dict[str, float]:
        """从概念快照中提取概念强度（优先hot_num，缺失时仅使用成交额，不使用涨跌幅参与评分）。"""
        if df is None or df.empty:
            return {}
        concept_cols = self._find_concept_cols(df, source=source)
        if not concept_cols:
            return {}
        concept_col = concept_cols[0]
        hot_col = self._find_hot_num_col(df)
        amount_col = self._find_col(df, ["amount", "成交额", "turnover"])

        out: Dict[str, float] = {}
        for _, row in df.iterrows():
            concept = self._clean_text(row.get(concept_col))
            if not concept or self._is_noise_concept(concept):
                continue
            hot_val = self._to_float(row.get(hot_col)) if hot_col else 0.0
            amount_val = self._to_float(row.get(amount_col)) if amount_col else 0.0
            score = max(hot_val, 0.0)
            if score <= 0:
                score += min(max(amount_val, 0.0) / 1e8, 10.0)
            prev = float(out.get(concept, 0.0) or 0.0)
            if score > prev:
                out[concept] = round(score, 3)
        return out

    def _extract_valid_concepts_from_snapshot(
        self, df: Optional[pd.DataFrame], source: str = ""
    ) -> Set[str]:
        """
        从概念快照中提取有效概念名称集合，作为白名单过滤成分接口异常值。
        """
        if df is None or df.empty:
            return set()
        concept_cols = self._find_concept_cols(df, source=source)
        if not concept_cols:
            return set()
        out: Set[str] = set()
        for _, row in df.iterrows():
            for col in concept_cols:
                text = self._clean_text(row.get(col))
                if not text:
                    continue
                for concept in self._split_concepts(text):
                    if not concept or self._is_noise_concept(concept):
                        continue
                    out.add(concept)
        return out

    def _filter_mapping_by_concept_whitelist(
        self,
        stock_concept_map: Dict[str, List[str]],
        stock_hot_map: Dict[str, Dict[str, float]],
        whitelist: Set[str],
    ) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, float]]]:
        if not stock_concept_map or not whitelist:
            return stock_concept_map, stock_hot_map
        out_map: Dict[str, List[str]] = {}
        out_hot: Dict[str, Dict[str, float]] = {}
        for code, concepts in (stock_concept_map or {}).items():
            valid = [c for c in (concepts or []) if c in whitelist]
            valid = self._dedup_concepts(valid)
            if not valid:
                continue
            out_map[code] = valid
            old_hot = (stock_hot_map or {}).get(code, {}) or {}
            out_hot[code] = {c: float(old_hot.get(c, 0.0) or 0.0) for c in valid}
        return out_map, out_hot

    def _build_top_concepts(
        self,
        concept_hot_num_total: Dict[str, float],
        concept_stock_hits: Counter,
    ) -> List[Dict[str, Any]]:
        concepts = set(list((concept_hot_num_total or {}).keys()) + list((concept_stock_hits or {}).keys()))
        if not concepts:
            return []

        rows: List[Dict[str, Any]] = []
        for concept in concepts:
            stock_count = int(concept_stock_hits.get(concept, 0) or 0)
            hot_total = float(concept_hot_num_total.get(concept, 0.0) or 0.0)
            hot_avg = hot_total / stock_count if stock_count > 0 else 0.0
            rows.append(
                {
                    "concept": concept,
                    "limit_up_count": stock_count,
                    "stock_count": stock_count,
                    "hot_num_total": round(hot_total, 2),
                    "hot_num_avg": round(hot_avg, 2),
                }
            )
        rows.sort(key=lambda x: (x.get("hot_num_total", 0), x.get("stock_count", 0)), reverse=True)
        return rows[:10]

    def _build_strength_map_from_hot(
        self,
        concept_hot_num_total: Dict[str, float],
        concept_stock_hits: Counter,
    ) -> Dict[str, float]:
        concepts = set(list((concept_hot_num_total or {}).keys()) + list((concept_stock_hits or {}).keys()))
        if not concepts:
            return {}

        max_hot = max([float(concept_hot_num_total.get(c, 0.0) or 0.0) for c in concepts] + [0.0])
        max_hit = max([int(concept_stock_hits.get(c, 0) or 0) for c in concepts] + [1])
        out: Dict[str, float] = {}
        ranked = sorted(
            concepts,
            key=lambda c: (
                float(concept_hot_num_total.get(c, 0.0) or 0.0),
                int(concept_stock_hits.get(c, 0) or 0),
            ),
            reverse=True,
        )[:10]
        for idx, concept in enumerate(ranked):
            hot_total = float(concept_hot_num_total.get(concept, 0.0) or 0.0)
            hit_count = int(concept_stock_hits.get(concept, 0) or 0)
            hot_weight = 0.0 if max_hot <= 0 else min((hot_total / max_hot) * 2.8, 2.8)
            hit_weight = min((hit_count / max(max_hit, 1)) * 1.2, 1.2)
            base = hot_weight + hit_weight
            rank_bonus = max(0.5, 1.0 - idx * 0.06)
            score = min(base + rank_bonus, 4.0)
            out[concept] = round(score, 2)
        return out

    def _build_rotation_summary(
        self,
        concept_stock_hits: Counter,
        concept_hot_num_total: Dict[str, float],
        strength_map: Dict[str, float],
        tracked_stocks: int,
        requested_stocks: int,
        days: int,
        concept_source: str = "",
    ) -> Dict[str, Any]:
        all_concepts = set(list((concept_stock_hits or {}).keys()) + list((concept_hot_num_total or {}).keys()))
        if not all_concepts:
            return {}

        total_hits = sum(int(concept_stock_hits.get(c, 0) or 0) for c in all_concepts) or 1
        total_hot = sum(float(concept_hot_num_total.get(c, 0.0) or 0.0) for c in all_concepts) or 1.0
        sorted_by_hot = sorted(
            all_concepts,
            key=lambda c: (
                float(concept_hot_num_total.get(c, 0.0) or 0.0),
                int(concept_stock_hits.get(c, 0) or 0),
            ),
            reverse=True,
        )
        top_concept = sorted_by_hot[0]
        top_count = int(concept_stock_hits.get(top_concept, 0) or 0)
        top_hot = float(concept_hot_num_total.get(top_concept, 0.0) or 0.0)
        top_ratio = top_hot / total_hot if total_hot > 0 else top_count / total_hits
        active_concepts = len(all_concepts)

        if top_ratio >= 0.35:
            flow_signal = "主线集中，资金偏抱团"
        elif active_concepts >= 20:
            flow_signal = "题材分散，轮动较快"
        else:
            flow_signal = "结构轮动，主线与支线并行"

        hot_strength = [f"{name}({score})" for name, score in list(strength_map.items())[:5]]
        return {
            "days": max(int(days), 1),
            "active_concepts": active_concepts,
            "rotation_ratio": round(1 - top_ratio, 2),
            "tracked_stocks": int(tracked_stocks),
            "requested_stocks": int(requested_stocks),
            "coverage_ratio": round(tracked_stocks / max(requested_stocks, 1), 2),
            "concept_source": concept_source or "unknown",
            "leading_concepts": [
                {
                    "concept": concept,
                    "days_as_top": int(concept_stock_hits.get(concept, 0) or 0),
                    "hot_num_total": round(float(concept_hot_num_total.get(concept, 0.0) or 0.0), 2),
                }
                for concept in sorted_by_hot[:5]
            ],
            "hot_strength_top5": hot_strength,
            "capital_flow_signal": flow_signal,
            "strongest_concept": top_concept,
            "strongest_count": int(top_count),
            "strongest_hot_num": round(top_hot, 2),
            "hot_num_total": round(total_hot, 2),
        }

    def _merge_mapping(self, target: Dict[str, List[str]], incoming: Dict[str, List[str]]) -> None:
        for code, concepts in (incoming or {}).items():
            old = target.get(code, [])
            merged = old + concepts
            target[code] = self._dedup_concepts(merged)

    def _dedup_concepts(self, concepts: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for concept in concepts:
            clean = self._clean_text(concept)
            if not clean:
                continue
            if self._is_noise_concept(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out[:30]

    def _split_concepts(self, value: Any) -> List[str]:
        text = self._clean_text(value)
        if not text:
            return []
        parts = re.split(r"[,，;；/|、\s]+", text)
        out: List[str] = []
        for part in parts:
            concept = self._clean_text(part)
            if concept:
                out.append(concept)
        return out

    def _find_cols(self, df: pd.DataFrame, keywords: List[str]) -> List[str]:
        out: List[str] = []
        for col in df.columns:
            name = str(col).lower()
            if any(k.lower() in name for k in keywords):
                out.append(col)
        return out

    def _find_col(self, df: pd.DataFrame, keywords: List[str]) -> str:
        cols = self._find_cols(df, keywords)
        return cols[0] if cols else ""

    def _find_stock_code_cols(self, df: pd.DataFrame) -> List[str]:
        """识别股票代码列（排除概念代码等）"""
        out: List[str] = []
        for col in df.columns:
            name = str(col).lower()
            if (
                name == "ts_code"
                or name == "stock_code"
                or name == "股票代码"
                or name == "证券代码"
                or name == "symbol"
                or name == "con_code"
                or "ts_code" in name
                or "stock_code" in name
            ):
                out.append(col)
                continue
            if name == "code":
                out.append(col)
                continue
            if "code" in name:
                # 明确排除概念/板块代码列
                if any(
                    bad in name
                    for bad in ["cpt_code", "concept_code", "block_code", "bk_code"]
                ):
                    continue
                if any(good in name for good in ["stock", "sec", "证券", "股票"]):
                    out.append(col)
        return out

    def _find_concept_cols(self, df: pd.DataFrame, source: str = "") -> List[str]:
        """识别概念名称列，避免把股票名称列误当概念"""
        columns = {str(col).lower(): col for col in df.columns}
        if source == "dc_member":
            if "cpt_name" in columns:
                return [columns["cpt_name"]]
            if "concept_name" in columns:
                return [columns["concept_name"]]
            # dc_member 中 name 语义不稳定，优先依赖 cpt_name/concept_name，
            # 无明确概念名称列时交给概念代码(cpt_code/ts_code)+dc_index 反查，避免把股票名称误当概念。
            return []
        if source == "kpl" and {"con_code", "name", "con_name"}.issubset(columns.keys()):
            return [columns["name"]]

        if source == "dc":
            if "cpt_name" in columns:
                return [columns["cpt_name"]]
            if "concept_name" in columns:
                return [columns["concept_name"]]
            # dc_concept_cons 常见形态下，name更可能是概念名，con_name更可能是个股名
            if "name" in columns:
                return [columns["name"]]
            if "con_name" in columns:
                return [columns["con_name"]]

        if {"con_code", "name", "con_name"}.issubset(columns.keys()):
            chosen = self._pick_low_cardinality_col(df, columns["name"], columns["con_name"])
            return [chosen] if chosen else [columns["name"]]

        out: List[str] = []
        for col in df.columns:
            name = str(col).lower()
            if any(
                k in name
                for k in [
                    "con_name",
                    "cpt_name",
                    "concept_name",
                    "concept",
                    "概念",
                    "题材",
                    "板块",
                ]
            ):
                out.append(col)

        # 某些接口版本只给 name，这里谨慎兜底：仅在没有明显股票名称列时才启用
        if not out:
            has_stock_name_col = any(
                any(k in str(col).lower() for k in ["stock_name", "sec_name", "股票名称", "证券简称", "gpmc"])
                for col in df.columns
            )
            if not has_stock_name_col:
                for col in df.columns:
                    if str(col).lower() == "name":
                        out.append(col)
        return out

    def _pick_low_cardinality_col(self, df: pd.DataFrame, col_a: str, col_b: str) -> str:
        """在两个候选列中选择更像概念列（唯一值占比更低）。"""
        if not col_a:
            return col_b
        if not col_b:
            return col_a
        total = max(len(df), 1)
        try:
            uniq_a = int(df[col_a].astype(str).str.strip().replace("nan", "").nunique())
            uniq_b = int(df[col_b].astype(str).str.strip().replace("nan", "").nunique())
        except Exception:
            return col_a
        ratio_a = uniq_a / total
        ratio_b = uniq_b / total
        return col_a if ratio_a <= ratio_b else col_b

    def _filter_stock_code_cols(self, df: pd.DataFrame, cols: List[str]) -> List[str]:
        """过滤掉不像股票代码的列（至少命中一个可归一化6位代码）。"""
        out: List[str] = []
        for col in cols:
            valid = False
            series = df[col] if col in df.columns else []
            sample_count = 0
            for value in series:
                text = self._clean_text(value)
                if not text:
                    continue
                sample_count += 1
                if self._normalize_code(text):
                    valid = True
                    break
                if sample_count >= 80:
                    break
            if valid:
                out.append(col)
        return out

    def _find_hot_num_col(self, df: pd.DataFrame) -> str:
        """识别概念热度值列（hot_num）。"""
        preferred = [
            "hot_num",
            "hotnum",
            "hot_number",
            "热度值",
            "热度",
            "heat_num",
            "heat",
        ]
        for col in df.columns:
            name = str(col).lower()
            if name in preferred:
                return col
        for col in df.columns:
            name = str(col).lower()
            if "hot" in name and "num" in name:
                return col
        return ""

    def _resolve_schema(self, df: pd.DataFrame, source: str = "") -> Dict[str, Any]:
        """
        识别概念成分接口字段形态。
        常见形态：
        - dc_member: con_code(股票代码) + cpt_code/ts_code(概念代码) + cpt_name/name(概念名)
        - kpl_concept_cons: ts_code(概念代码) / name(概念名) / con_name(股票名) / con_code(股票代码)
        - dc_concept_cons: ts_code(股票代码) + con_name/name/cpt_name(概念名，视版本而定)
        """
        columns = {str(col).lower(): col for col in df.columns}
        if source == "dc_member":
            stock_cols = self._filter_stock_code_cols(
                df,
                [x for x in [columns.get("con_code"), columns.get("stock_code"), columns.get("code")] if x],
            )
            concept_cols = self._find_concept_cols(df, source="dc_member")
            return {
                "stock_code_cols": stock_cols,
                "concept_cols": concept_cols,
                "stock_name_col": "",
                "trade_date_col": columns.get("trade_date", ""),
                "hot_num_col": "",
            }

        if source == "kpl" and {"con_code", "name", "con_name"}.issubset(columns.keys()):
            return {
                "stock_code_cols": self._filter_stock_code_cols(df, [columns["con_code"]]),
                "concept_cols": [columns["name"]],
                "stock_name_col": columns.get("con_name", ""),
                "trade_date_col": columns.get("trade_date", ""),
                "hot_num_col": columns.get("hot_num", ""),
            }

        if source == "dc":
            stock_cols = self._filter_stock_code_cols(
                df,
                [x for x in [columns.get("ts_code"), columns.get("stock_code"), columns.get("code")] if x],
            )
            concept_cols = self._find_concept_cols(df, source="dc")
            stock_name_col = ""
            if "con_name" in columns and (not concept_cols or columns["con_name"] not in concept_cols):
                stock_name_col = columns["con_name"]
            return {
                "stock_code_cols": stock_cols,
                "concept_cols": concept_cols,
                "stock_name_col": stock_name_col,
                "trade_date_col": columns.get("trade_date", ""),
                "hot_num_col": self._find_hot_num_col(df),
            }

        if {"con_code", "name", "con_name"}.issubset(columns.keys()):
            stock_cols = self._filter_stock_code_cols(df, [columns["con_code"]])
            concept_col = self._pick_low_cardinality_col(df, columns["name"], columns["con_name"])
            return {
                "stock_code_cols": stock_cols,
                "concept_cols": [concept_col] if concept_col else [columns["name"]],
                "stock_name_col": columns.get("con_name", ""),
                "trade_date_col": columns.get("trade_date", ""),
                "hot_num_col": columns.get("hot_num", ""),
            }

        stock_cols = self._filter_stock_code_cols(df, self._find_stock_code_cols(df))
        concept_cols = self._find_concept_cols(df, source=source)
        stock_name_col = ""
        fallback_stock_name_col = self._find_col(df, ["stock_name", "sec_name", "股票名称", "证券简称", "gpmc", "con_name"])
        if fallback_stock_name_col and fallback_stock_name_col not in concept_cols:
            stock_name_col = fallback_stock_name_col
        return {
            "stock_code_cols": stock_cols,
            "concept_cols": concept_cols,
            "stock_name_col": stock_name_col,
            "trade_date_col": columns.get("trade_date", ""),
            "hot_num_col": self._find_hot_num_col(df),
        }

    def _normalize_codes(self, codes: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in codes:
            code = self._normalize_code(item)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def _normalize_code(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip().upper()
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if m:
            return m.group(1)
        return text if len(text) == 6 and text.isdigit() else ""

    def _normalize_concept_code_key(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip().upper()
        if not text:
            return ""
        text = text.replace(" ", "")
        # 概念代码可能带后缀，统一按原值和去后缀值做映射
        if "." in text:
            base = text.split(".", 1)[0]
            return base or text
        return text

    def _normalize_trade_date_key(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip().replace("-", "").replace("/", "").replace(".", "")
        if re.fullmatch(r"\d{8}", text):
            return text
        return ""

    def _to_compact_trade_date(self, trade_date: Optional[str]) -> str:
        if not trade_date:
            return ""
        text = str(trade_date).strip().replace("-", "").replace("/", "").replace(".", "")
        return text if re.fullmatch(r"\d{8}", text) else ""

    def _to_ts_code(self, code: str) -> str:
        if not code or len(code) != 6 or not code.isdigit():
            return code
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith("0") or code.startswith("3"):
            return f"{code}.SZ"
        if code.startswith("8") or code.startswith("4"):
            return f"{code}.BJ"
        return f"{code}.SZ"

    def _to_float(self, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            text = str(value).strip().replace(",", "")
            return float(text) if text else 0.0
        except Exception:
            return 0.0

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        text = re.sub(r"\s+", " ", text)
        return "" if text.lower() == "nan" else text

    def _is_noise_concept(self, concept: str) -> bool:
        if not concept:
            return True
        noise = {"概念", "板块", "题材", "-", "--", "N/A", "None", "nan"}
        if concept in noise:
            return True
        if concept.isdigit():
            return True
        return len(concept) < 2

    def _fmt_trade_date(self, trade_date: Optional[str]) -> str:
        if not trade_date:
            return datetime.now().strftime("%Y-%m-%d")
        text = str(trade_date).strip()
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if re.fullmatch(r"\d{4}/\d{2}/\d{2}", text):
            return text.replace("/", "-")
        return datetime.now().strftime("%Y-%m-%d")
