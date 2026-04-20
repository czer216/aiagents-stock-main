"""
龙虎榜短窗K线趋势信号（Tushare daily）
按交易日快照拉取后在内存匹配股票，避免逐股高频请求。
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout
from trade_calendar_service import TradeCalendarService


class SevenDayKlineTrendFetcher:
    """基于 Tushare daily 的短窗趋势阶段识别（默认9日）"""

    def __init__(self):
        self.data_source_manager = data_source_manager
        self.trade_calendar = TradeCalendarService()

    def get_trend_data(
        self,
        days: int = 9,
        end_date: Optional[str] = None,
        stock_codes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        window_days = max(min(int(days or 9), 9), 5)
        result: Dict[str, Any] = {
            "data_success": False,
            "source": "tushare.daily",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window_days": window_days,
            "trade_dates": [],
            "stock_trend_map": {},
            "trend_summary": {},
            "top_strength": [],
        }

        pro = self.data_source_manager.tushare_api
        if not self.data_source_manager.tushare_available or pro is None:
            result["error"] = "Tushare不可用或未配置Token"
            return result
        if not hasattr(pro, "daily"):
            result["error"] = "当前Tushare权限或版本不支持 daily"
            return result

        target_codes = self._normalize_codes(stock_codes or [])
        if not target_codes:
            result["error"] = "未提供龙虎榜股票代码，跳过K线趋势阶段识别"
            return result
        target_set = set(target_codes)

        trade_dates = self._build_recent_trade_dates(days=window_days, end_date=end_date)
        result["trade_dates"] = [self._fmt_trade_date(x) for x in trade_dates]
        if not trade_dates:
            result["error"] = "未生成有效交易日"
            return result

        per_stock: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for trade_date in trade_dates:
            day_df = self._fetch_trade_date_snapshot(trade_date)
            if day_df is None or day_df.empty:
                continue
            rows = self._extract_daily_rows(day_df, target_set)
            for row in rows:
                per_stock[row["code"]].append(row)

        stock_trend_map: Dict[str, Dict[str, Any]] = {}
        for code in target_codes:
            items = per_stock.get(code, [])
            if not items:
                continue
            detail = self._calc_stage_detail(items)
            if not detail:
                continue
            stock_trend_map[code] = detail

        if not stock_trend_map:
            result["error"] = "未获取到有效的K线趋势数据"
            return result

        stage_counter = Counter(str(v.get("trend_stage", "")) for v in stock_trend_map.values())
        top_strength = sorted(
            [
                {
                    "code": code,
                    "name": detail.get("name", ""),
                    "trend_stage": detail.get("trend_stage", ""),
                    "trend_score": float(detail.get("trend_score", 0.0) or 0.0),
                    "change_7d_pct": float(detail.get("change_7d_pct", 0.0) or 0.0),
                    "up_streak": int(detail.get("up_streak", 0) or 0),
                }
                for code, detail in stock_trend_map.items()
            ],
            key=lambda x: (x["trend_score"], x["change_7d_pct"], x["up_streak"]),
            reverse=True,
        )[:10]

        result.update(
            {
                "data_success": True,
                "stock_trend_map": stock_trend_map,
                "top_strength": top_strength,
                "trend_summary": {
                    "tracked_stocks": len(stock_trend_map),
                    "window_days": window_days,
                    "stage_distribution": {k: int(v) for k, v in stage_counter.items() if k},
                    "accelerating_count": int(stage_counter.get("加速期", 0)),
                    "start_count": int(stage_counter.get("启动期", 0)),
                    "high_shake_count": int(stage_counter.get("高位震荡", 0)),
                    "decline_count": int(stage_counter.get("退潮期", 0)),
                },
            }
        )
        return result

    def _fetch_trade_date_snapshot(self, trade_date: str) -> Optional[pd.DataFrame]:
        pro = self.data_source_manager.tushare_api
        candidates = [
            {"trade_date": trade_date, "fields": "ts_code,trade_date,open,high,low,close,pct_chg,vol"},
            {"trade_date": trade_date},
            {"date": trade_date, "fields": "ts_code,trade_date,open,high,low,close,pct_chg,vol"},
            {"date": trade_date},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: pro.daily(**kwargs),
                    timeout_sec=60,
                    api_name="daily",
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

    def _extract_daily_rows(self, df: pd.DataFrame, target_set: set) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "code", "股票代码", "证券代码"])
        if not code_col:
            return []
        date_col = self._find_col(df, ["trade_date", "date", "日期"])
        close_col = self._find_col(df, ["close", "收盘"])
        open_col = self._find_col(df, ["open", "开盘"])
        high_col = self._find_col(df, ["high", "最高"])
        low_col = self._find_col(df, ["low", "最低"])
        pct_col = self._find_col(df, ["pct_chg", "pct_change", "涨跌幅", "pct"])
        vol_col = self._find_col(df, ["vol", "volume", "成交量"])
        name_col = self._find_col(df, ["name", "股票名称", "简称"])

        out: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_code(row.get(code_col))
            if not code or code not in target_set:
                continue
            close_val = self._safe_number(row.get(close_col)) if close_col else 0.0
            if close_val <= 0:
                continue
            out.append(
                {
                    "code": code,
                    "trade_date": self._norm_trade_date(row.get(date_col)) if date_col else "",
                    "open": float(self._safe_number(row.get(open_col))) if open_col else 0.0,
                    "high": float(self._safe_number(row.get(high_col))) if high_col else 0.0,
                    "low": float(self._safe_number(row.get(low_col))) if low_col else 0.0,
                    "close": float(close_val),
                    "pct_chg": float(self._safe_number(row.get(pct_col))) if pct_col else 0.0,
                    "vol": float(self._safe_number(row.get(vol_col))) if vol_col else 0.0,
                    "name": self._clean_text(row.get(name_col)) if name_col else "",
                }
            )

        dedup: Dict[str, Dict[str, Any]] = {}
        for item in out:
            code = item["code"]
            old = dedup.get(code)
            if old is None:
                dedup[code] = item
                continue
            old_date = old.get("trade_date", "")
            new_date = item.get("trade_date", "")
            if new_date >= old_date:
                dedup[code] = item
        return list(dedup.values())

    def _calc_stage_detail(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {}
        rows = sorted(items, key=lambda x: x.get("trade_date", ""))
        closes = [float(x.get("close", 0.0) or 0.0) for x in rows if float(x.get("close", 0.0) or 0.0) > 0]
        if len(closes) < 2:
            return {}

        returns: List[float] = []
        for idx in range(1, len(closes)):
            prev = closes[idx - 1]
            curr = closes[idx]
            if prev > 0:
                returns.append((curr - prev) / prev * 100.0)

        latest_close = closes[-1]
        latest_open = float(rows[-1].get("open", 0.0) or 0.0)
        latest_high = float(rows[-1].get("high", 0.0) or 0.0)
        latest_low = float(rows[-1].get("low", 0.0) or 0.0)
        first_close = closes[0]
        low_close = min(closes)
        total_change = (latest_close - first_close) / first_close * 100.0 if first_close > 0 else 0.0
        latest_change = float(rows[-1].get("pct_chg", 0.0) or 0.0)
        if abs(latest_change) < 1e-8 and returns:
            latest_change = float(returns[-1])

        up_days = sum(1 for r in returns if r > 0)
        down_days = sum(1 for r in returns if r < 0)

        up_streak = 0
        for r in reversed(returns):
            if r > 0:
                up_streak += 1
            else:
                break

        max_close = max(closes)
        drawdown = (latest_close - max_close) / max_close * 100.0 if max_close > 0 else 0.0

        vols = [float(x.get("vol", 0.0) or 0.0) for x in rows if float(x.get("vol", 0.0) or 0.0) > 0]
        vol_ratio = 0.0
        if len(vols) >= 2:
            prev_avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
            if prev_avg > 0:
                vol_ratio = vols[-1] / prev_avg

        # K线形态：用于高位+放量长上影风险识别
        upper_shadow = max(latest_high - max(latest_open, latest_close), 0.0)
        lower_shadow = max(min(latest_open, latest_close) - latest_low, 0.0)
        body = abs(latest_close - latest_open)
        day_range = max(latest_high - latest_low, 1e-6)
        upper_shadow_ratio = upper_shadow / day_range
        body_ratio = body / day_range
        latest_near_high = 0.0
        if latest_high > 0:
            latest_near_high = (latest_close - latest_low) / max(day_range, 1e-6)

        stage = "震荡期"
        reason = "近窗内涨跌交替，趋势不够连续"
        if total_change <= -4 or (up_days <= 2 and latest_change <= -1.0):
            stage = "退潮期"
            reason = "近窗累计涨幅弱且下跌日偏多"
        elif total_change >= 14 and up_days >= 5:
            if up_streak >= 2:
                stage = "加速期"
                reason = "近窗累计涨幅高且保持连阳"
            else:
                stage = "高位震荡"
                reason = "近窗累计涨幅高，但短线出现高位分歧"
        elif total_change >= 8 and up_days >= 4:
            if up_streak >= 2:
                stage = "加速期"
                reason = "近窗趋势持续上行，资金推动明显"
            else:
                stage = "高位震荡"
                reason = "已有明显涨幅，近期分歧增大"
        elif total_change >= 3 and up_days >= 3:
            stage = "启动期"
            reason = "近窗开始走强，但尚未进入高斜率阶段"

        trend_score_map = {
            "加速期": 1.0,
            "启动期": 0.72,
            "高位震荡": 0.58,
            "震荡期": 0.45,
            "退潮期": 0.2,
        }

        return {
            "trend_stage": stage,
            "trend_reason": reason,
            "trend_score": round(float(trend_score_map.get(stage, 0.45)), 3),
            "change_7d_pct": round(float(total_change), 2),
            "latest_change_pct": round(float(latest_change), 2),
            "up_days": int(up_days),
            "down_days": int(down_days),
            "up_streak": int(up_streak),
            "drawdown_from_high_pct": round(float(drawdown), 2),
            "vol_ratio": round(float(vol_ratio), 2),
            "latest_close": round(float(latest_close), 3),
            "high_7d": round(float(max_close), 3),
            "low_7d": round(float(low_close), 3),
            "latest_open": round(float(latest_open), 3),
            "latest_high": round(float(latest_high), 3),
            "latest_low": round(float(latest_low), 3),
            "latest_upper_shadow_ratio": round(float(upper_shadow_ratio), 3),
            "latest_body_ratio": round(float(body_ratio), 3),
            "latest_near_high_ratio": round(float(latest_near_high), 3),
            "latest_upper_shadow_pct": round(float(upper_shadow / max(latest_close, 1e-6) * 100.0), 2),
            "latest_trade_date": self._fmt_trade_date(rows[-1].get("trade_date", "")),
            "name": rows[-1].get("name", ""),
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

    def _build_recent_trade_dates(self, days: int, end_date: Optional[str] = None) -> List[str]:
        end8 = self._norm_trade_date(end_date) if end_date else datetime.now().strftime("%Y%m%d")
        return self.trade_calendar.recent_open_days(end8, max(int(days or 0), 1))

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
        return "" if text.lower() == "nan" else re.sub(r"\s+", " ", text)

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

    def _norm_trade_date(self, value: Any) -> str:
        text = self._clean_text(value)
        parsed = self._parse_date(text)
        if parsed:
            return parsed.strftime("%Y%m%d")
        m = re.search(r"(\d{8})", text)
        return m.group(1) if m else ""

    def _fmt_trade_date(self, trade_date: str) -> str:
        if not trade_date or len(trade_date) != 8:
            return trade_date
        return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
