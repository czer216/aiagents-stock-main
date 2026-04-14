"""
股票扩展数据获取模块
1. 个股概念板块（Tushare `kpl_concept_cons`）
2. 同花顺历史异动（问财检索）
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import re

import pandas as pd
import pywencai
from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout


class THSFeatureDataFetcher:
    """扩展数据获取器（概念: Tushare；异动: pywencai）"""

    def __init__(self):
        self.data_source_manager = data_source_manager

    def get_context_data(self, symbol: str, query_date: Optional[str] = None) -> Dict[str, Any]:
        """
        获取个股概念板块 + 同花顺历史异动

        Args:
            symbol: A股代码（6位）
            query_date: 查询日期（前端指定，支持 YYYY-MM-DD / YYYYMMDD）

        Returns:
            dict: 结构化上下文数据
        """
        normalized_query_date = self._normalize_query_date(query_date)
        result = {
            "symbol": symbol,
            "source": "concept:tushare.kpl_concept_cons;abnormal:pywencai",
            "data_success": False,
            "concept_data": {"has_data": False, "concepts": [], "count": 0},
            "abnormal_data": {"has_data": False, "records": [], "summary": {}},
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "query_trade_date": normalized_query_date,
        }

        if not self._is_chinese_stock(symbol):
            result["error"] = "仅支持A股"
            return result

        # 1) 概念板块
        concept_data = self._get_concept_data(symbol, query_date=normalized_query_date)
        if concept_data:
            result["concept_data"] = concept_data

        # 2) 同花顺历史异动
        abnormal_data = self._get_abnormal_data(symbol)
        if abnormal_data:
            result["abnormal_data"] = abnormal_data

        result["data_success"] = bool(
            result["concept_data"].get("has_data")
            or result["abnormal_data"].get("has_data")
        )
        return result

    def format_context_for_ai(self, data: Dict[str, Any]) -> str:
        """格式化为 AI 可读文本"""
        if not data:
            return "未获取到同花顺扩展数据。"

        lines: List[str] = []
        concept_data = data.get("concept_data", {})
        abnormal_data = data.get("abnormal_data", {})

        lines.append("【扩展数据（概念: Tushare / 异动: 问财）】")
        lines.append(f"- 查询时间: {data.get('query_time', 'N/A')}")

        if concept_data.get("has_data"):
            concepts = concept_data.get("concepts", [])
            lines.append(f"- 概念板块({len(concepts)}个): {'、'.join(concepts[:12])}")
        else:
            lines.append("- 概念板块: 未获取到")

        if abnormal_data.get("has_data"):
            summary = abnormal_data.get("summary", {})
            top_types = summary.get("top_types", [])
            lines.append(
                f"- 历史异动: 共{summary.get('total_records', 0)}条，"
                f"最近日期 {summary.get('latest_date', '未知')}"
            )
            if top_types:
                lines.append(f"- 高频异动类型: {'、'.join(top_types[:5])}")
        else:
            lines.append("- 历史异动: 未获取到")

        records = abnormal_data.get("records", [])[:8]
        if records:
            lines.append("\n【历史异动样本】")
            for idx, rec in enumerate(records, 1):
                lines.append(
                    f"{idx}. {rec.get('日期', '未知')} | "
                    f"{rec.get('标签', rec.get('异动类型', '异动'))} | "
                    f"{rec.get('标题', rec.get('异动类型', '异动'))}"
                )
                if rec.get("原因"):
                    lines.append(f"   原因: {rec.get('原因')}")

        return "\n".join(lines)

    def _get_concept_data(self, symbol: str, query_date: Optional[str] = None) -> Dict[str, Any]:
        """使用 Tushare `kpl_concept_cons` 获取个股概念板块"""
        query_name = "tushare.kpl_concept_cons"
        pro = self.data_source_manager.tushare_api
        if not self.data_source_manager.tushare_available or pro is None:
            return {
                "has_data": False,
                "query": query_name,
                "count": 0,
                "concepts": [],
                "error": "Tushare不可用或未配置Token",
            }
        if not hasattr(pro, "kpl_concept_cons"):
            return {
                "has_data": False,
                "query": query_name,
                "count": 0,
                "concepts": [],
                "error": "当前Tushare权限或版本不支持 kpl_concept_cons",
            }

        ts_code = self._to_ts_code(symbol)
        df = self._fetch_kpl_concept_cons(symbol=symbol, ts_code=ts_code, query_date=query_date)
        concepts = self._extract_kpl_concepts(df, symbol=symbol, ts_code=ts_code)
        if concepts:
            return {
                "has_data": True,
                "query": query_name,
                "count": len(concepts),
                "concepts": concepts[:30],
                "stock_code": ts_code,
                "raw_rows": int(len(df)) if df is not None else 0,
            }
        return {
            "has_data": False,
            "query": query_name,
            "count": 0,
            "concepts": [],
            "stock_code": ts_code,
        }

    def _fetch_kpl_concept_cons(
        self, symbol: str, ts_code: str, query_date: Optional[str] = None
    ) -> pd.DataFrame:
        """多参数尝试调用 kpl_concept_cons，兼容不同版本参数命名"""
        pro = self.data_source_manager.tushare_api
        if pro is None:
            return pd.DataFrame()

        target_trade_date = self._normalize_query_date(query_date) or datetime.now().strftime("%Y%m%d")
        recent_trade_dates: List[str] = [target_trade_date]

        candidates: List[Dict[str, Any]] = []
        for day in recent_trade_dates:
            candidates.extend(
                [
                    {"trade_date": day, "con_code": ts_code},
                    {"trade_date": day, "con_code": symbol},
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
        return pd.DataFrame()

    def _normalize_query_date(self, query_date: Optional[str]) -> str:
        if not query_date:
            return ""
        text = str(query_date).strip().replace("/", "-").replace(".", "-")
        if re.fullmatch(r"\d{8}", text):
            return text
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text.replace("-", "")
        return ""

    def _extract_kpl_concepts(
        self, df: pd.DataFrame, symbol: str, ts_code: str
    ) -> List[str]:
        """从 kpl_concept_cons 结果中提取目标股票概念"""
        if df is None or df.empty:
            return []

        exact_symbol = symbol
        schema = self._resolve_kpl_schema(df)
        code_cols = schema.get("stock_code_cols", [])
        concept_cols = schema.get("concept_cols", [])
        trade_date_col = schema.get("trade_date_col", "")
        if not concept_cols:
            return []

        work_df = df.copy()

        # 仅保留目标股票，避免接口参数未生效时混入全市场数据
        if code_cols:
            symbol_mask = pd.Series(False, index=work_df.index)
            for col in code_cols:
                symbol_mask = symbol_mask | work_df[col].apply(
                    lambda value: self._normalize_stock_code(value) == exact_symbol
                )
            work_df = work_df[symbol_mask]

        if work_df.empty:
            return []

        # 只取最新交易日概念，确保“当前所属概念板块”
        if trade_date_col and trade_date_col in work_df.columns:
            trade_keys = work_df[trade_date_col].apply(self._normalize_trade_date_key)
            valid_dates = sorted([x for x in trade_keys.tolist() if x])
            if valid_dates:
                latest_date = valid_dates[-1]
                work_df = work_df[trade_keys == latest_date]
        elif len(work_df) > 100:
            # 无日期列且行数过大，风险较高，避免返回全量噪音
            return []

        concepts: List[str] = []
        for _, row in work_df.iterrows():
            for col in concept_cols:
                concepts.extend(self._split_to_tokens(row.get(col)))

        # 去重 + 过滤噪音
        deduped: List[str] = []
        seen = set()
        for item in concepts:
            name = item.strip()
            if not self._is_valid_concept(name):
                continue
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped

    def _resolve_kpl_schema(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        解析 kpl_concept_cons 常见字段形态：
        - 当前代理实测形态：
          ts_code(概念代码) / name(概念名) / con_name(股票名) / con_code(股票代码)
        """
        columns = {str(col).lower(): col for col in df.columns}

        # 优先命中实测形态，防止把 con_name(股票名) 当成概念名
        if {"con_code", "name", "con_name"}.issubset(columns.keys()):
            return {
                "stock_code_cols": [columns["con_code"]],
                "concept_cols": [columns["name"]],
                "trade_date_col": columns.get("trade_date", ""),
            }

        code_cols = [
            col
            for col in df.columns
            if any(
                k in str(col).lower()
                for k in ["stock_code", "con_code", "code", "ts_code", "股票代码", "证券代码"]
            )
        ]
        concept_cols = [
            col
            for col in df.columns
            if any(k in str(col).lower() for k in ["concept", "cpt", "题材", "概念", "板块"])
        ]
        if not concept_cols:
            concept_cols = [col for col in df.columns if str(col).lower() in ("name", "concept_name")]

        return {
            "stock_code_cols": code_cols,
            "concept_cols": concept_cols,
            "trade_date_col": columns.get("trade_date", ""),
        }

    def _normalize_trade_date_key(self, value: Any) -> str:
        text = self._safe_text(value)
        if not text:
            return ""
        text = text.replace("-", "").replace("/", "").replace(".", "")
        if re.fullmatch(r"\d{8}", text):
            return text
        return ""

    def _get_abnormal_data(self, symbol: str) -> Dict[str, Any]:
        """获取同花顺历史异动"""
        days_window = 7
        queries = [
            f"{symbol}最近7天同花顺异动解读",
            f"{symbol}最近7日异动解读",
            f"{symbol}最近7天历史异动",
            f"{symbol}同花顺历史异动",
        ]

        for query in queries:
            try:
                response = pywencai.get(query=query, loop=True)
                df = self._convert_to_dataframe(response)
                records = self._extract_abnormal_records(df, symbol=symbol, days=days_window)
                records = [r for r in records if self._is_meaningful_abnormal_record(r)]
                if records:
                    type_counter = Counter(
                        [self._record_type_label(r) for r in records if self._record_type_label(r)]
                    )
                    top_types = [f"{name}({count})" for name, count in type_counter.most_common(8)]
                    latest_date = next(
                        (
                            self._normalize_date_text(r.get("日期")) or "未知"
                            for r in records
                            if self._normalize_date_text(r.get("日期"))
                        ),
                        "未知",
                    )
                    return {
                        "has_data": True,
                        "query": query,
                        "records": records[:30],
                        "summary": {
                            "total_records": len(records),
                            "latest_date": latest_date,
                            "days_window": days_window,
                            "top_types": top_types,
                        },
                    }
            except Exception:
                continue

        return {"has_data": False, "query": queries[0], "records": [], "summary": {}}

    def _convert_to_dataframe(self, result: Any) -> pd.DataFrame:
        """将问财返回结构转换成 DataFrame"""
        if result is None:
            return pd.DataFrame()

        if isinstance(result, pd.DataFrame):
            return result

        if isinstance(result, list):
            try:
                return pd.DataFrame(result)
            except Exception:
                return pd.DataFrame()

        if isinstance(result, dict):
            for key in ["tableV1", "data", "datas", "result", "records"]:
                value = result.get(key)
                if isinstance(value, pd.DataFrame):
                    return value
                if isinstance(value, list):
                    try:
                        return pd.DataFrame(value)
                    except Exception:
                        continue
            try:
                return pd.DataFrame([result])
            except Exception:
                return pd.DataFrame()

        return pd.DataFrame()

    def _extract_concepts(self, df: pd.DataFrame) -> List[str]:
        """从问财结果提取概念列表"""
        if df is None or df.empty:
            return []

        concepts: List[str] = []
        candidate_cols = [
            col for col in df.columns if any(k in str(col) for k in ["概念", "题材"])
        ]

        target_rows = df.head(3)
        for _, row in target_rows.iterrows():
            for col in candidate_cols:
                concepts.extend(self._split_to_tokens(row.get(col)))

        # 兜底：若未命中概念列，扫前两行文本
        if not concepts:
            for _, row in target_rows.iterrows():
                for value in row.values:
                    text = str(value) if value is not None else ""
                    if "概念" in text or "题材" in text:
                        concepts.extend(self._split_to_tokens(text))

        # 去重并过滤噪音
        deduped: List[str] = []
        seen = set()
        for item in concepts:
            name = item.strip()
            if not self._is_valid_concept(name):
                continue
            if name not in seen:
                seen.add(name)
                deduped.append(name)

        return deduped

    def _extract_abnormal_records(
        self, df: pd.DataFrame, symbol: str, days: int = 7
    ) -> List[Dict[str, str]]:
        """从问财结果提取历史异动记录（默认最近7天）"""
        if df is None or df.empty:
            return []

        symbol_cols = [
            col
            for col in df.columns
            if any(k in str(col) for k in ["股票代码", "证券代码", "代码", "symbol", "异动股票"])
        ]
        date_col = self._find_column(df, ["日期", "时间"])
        type_col = self._find_column(df, ["异动类型", "异动", "事件", "类型"])
        tag_col = self._find_column(df, ["标签", "涨跌", "状态"])
        title_col = self._find_column(df, ["标题", "解读标题", "异动解读", "题材", "主题"])
        reason_col = self._find_column(df, ["行业原因", "原因", "触发", "解读", "摘要", "内容", "说明"])
        detail_cols = [
            col
            for col in df.columns
            if any(k in str(col) for k in ["说明", "原因", "内容", "涨跌", "价格", "成交", "幅"])
        ]

        work_df = df.copy()
        if symbol_cols:
            symbol_mask = pd.Series(False, index=work_df.index)
            for col in symbol_cols:
                symbol_mask = symbol_mask | work_df[col].apply(
                    lambda value: self._contains_symbol(value, symbol)
                )
            work_df = work_df[symbol_mask]
        else:
            # 没有显式代码列时，按整行文本兜底匹配，并剔除混入多只股票的汇总行
            work_df = work_df[
                work_df.apply(lambda row: self._row_is_target_symbol(row, symbol), axis=1)
            ]

        if work_df.empty:
            return []

        if date_col and date_col in work_df.columns:
            parsed = pd.to_datetime(work_df[date_col].apply(self._parse_row_datetime), errors="coerce")
            work_df = work_df.assign(_parsed_date=parsed)
        else:
            work_df = work_df.assign(_parsed_date=pd.NaT)

        if work_df["_parsed_date"].isna().all():
            work_df = work_df.assign(
                _parsed_date=pd.to_datetime(
                    work_df.apply(self._extract_datetime_from_row, axis=1), errors="coerce"
                )
            )

        # 仅保留最近 N 天（含今天）
        if not work_df["_parsed_date"].isna().all():
            cutoff_date = datetime.now().date() - timedelta(days=max(days - 1, 0))
            work_df = work_df[work_df["_parsed_date"].dt.date >= cutoff_date]

        if work_df.empty:
            return []

        work_df = work_df.sort_values("_parsed_date", ascending=False)

        records: List[Dict[str, str]] = []
        for _, row in work_df.head(50).iterrows():
            parsed_dt = row.get("_parsed_date")
            if isinstance(parsed_dt, datetime):
                date_value = parsed_dt.strftime("%Y-%m-%d")
            else:
                date_value = self._normalize_date_text(row.get(date_col)) if date_col else "未知"
                if not date_value:
                    date_value = "未知"

            event_type = self._sanitize_text(
                self._safe_text(row.get(type_col)) if type_col else "", max_len=24
            )
            tag_value = self._sanitize_text(
                self._safe_text(row.get(tag_col)) if tag_col else "", max_len=16
            )
            title_value = self._sanitize_text(
                self._safe_text(row.get(title_col)) if title_col else "", max_len=48
            )
            reason_value = self._sanitize_text(
                self._safe_text(row.get(reason_col)) if reason_col else "", max_len=180
            )

            detail_parts = []
            for col in detail_cols[:4]:
                text = self._sanitize_text(self._safe_text(row.get(col)), max_len=90)
                if text and text not in ("-", "nan", "None") and not self._is_noise_blob(text):
                    detail_parts.append(text)

            detail = "；".join(detail_parts)
            if not event_type and detail:
                event_type = "异动"
            if not title_value:
                title_value = event_type or tag_value
            if not reason_value and detail:
                reason_value = detail[:220]

            if self._is_noise_blob(title_value) and self._is_noise_blob(reason_value):
                continue

            # 没有任何信息则跳过
            if not event_type and not tag_value and not title_value and not reason_value and not detail:
                continue

            records.append(
                {
                    "日期": date_value if date_value else "未知",
                    "标签": tag_value,
                    "异动类型": event_type if event_type else "异动",
                    "标题": title_value,
                    "原因": reason_value[:260] if reason_value else "",
                    "详情": detail[:220],
                }
            )

        dedup_records: List[Dict[str, str]] = []
        seen = set()
        for record in records:
            key = (
                record.get("日期", ""),
                record.get("标签", ""),
                record.get("标题", ""),
                record.get("原因", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            dedup_records.append(record)

        return dedup_records

    def _is_meaningful_abnormal_record(self, record: Dict[str, str]) -> bool:
        """判断异动记录是否有足够信息，避免假阳性占位记录"""
        if not record:
            return False

        date_text = (record.get("日期") or "").strip()
        tag_text = (record.get("标签") or "").strip()
        event_type = (record.get("异动类型") or "").strip()
        title_text = (record.get("标题") or "").strip()
        reason_text = (record.get("原因") or "").strip()
        detail_text = (record.get("详情") or "").strip()

        has_substance = bool(tag_text or reason_text or detail_text)
        placeholder_titles = {"异动", "异动解读"}

        # 类似 DataFrame 文本摘要/拼接串，视为噪音
        if self._is_noise_blob(title_text) or self._is_noise_blob(reason_text):
            return False

        # 纯占位（未知日期 + 泛化标题 + 无正文）视为无效
        if date_text in ("", "未知") and title_text in placeholder_titles and not has_substance:
            return False

        # 只有“异动”这类通用词，且没有正文，视为无效
        if event_type in ("", "异动") and title_text in placeholder_titles and not has_substance:
            return False

        return True

    def _split_to_tokens(self, value: Any) -> List[str]:
        """将概念字符串拆分为 token"""
        if value is None:
            return []

        if isinstance(value, (list, tuple, set)):
            tokens: List[str] = []
            for item in value:
                tokens.extend(self._split_to_tokens(item))
            return tokens

        text = str(value).strip()
        if not text:
            return []

        # 常见格式：所属概念: 芯片, AI
        if ":" in text and len(text) > 3:
            left, right = text.split(":", 1)
            if "概念" in left or "题材" in left:
                text = right

        text = text.replace("\n", ",").replace("【", "").replace("】", "")
        pieces = re.split(r"[，,、;；|/]", text)
        return [p.strip() for p in pieces if p and p.strip()]

    def _find_column(self, df: pd.DataFrame, keywords: List[str]) -> str:
        """按关键字查找列名"""
        for col in df.columns:
            name = str(col)
            if any(k in name for k in keywords):
                return col
        return ""

    def _safe_text(self, value: Any) -> str:
        """安全转文本"""
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    def _contains_symbol(self, value: Any, symbol: str) -> bool:
        """判断字段中是否包含目标股票代码"""
        text = self._safe_text(value)
        if not text:
            return False
        codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", text))
        if codes:
            return symbol in codes
        return symbol in text

    def _normalize_stock_code(self, value: Any) -> str:
        """标准化股票代码为6位"""
        text = self._safe_text(value).upper()
        if not text:
            return ""
        if "." in text:
            base = text.split(".", 1)[0]
            if len(base) == 6 and base.isdigit():
                return base
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if match:
            return match.group(1)
        return ""

    def _to_ts_code(self, symbol: str) -> str:
        """将6位股票代码转换为 tushare ts_code"""
        if not symbol or len(symbol) != 6 or not symbol.isdigit():
            return symbol
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        if symbol.startswith("0") or symbol.startswith("3"):
            return f"{symbol}.SZ"
        if symbol.startswith("8") or symbol.startswith("4"):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"

    def _row_is_target_symbol(self, row: pd.Series, symbol: str) -> bool:
        """判断整行是否属于目标股票，并过滤混入多股票的汇总行"""
        parts = [self._safe_text(v) for v in row.values if self._safe_text(v)]
        if not parts:
            return False
        text = " ".join(parts)
        codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", text))
        if not codes:
            return symbol in text
        return symbol in codes and len(codes) == 1

    def _parse_row_datetime(self, value: Any):
        """解析各种日期格式，返回 datetime 或 NaT"""
        text = self._safe_text(value)
        if not text:
            return pd.NaT

        parsed = pd.to_datetime(text, errors="coerce")
        if pd.notna(parsed):
            return parsed.to_pydatetime()

        # 兜底提取日期：YYYY-MM-DD / YYYYMMDD，若有多个取最后一个（通常更接近最新时间）
        matches = re.findall(r"\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}|\d{8}", text)
        if not matches:
            return pd.NaT

        for candidate in reversed(matches):
            normalized = candidate.replace("/", "-").replace(".", "-")
            if re.fullmatch(r"\d{8}", normalized):
                normalized = f"{normalized[0:4]}-{normalized[4:6]}-{normalized[6:8]}"
            parsed = pd.to_datetime(normalized, errors="coerce")
            if pd.notna(parsed):
                return parsed.to_pydatetime()
        return pd.NaT

    def _extract_datetime_from_row(self, row: pd.Series):
        """当无显式日期列时，从整行文本提取日期"""
        text = " ".join([self._safe_text(v) for v in row.values if self._safe_text(v)])
        return self._parse_row_datetime(text)

    def _normalize_date_text(self, value: Any) -> str:
        """日期标准化为 YYYY-MM-DD"""
        parsed = self._parse_row_datetime(value)
        if isinstance(parsed, datetime):
            return parsed.strftime("%Y-%m-%d")
        return ""

    def _sanitize_text(self, text: str, max_len: int = 120) -> str:
        """清洗文本，避免超长噪音内容污染展示"""
        value = self._safe_text(text)
        if not value:
            return ""
        value = re.sub(r"\s+", " ", value).replace("**", "").replace("`", "").strip()
        value = re.sub(r"\[\s*\d+\s*rows?\s*x\s*\d+\s*columns?\s*\]", "", value, flags=re.I).strip()
        if len(value) > max_len:
            value = value[:max_len].rstrip(" ，,;；。") + "..."
        return value

    def _is_noise_blob(self, text: str) -> bool:
        """判断是否为不可读的大段噪音文本"""
        value = self._safe_text(text)
        if not value:
            return False
        if re.search(r"\d+\s*rows?\s*x\s*\d+\s*columns?", value, flags=re.I):
            return True
        codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", value))
        if len(codes) >= 3:
            return True
        return False

    def _record_type_label(self, record: Dict[str, str]) -> str:
        """提取用于摘要统计的短标签"""
        candidates = [
            record.get("标签", ""),
            record.get("异动类型", ""),
            record.get("标题", ""),
        ]
        for item in candidates:
            label = self._sanitize_text(item, max_len=16)
            if not label:
                continue
            if label in {"异动", "异动解读", "未知"}:
                continue
            if self._is_noise_blob(label):
                continue
            return label
        return "异动"

    def _is_chinese_stock(self, symbol: str) -> bool:
        return symbol.isdigit() and len(symbol) == 6

    def _is_valid_concept(self, name: str) -> bool:
        """过滤噪音概念名"""
        if not name:
            return False
        invalid_values = {
            "-", "--", "N/A", "nan", "None", "概念", "题材", "所属概念", "同花顺概念", "暂无"
        }
        if name in invalid_values:
            return False
        if name.isdigit():
            return False
        if len(name) < 2 or len(name) > 30:
            return False
        return True
